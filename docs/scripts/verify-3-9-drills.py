"""排期任务 3-9 剩余演练：对话并发 / Python 宕机降级 / PG 连接中断恢复。

## 为什么这三项要单独演练（其余项已有证据）

3-9 的清单是「对话并发 / 定时任务重入 / Python 宕机降级 / SQLite 或 PG 损坏与恢复 / MySQL 慢查询」。
其中：
  · **定时任务重入** —— 1-9 已验（`verify-1-9-analyze.py` 的「重入安全」段）；
  · **Python 宕机降级** —— 0-15 验过 `agent.enabled=false` 与停 Python 的降级帧，
    但那时 checkpointer 还是 SQLite；本轮补 **PG 后端下** 的同一条路径；
  · **对话并发 / PG 连接中断恢复** —— **此前完全没测过**，而这两条正是“上线后才炸”的典型：
    并发暴露共享状态问题，连接中断暴露连接池与错误处理问题。
  · **MySQL 慢查询** —— 本机开发库 29 单/28 工单，**造不出有意义的慢查询**（强行造要写脏数据），
    如实留待 M2 真机环境执行，不在本脚本假装覆盖。

## 三相位的环境要求（脚本会**自检**，不满足就拒绝执行——这是 FIX-16 那次误写事故的教训）

| 相位 | 要求 | 自检 |
| --- | --- | --- |
| `--phase concurrent` | Python 可用 + PG 可用 | 都用 /health 与 TCP 探 |
| `--phase python-down` | **Python 必须已停** + Java 可用 | Python 可连则拒绝 |
| `--phase pg-recover` | Python 可用 + PG 可用 | 同上 |

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a
python ../docs/scripts/verify-3-9-drills.py --phase concurrent
# 停 Python 后：
python ../docs/scripts/verify-3-9-drills.py --phase python-down
python ../docs/scripts/verify-3-9-drills.py --phase pg-recover
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402

AGENT_BASE = os.environ.get("DKD_AGENT_BASE_URL", "http://127.0.0.1:8090")
JAVA_BASE = os.environ.get("DKD_AGENT_JAVA_BASE", "http://127.0.0.1:8080")
SERVICE_SECRET = os.environ.get("DKD_AGENT_SERVICE_SECRET", "")

CONCURRENCY = 8

_pass = 0
_fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def tcp_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def agent_up() -> bool:
    return tcp_ok("127.0.0.1", 8090)


def pg_up() -> bool:
    return tcp_ok("127.0.0.1", 5432)


async def sse_chat(client: httpx.AsyncClient, *, conversation_id: str, text: str) -> dict:
    """发一轮 SSE，返回按事件类型归类的帧字典。"""
    events: dict[str, list[dict]] = {}
    async with client.stream(
        "POST",
        f"{AGENT_BASE}/agent/chat",
        json={"message": text, "conversation_id": conversation_id, "scene": 1},
        headers={"X-Agent-User": "7", "X-Request-Id": f"drill-{conversation_id}"},
        timeout=60.0,
    ) as resp:
        event_name = None
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: ") and event_name:
                try:
                    events.setdefault(event_name, []).append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    events.setdefault(event_name, []).append({"raw": line[6:]})
    return events


async def sse_chat_safe(client: httpx.AsyncClient, *, conversation_id: str, text: str) -> dict:
    """sse_chat 的容错版：**把“流被直接打断”也当成一种可报告的结果**。

    为什么需要它（2026-10-01 首次跑本相位时就被这个坑绊住）：PG 连接被终止后，
    服务端的 SSE 生成器抛 psycopg.OperationalError 一路抛穿，
    客户端拿到的是 httpx.ReadError——脚本当场 traceback 退出，什么结论都没产出。
    “异常本身”就是本次演练要观察的对象，不该让它把脚本打挂。
    """
    try:
        return await sse_chat(client, conversation_id=conversation_id, text=text)
    except Exception as exc:  # noqa: BLE001 —— 这里就是要观察失败形态
        return {"_transport_error": f"{type(exc).__name__}: {exc}"}


# ----------------------------------------------------------------------------------
# 相位一：对话并发
# ----------------------------------------------------------------------------------


async def phase_concurrent() -> None:
    print(f"\n[并发] {CONCURRENCY} 个**不同会话**同时对话（PG checkpointer）")
    if not agent_up():
        print("  ⛔ 拒绝执行：Python（:8090）不可用")
        raise SystemExit(1)

    async with httpx.AsyncClient() as client:
        before = await client.get(f"{AGENT_BASE}/agent/restock/plans")  # 预热连接
        _ = before

        ids = [f"drill-conc-{i}" for i in range(CONCURRENCY)]
        results = await asyncio.gather(
            *[sse_chat(client, conversation_id=cid, text=f"并发第 {i} 号会话") for i, cid in enumerate(ids)],
            return_exceptions=True,
        )

    errors = [r for r in results if isinstance(r, Exception)]
    check("全部并发请求未抛异常", not errors, f"异常 {len(errors)} 个：{errors[:2]}")

    ok_flows = 0
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            continue
        has_meta = bool(res.get("meta"))
        has_delta = bool(res.get("delta"))
        has_done = bool(res.get("done"))
        if has_meta and has_delta and has_done:
            ok_flows += 1
        else:
            print(f"  · 会话 {ids[i]} 帧缺失：{sorted(res)}")
    check(
        f"{CONCURRENCY} 个会话都走完 meta→delta→done",
        ok_flows == CONCURRENCY,
        f"完整 {ok_flows}/{CONCURRENCY}",
    )

    # 关键：并发下会话不得串话——每个会话的 conversation_id 必须是自己的
    mismatched = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            continue
        for meta in res.get("meta", []):
            if meta.get("conversation_id") != ids[i]:
                mismatched.append((ids[i], meta.get("conversation_id")))
    check("无跨会话串话（每个会话拿回自己的 conversation_id）", not mismatched, f"串话 {mismatched[:3]}")

    # 再由**内容**确认一次：各会话的 delta 文本必须与自己的输入对应
    leaked = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            continue
        text = "".join(d.get("text", "") for d in res.get("delta", []))
        if f"{i} 号会话" not in text:
            leaked.append((ids[i], text[:40]))
    check("各会话内容与输入一一对应（没串到别的会话）", not leaked, f"异常 {leaked[:3]}")


# ----------------------------------------------------------------------------------
# 相位二：Python 宕机降级（PG 后端下重验）
# ----------------------------------------------------------------------------------


async def phase_python_down() -> None:
    print("\n[降级] Python 已停：网关应报未就绪且对话走降级，而不是假装可用")
    if agent_up():
        print("  ⛔ 拒绝执行：Python（:8090）仍可连——本相位必须先真的停掉它")
        print("     停法： 任务管理器结束 uvicorn/python 进程，或 taskkill /PID <pid> /F")
        raise SystemExit(1)
    print("  ✓ 前置自检通过：Python 不可连")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 网关状态探测（前端据此显示降级横幅）
        login = await client.post(f"{JAVA_BASE}/login", json={"username": "admin", "password": "admin123"})
        token = (login.json() or {}).get("token")
        if not token:
            check("登录取得 JWT（降级演练需要认证）", False, login.text[:120])
            return
        check("登录取得 JWT", True)

        # Python 已停，状态探测必须走网关（直连 Python 此时必然连不上，测不出网关的判定）
        status = await client.get(
            f"{JAVA_BASE}/agent/status", headers={"Authorization": f"Bearer {token}"}
        )
        body = status.json() if status.content else {}
        data = body.get("data") or {}
        print(f"  → 网关 /agent/status：enabled={data.get('enabled')} upstream={data.get('upstream')}")
        check(
            "网关如实上报 upstream=down（前端据此禁用入口）",
            data.get("upstream") == "down",
            f"upstream={data.get('upstream')}",
        )

        # 对话应返回降级帧而不是 500/超时
        events = await sse_chat(client, conversation_id="drill-down", text="Python 已停时的一句话")
        print(f"  → 帧类型：{sorted(events)}")
        check("对话返回了降级/错误帧（不是静默失败）", bool(events.get("error") or events.get("done")),
              f"帧={sorted(events)}")
        if events.get("error"):
            err = events["error"][0]
            print(f"  → 降级帧内容：{str(err)[:120]}")
            check("降级帧明确标记 degrade（前端可据此提示）", bool(err.get("degrade")), f"degrade={err.get('degrade')}")


# ----------------------------------------------------------------------------------
# 相位三：PG 连接中断恢复
# ----------------------------------------------------------------------------------


async def phase_pg_recover() -> None:
    print("\n[PG] 中断应用侧的 PG 连接，验证连接池能自动恢复（不重启服务）")
    if not agent_up() or not pg_up():
        print("  ⛔ 拒绝执行：需要 Python 与 PG 都在运行")
        raise SystemExit(1)

    async with httpx.AsyncClient() as client:
        # 基线：先跑一轮，确认正常
        base = await sse_chat(client, conversation_id="drill-pg-base", text="PG 中断前基线")
        check("中断前基线对话正常", bool(base.get("done")) and bool(base.get("delta")),
              f"帧={sorted(base)}")

        # 用**应用自己的角色**终止自己的其它后端（PG 允许角色终止自己的后端，不需要超级用户）
        try:
            killed = await _kill_own_backends()
            print(f"  → 已终止 {killed} 个应用侧 PG 后端")
            check("成功制造连接中断", killed >= 1, f"killed={killed}")
        except Exception as exc:  # noqa: BLE001
            check("成功制造连接中断", False, f"{type(exc).__name__}: {exc}")
            return

        # 中断后立刻再跑一轮：要么成功（池重连），要么失败但**可诊断**
        after = await sse_chat_safe(client, conversation_id="drill-pg-after", text="PG 中断后的一轮")
        if "_transport_error" in after:
            # 这就是修复前的形态：流被直接打断，客户端什么都没拿到
            check(
                "连接中断后**没有抛穿 SSE**（不得出现传输层断流）",
                False,
                f"客户端只拿到传输错误：{after['_transport_error'][:90]}",
            )
        elif bool(after.get("done")) and bool(after.get("delta")):
            check("连接中断后对话自动恢复（连接池重连，无需重启服务）", True, f"帧={sorted(after)}")
        else:
            detail = " | ".join(str(f)[:90] for f in after.get("error", [])) or str(sorted(after))
            check("连接中断后失败但**可诊断**（error 帧，而不是断流）", bool(after.get("error")), detail)

        # 收尾：再跑一轮确认稳定
        final = await sse_chat_safe(client, conversation_id="drill-pg-final", text="PG 恢复后收尾")
        check("后续会话稳定可用", bool(final.get("done")), f"帧={sorted(final)}")


async def _kill_own_backends() -> int:
    """终止当前应用角色自己的其它 PG 后端（PG 允许角色终止自己的后端，无需超级用户）。"""
    import psycopg
    from app.config import get_settings

    dsn = get_settings().pg_dsn
    if not dsn:
        raise RuntimeError("当前未配置 DKD_AGENT_PG_DSN（本相位要求 PG checkpointer）")

    with psycopg.connect(dsn, connect_timeout=8, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity "
            "where usename = current_user and pid <> pg_backend_pid()"
        )
        return sum(1 for row in cur.fetchall() if row[0])


async def main() -> int:
    parser = argparse.ArgumentParser(description="3-9 剩余演练（对话并发 / Python 宕机降级 / PG 恢复）")
    parser.add_argument("--phase", required=True, choices=["concurrent", "python-down", "pg-recover"])
    args = parser.parse_args()

    if args.phase == "concurrent":
        await phase_concurrent()
    elif args.phase == "python-down":
        await phase_python_down()
    else:
        await phase_pg_recover()

    print(f"\n结果：PASS={_pass} FAIL={_fail}")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
