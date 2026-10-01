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

## 各相位的环境要求（脚本会**自检**，不满足就拒绝执行——这是 FIX-16 那次误写事故的教训）

| 相位 | 要求 | 自检 |
| --- | --- | --- |
| `--phase concurrent` | Python 可用 + PG 可用 | 都用 /health 与 TCP 探 |
| `--phase python-down` | **Python 必须已停** + Java 可用 | Python 可连则拒绝；加 `--auto-manage-service` 则由脚本自己停→验→起（默认关闭，不擅自停别人的进程） |
| `--phase pg-recover` | Python 可用 + PG 可用 | 同上 |
| `--phase sqlite-corrupt` | **无**（自建临时 SQLite 文件，不碰服务） | 无需自检；不需要 PG，也不需要服务在跑 |
| `--phase slow-query` | MySQL 可用（只读） | MySQL 不可连则拒绝；会打印业务表行数说明数据量 |

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a
python ../docs/scripts/verify-3-9-drills.py --phase concurrent
# 停 Python 后（人工停），或让脚本代劳：
python ../docs/scripts/verify-3-9-drills.py --phase python-down
python ../docs/scripts/verify-3-9-drills.py --phase python-down --auto-manage-service
python ../docs/scripts/verify-3-9-drills.py --phase pg-recover
# 与上面几相无关，可随时跑：
python ../docs/scripts/verify-3-9-drills.py --phase sqlite-corrupt
python ../docs/scripts/verify-3-9-drills.py --phase slow-query
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402

AGENT_PORT = 8090  # 与 AGENT_BASE 同源（本机默认）
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


# ----------------------------------------------------------------------------------
# 「Python 宕机降级」相的服务接管（--auto-manage-service）
# ----------------------------------------------------------------------------------
#
# 这一相原本要求人工先把 Python 停掉，实测有两个麻烦：
#   ① 忘了停 → 脚本拒跑（护栏是对的，但每次都要人工切一顿）；
#   ② 停完忘了起 → 环境留在半停状态，后续相位全部失败。
# 所以加一个**显式开关**：`--auto-manage-service` 时由脚本自己停、验、再起。
# 默认关闭（不自动动别人的进程），需要时显式给。
#
# ⚠️ 重启**必须用本仓入口 `python -m app`**（见 FIX-20：uvicorn 0.36+ 在 Windows 上
#    默认给 ProactorEventLoop，psycopg 异步不支持；`python -m app` 内部已把 loop 钉住）。
#    这条写在代码里而不是注释里——重启命令写错会让整台环境静默变成另一种形态。
_AGENT_ROOT_FOR_RESTART = Path(__file__).resolve().parents[2] / "dkd-agent"


def _windows_pids_on_port(port: int) -> list[int]:
    """取监听该端口的 PID 列表（Windows：netstat -ano 解析）。"""
    out = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, timeout=20
    ).stdout
    pids: list[int] = []
    for line in out.splitlines():
        if f":{port}" not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pids.append(int(parts[-1]))
    return sorted(set(pids))


def _process_cmdline(pid: int) -> str:
    """取进程命令行（用于把“我们停了什么”如实打印出来）。"""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=25,
        ).stdout.strip()
        return out
    except Exception:  # noqa: BLE001 —— 拿不到命令行不影响停服务，但要如实说
        return "<无法读取命令行>"


async def stop_python_service() -> dict:
    """停掉监听 :8090 的进程，返回 {stopped: bool, pids: [...], cmdline: str}。"""
    pids = _windows_pids_on_port(AGENT_PORT)
    if not pids:
        return {"stopped": False, "pids": [], "cmdline": ""}
    cmdline = _process_cmdline(pids[0])
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=20)
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        if not _windows_pids_on_port(AGENT_PORT):
            return {"stopped": True, "pids": pids, "cmdline": cmdline}
        await asyncio.sleep(0.5)
    return {"stopped": False, "pids": pids, "cmdline": cmdline, "timeout": True}


async def start_python_service(timeout_s: float = 90) -> dict:
    """用本仓入口重启 Python（`python -m app`），等 /health 变绿。"""
    log_path = Path(tempfile.gettempdir()) / "dkd-agent-pyphase.log"
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(  # noqa: S603 —— 参数是固定列表，不是 shell 拼接
            [sys.executable, "-m", "app"],
            cwd=str(_AGENT_ROOT_FOR_RESTART),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if tcp_ok("127.0.0.1", AGENT_PORT):
            try:
                async with httpx.AsyncClient(timeout=5.0) as probe:
                    resp = await probe.get(f"{AGENT_BASE}/health")
                if resp.status_code == 200:
                    return {"started": True, "pid": proc.pid, "log": str(log_path)}
            except Exception:  # noqa: BLE001 —— 还没起完，继续等
                pass
        if proc.poll() is not None:
            return {
                "started": False,
                "pid": proc.pid,
                "log": str(log_path),
                "exit_code": proc.returncode,
            }
        await asyncio.sleep(1.0)
    return {"started": False, "pid": proc.pid, "log": str(log_path), "timeout": True}


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


async def sse_chat_via_gateway(client: httpx.AsyncClient, *, token: str, text: str) -> dict:
    """经 **Java 网关**发一轮 SSE（Python 已停时必须走这条：直连 Python 只会 ConnectError）。

    为什么要单独一个函数（而不是复用 `sse_chat`）：`sse_chat` 直连 Python :8090，
    而本相位的**前提就是它已经停了**——第一版就是这么写的，结果一调对话就
    `httpx.ConnectError` 把脚本打挂，前两项断言过了、真正要验的降级帧反而没验到。
    网关在 Python 不可达时会合成降级帧（`event: error` + `degrade=true`，
    随后 `event: done` + `finish_reason=degrade`），这才是要观察的对象。
    """
    events: dict[str, list[dict]] = {}
    async with client.stream(
        "POST",
        f"{JAVA_BASE}/agent/chat",
        json={"message": text, "scene": 1},
        headers={"Authorization": f"Bearer {token}", "X-Request-Id": "drill-python-down"},
        timeout=30.0,
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

    # 再由**内容**确认一次——但断言必须是**模式相关**的（2026-10-01 修）：
    #
    # 本相位最初是在 `DKD_AGENT_USE_LLM=0`（echo 回显）时写的，那时“回复=原样回显输入”，
    # 所以“回复必须含自己的输入标记”很容易成立。后来本机切到真模型，同一个断言就**必然失败**：
    # 模型对「并发第 3 号会话」的回答是一句正常的问候/澄清，不会复读输入。
    # 那是**演练假设过期**，不是产品串话——若不区分，就会把“真模型在正常工作”报成并发缺陷。
    #
    # 因此：
    #   · echo 模式：回复必须含**自己**的标记（能验到“没丢内容”）；
    #   · llm 模式：回复**不得含别人的**标记（能验到“没串到别会话的历史”，且不依赖模型复读）。
    modes = {str((res.get("meta") or [{}])[0].get("mode") or "?") for res in results if not isinstance(res, Exception)}
    print(f"  → 服务端模式：{sorted(modes)}")
    leaked: list = []
    missing_self: list = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            continue
        text = "".join(d.get("text", "") for d in res.get("delta", []))
        others = [f"{j} 号会话" for j in range(CONCURRENCY) if j != i]
        if any(mark in text for mark in others):
            leaked.append((ids[i], text[:60]))
        if f"{i} 号会话" not in text:
            missing_self.append(ids[i])
    check(
        "无跨会话内容串话（回复里不得出现别的会话的输入标记）",
        not leaked,
        f"串话 {leaked[:3]}",
    )
    if "echo" in modes:
        check(
            "echo 模式：每个会话都回显了自己的输入（内容未丢失）",
            not missing_self,
            f"未回显自己的：{missing_self[:3]}",
        )
    else:
        info = (
            "llm 模式：模型不会复读输入，因此“必须含自己的标记”这条不适用"
            "（在 echo 模式下验证；已跑到这条时请把 DKD_AGENT_USE_LLM 置 0 重跑）"
        )
        print(f"  · {info}")


# ----------------------------------------------------------------------------------
# 相位二：Python 宕机降级（PG 后端下重验）
# ----------------------------------------------------------------------------------


async def phase_python_down(*, auto_manage: bool = False) -> None:
    """Python 已停 → 网关应如实报未就绪、对话走降级。

    @param auto_manage 为 True 时由脚本**自己停 → 验 → 再起**（`--auto-manage-service`）。
                       默认 False：不擅自停别人的进程，仍要求人工先停（护栏不变）。
    """
    print("\n[降级] Python 已停：网关应报未就绪且对话走降级，而不是假装可用")
    if agent_up():
        if not auto_manage:
            print("  ⛔ 拒绝执行：Python（:8090）仍可连——本相位必须先真的停掉它")
            print("     两种做法：① 人工停：netstat -ano | findstr :8090 拿 PID → taskkill /PID <pid> /F")
            print("               ② 让脚本代劳：加 --auto-manage-service（脚本验完会自己重启）")
            raise SystemExit(1)
        print("  → auto-manage：正在停掉 :8090 的服务…")
        stopped_info = await stop_python_service()
        if not stopped_info.get("stopped"):
            print(f"  ⛔ 停服务失败：{stopped_info}（请人工停后重跑）")
            raise SystemExit(1)
        print(f"  → 已停止 pid={stopped_info['pids']}（{stopped_info['cmdline'][:60]}）")
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

        # 对话应返回降级帧而不是断流/超时。**必须经 Java 网关**：Python 已停，
        # 直连 :8090 只会 ConnectError（本相位第一版就是这么把自己的脚本打挂的）。
        events = await sse_chat_via_gateway(client, token=token, text="Python 已停时的一句话")
        print(f"  → 帧类型：{sorted(events)}")
        check("经网关的对话返回了降级/错误帧（不是静默失败）",
              bool(events.get("error") or events.get("done")), f"帧={sorted(events)}")
        if events.get("error"):
            err = events["error"][0]
            print(f"  → 降级帧内容：{str(err)[:120]}")
            check("降级帧明确标记 degrade（前端可据此提示）",
                  bool(err.get("degrade")), f"degrade={err.get('degrade')}")
            check("降级文案指向智能体服务（而不是含糊的“操作失败”）",
                  "智能体服务" in str(err.get("msg") or ""), f"msg={str(err.get('msg'))[:60]}")
        done = (events.get("done") or [{}])[0]
        check("降级收尾帧 finish_reason=degrade（前端据此收掉流式态）",
              done.get("finish_reason") == "degrade", f"done={done}")

    # 收尾：把服务起回来（**不留半停环境**——人工停完最容易忘的就是这一步）
    if auto_manage:
        print("  → auto-manage：正在重启 Python（必须用 `python -m app`，见 FIX-20）…")
        started = await start_python_service()
        check(
            "服务已自动重启并 /health 200（不留半停环境）",
            bool(started.get("started")),
            f"pid={started.get('pid')} 日志={started.get('log')}"
            if started.get("started")
            else f"启动失败：{started}；请手工执行 cd dkd-agent && .venv/Scripts/python.exe -m app",
        )




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


# ----------------------------------------------------------------------------------
# 相位四：SQLite 文件损坏（与前三相解耦：自建临时文件，不需要服务，也不受本机切 PG 影响）
# ----------------------------------------------------------------------------------


async def phase_sqlite_corrupt() -> None:
    """SQLite 会话库损坏 → 必须给出**可诊断**的错误，且不得静默丢会话。

    ## 为什么这条演练曾经做不了（以及本次怎么把它变成可做）

    3-9 清单里的“SQLite 或 PG 损坏与恢复”，此前一直记着“本机已切 PG，做不了”。
    实际上损坏演练**不需要**服务跑在 SQLite 上：它要验证的是
    「会话库文件坏掉后，系统是给一个可诊断的错，还是静默地当作“没有历史”」。
    所以在临时目录里自建一个 SQLite checkpointer、走真实的图（echo 分支，不调模型）即可：
    既不碰本机开发库，也不依赖服务进程，**在任何后端配置下都能跑**。

    ## 为什么选择“破坏第 2 页”而不是“截断文件”

    截断文件会连数据库头一起破坏，`setup()` 当场就抛（太早、太容易）；
    而只破坏**页面内容**（保留头部）更接近真实故障（磁盘坏扇区/拷贝中断），
    表现为「能打开、能建表，一读会话就报 malformed」——正是最难排查的形态。

    ## 本相位额外验证的一件事：错误分类（FIX-24 的后半句）

    2026-10-01 首次跑通本相位时发现：`sqlite3.DatabaseError` 的类型名不带
    `Operational/Interface/DBAPI/SQLAlchemy`，于是 `/agent/chat` 会把它归成
    “服务内部错误（500）”——用户被告知去查编排逻辑，而真正该做的是恢复会话库文件。
    已把分类规则抽成 `app.api.chat._is_storage_error`（单一定义）并补上 `Database/Integrity` 标记。
    本相**直接调用那个真实函数**断言，而不是在脚本里重写一份判断——
    否则“演练说会归存储类、线上却报内部错误”这种假结论迟早会出现。
    """
    import sqlite3
    import tempfile
    from pathlib import Path as _Path

    from app.api.chat import _is_storage_error
    from app.checkpoint import SqliteCheckpointStore
    from app.graphs.chat_graph import build_chat_graph

    print("\n[SQLite] 会话库文件损坏：必须可诊断，且不得静默丢会话（自建临时文件，不碰服务/业务数据）")

    tmp_dir = _Path(tempfile.mkdtemp(prefix="dkd-sqlite-corrupt-"))
    db_path = tmp_dir / "checkpoints.db"
    thread = {"configurable": {"thread_id": "sqlite-corrupt-drill"}}

    # ---- 1) 基线：正常写入 + 读回（否则后面的“读不到”可能只是本来就没有） ----
    store = SqliteCheckpointStore(db_path)
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=False)
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "损坏前的一轮"}], "scene": 1},
            config=thread,
        )
        state = await graph.aget_state(thread)
        reply = str(state.values.get("reply") or "")
    finally:
        await store.close()
    check("损坏前：会话可正常写回并读回", bool(reply), f"reply={reply[:40]!r}")
    check("损坏前：库文件非空", db_path.stat().st_size > 0, f"{db_path.stat().st_size} bytes")

    # ---- 2) 破坏第 2 页及之后（保留数据库头：模拟磁盘坏扇区，而不是“不是一个库”） ----
    with open(db_path, "r+b") as fh:
        fh.seek(4096)  # 跳过第 1 页（数据库头 + sqlite_master）
        fh.write(b"\x00\xff" * 8192)
        fh.flush()
    print(f"  → 已破坏 {db_path.name} 第 2 页起的内容（头部保留）")

    # ---- 3) 重开并读取：必须抛**可诊断**错误，而不是返回空会话 ----
    store2 = SqliteCheckpointStore(db_path)
    raised: Exception | None = None
    silent_reply: str | None = None
    try:
        saver2 = await store2.open()
        graph2 = build_chat_graph(checkpointer=saver2, use_llm=False)
        state2 = await graph2.aget_state(thread)
        silent_reply = str(state2.values.get("reply") or "")
    except Exception as exc:  # noqa: BLE001 —— 这里就是要观察失败形态
        raised = exc
    finally:
        try:
            await store2.close()
        except Exception:  # noqa: BLE001 —— 已损坏的库关闭时再报错不影响结论
            pass

    check(
        "损坏后：**不得静默返回空会话**（丢会话而不报错是最危险的失败模式）",
        raised is not None,
        f"未抛异常，读回 reply={silent_reply!r}" if raised is None else "",
    )
    if raised is None:
        return

    kind = type(raised).__name__
    message = str(raised)
    print(f"  → 抛出：{type(raised).__module__}.{kind}: {message[:90]}")
    check("损坏后：抛出可诊断的错误（而不是断流/静默）", True, kind)
    check(
        "错误信息能定位到“会话库文件损坏”（而不是一句泛泛的内部错误）",
        any(
            marker in message.lower()
            for marker in ("malformed", "not a database", "corrupt", "encrypted", "disk image")
        ),
        message[:90],
    )
    check(
        "该异常会被 /agent/chat 归为「会话存储」类（用户得到 503 与“会话存储”文案，而非“内部错误”）",
        _is_storage_error(raised),
        f"kind={kind}（分类函数：app.api.chat._is_storage_error）",
    )
    # 反向断言：分类函数不得把什么都当存储问题（否则会把业务故障推给运维）
    check(
        "分类函数双向断言：编程错误不算存储故障、完整性错误算",
        (not _is_storage_error(RuntimeError("boom")))
        and _is_storage_error(sqlite3.IntegrityError("x")),
        "RuntimeError → 非存储类；IntegrityError → 存储类（数据完整性）",
    )

    # 收尾：临时目录清理（不留垃圾；这些文件是演练自造的，不属业务数据）
    try:
        db_path.unlink(missing_ok=True)
        tmp_dir.rmdir()
        print(f"  → 已清理临时目录 {tmp_dir}")
    except OSError as exc:  # noqa: BLE001 —— 清理失败不影响结论，但要说明
        print(f"  → 临时目录未清理（{type(exc).__name__}）：{tmp_dir}")


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


# ----------------------------------------------------------------------------------
# 相位五：MySQL 慢查询护栏（受限窗口 / 分批 / 超时熔断）
# ----------------------------------------------------------------------------------

# 认为“能产生真实慢查询”的业务表行数下限（实测：本机 tb_order 只有几十行）
SLOW_QUERY_ROWS = 10000


class _RecordingSession:
    """转发到真 session 并记录 SQL（供分批计数与 EXPLAIN 使用），**不改变行为**。

    为什么要包一层而不是“拼一条差不多的 SQL 去 EXPLAIN”：
    EXPLAIN 的对象必须是**工具层真正会执行的那条 SQL**，否则演练验的是另一条语句，
    结论不能用来支持“生产上这条查询不会全表扫”（这类假证据比没有证据更危险）。
    记录到的 params 也会一并带进 EXPLAIN，保证参数类型/展开方式与原查询一致。
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.sqls: list[tuple[str, dict]] = []

    async def execute(self, statement: object, params: dict | None = None) -> object:
        self.sqls.append((str(statement), dict(params or {})))
        return await self._inner.execute(statement, params)  # type: ignore[attr-defined]


def _to_explain_sql(sql: str) -> tuple[str, tuple[str, ...]]:
    """把工具层 SQL 里的 POSTCOMPILE 展开占位符还原成 `:name`，供 EXPLAIN 使用。

    为什么必须还原（本相位第一次跑就撞到）：SQLAlchemy 对 `bindparam(..., expanding=True)`
    的参数，在 `str(TextClause)` 里渲染成 `__[POSTCOMPILE_inner_codes]`，**真正执行时**才展开成
    多个绑定参数。直接拿它拼 `"EXPLAIN " + sql` 会报
    `This text() construct doesn't define a bound parameter named 'inner_codes'`——
    如果就此收工，会得出“工具层 SQL 没法 EXPLAIN”这种**错误的结论**（实际上只是渲染形式不同）。

    @return (可直接 EXPLAIN 的 SQL, 需要以 expanding 方式绑定的参数名)
    """
    names = tuple(re.findall(r"__\[POSTCOMPILE_(\w+)\]", sql))
    # 展开参数在 str(TextClause) 里是带外层括号的（`in (__[POSTCOMPILE_x])`），
    # 而 expanding 绑定会**自己补括号**——不把原括号删掉就成了 `in ((%s, %s))`，
    # MySQL 直接报 1241 Operand should contain 1 column(s)（本相位第一次跑就撞到）。
    stripped = re.sub(r"\(\s*__\[POSTCOMPILE_(\w+)\]\s*\)", r":\1", sql)
    return re.sub(r"__\[POSTCOMPILE_(\w+)\]", r":\1", stripped), names


async def _explain(
    session: object, sql: str, params: dict, *, expanding: tuple[str, ...] = ()
) -> dict:
    """对给定 SQL 跑 EXPLAIN 并返回第一行（列名与 MySQL 一致：type / key / key_len / rows）。"""
    from sqlalchemy import bindparam, text

    statement = text("EXPLAIN " + sql)
    if expanding:
        statement = statement.bindparams(*(bindparam(name, expanding=True) for name in expanding))
    result = await session.execute(statement, params)  # type: ignore[attr-defined]
    row = result.mappings().first()
    return dict(row) if row else {}


async def _pick_machine_codes(session: object, *, limit: int = 4) -> list[str]:
    """取几台真实运行中的设备编号（用于真实查询与分批验证）。"""
    from sqlalchemy import text

    result = await session.execute(  # type: ignore[attr-defined]
        text("select inner_code from tb_vending_machine where vm_status = 1 limit :n"),
        {"n": limit},
    )
    return [str(code) for code in result.scalars().all()]


async def phase_slow_query() -> None:
    """MySQL 慢查询护栏（3-9 剩余项）：受限窗口 / 分批 / 超时熔断。

    ## 为什么本相位必须明确区分“验到了什么”与“没验到什么”

    排期里这一项一直挂着“本机开发库造不出有意义的慢查询”。但“造不出慢查询”不等于
    “护栏没法验”：三件事可以分开检验——

    | 护栏 | 本机能否用真 MySQL 验证 | 怎么验 |
    | --- | --- | --- |
    | 受限窗口（时间范围下推到索引） | ✅ 能 | 对**工具层真实执行的 SQL** 跑 EXPLAIN（FORCE INDEX 隔离“优化器选了全表”） |
    | 分批（IN 列表被切开） | ✅ 能 | 把 `read_batch_size` 调到 2，数**真实发出几条 SQL** |
    | 超时熔断 | ✅ 能 | 构造真实形状的重查询（四表交叉聚合），验证它被切断并转成可读错误 |
    | **真实业务数据量下的慢查询** | ❌ **不能** | tb_order 只有几十行；这必须在 **M2 真机环境**验（下方会打印真实行数） |

    最后一行不是“省略了”，而是**本相位会主动说不覆盖**的事——不写清楚就会变成
    “演练全绿”但生产上仍可能被无窗口查询拖垮的假安全感。
    """
    print("\n[慢查询] MySQL 读写护栏：受限窗口 / 分批 / 超时熔断（只读，不写业务数据）")
    if not tcp_ok("127.0.0.1", 3306):
        print("  ⛔ 拒绝执行：MySQL（:3306）不可用")
        raise SystemExit(1)

    from sqlalchemy import text

    from app.config import get_settings
    from app.tools import read_tools
    from app.tools.read_tools import QueryTimeoutError, _fetch_all

    settings = get_settings()

    from app.db import dispose_engine, get_sessionmaker

    maker = get_sessionmaker()
    async with maker() as session:
        # ---- 0) 数据量自检：如实报告“能不能验真实慢查询” ----
        volumes: dict[str, object] = {}
        for table in ("tb_order", "tb_task", "tb_channel", "tb_inventory_log"):
            try:
                volumes[table] = int(
                    (await session.execute(text(f"select count(*) from {table}"))).scalar() or 0
                )
            except Exception as exc:  # noqa: BLE001
                volumes[table] = f"不可读({type(exc).__name__})"
        print(f"  → 业务表真实行数：{volumes}")
        order_rows = volumes.get("tb_order")
        real_slow_possible = isinstance(order_rows, int) and order_rows >= SLOW_QUERY_ROWS
        print(
            "  [信息] 真实业务数据量下的慢查询："
            + ("可验（行数达标）" if real_slow_possible else f"**不覆盖**（tb_order={order_rows} < {SLOW_QUERY_ROWS}，需 M2 真机环境）")
        )

        # ---- 1) 受限窗口：对**工具层真实 SQL** 跑 EXPLAIN ----
        codes = await _pick_machine_codes(session, limit=4)
        check("取到真实设备编号（否则本相位什么也验不了）", len(codes) >= 2, f"codes={codes}")
        # 窗口覆盖库中历史订单（本机数据在 2023-09，近期窗口一律空结果）
        start, end = read_tools.default_sales_window(days=3650)
        recorder = _RecordingSession(session)
        await read_tools.aggregate_channel_sales(recorder, inner_codes=codes, start=start, end=end)
        check("聚合查询在真 MySQL 上执行成功", len(recorder.sqls) >= 1, f"{len(recorder.sqls)} 条 SQL")
        sql, params = recorder.sqls[0]
        sql, expanding = _to_explain_sql(sql)

        # FORCE INDEX 的目的：本表只有几十行时，优化器会自己选全表扫（与索引是否可用无关），
        # 直接 EXPLAIN 得不出结论。强制索引才能把问题隔离成“索引能不能支持这个谓词”。
        forced = sql.replace(
            "from tb_order o", "from tb_order o force index (idx_order_inner_code_create_time)"
        )
        plan = await _explain(session, forced, params, expanding=expanding)
        ok_key = str(plan.get("key") or "") == "idx_order_inner_code_create_time"
        ok_type = plan.get("type") not in (None, "ALL")
        check(
            "受限窗口：时间范围被下推到索引（非全表扫）",
            ok_key and ok_type,
            f"type={plan.get('type')} key={plan.get('key')} key_len={plan.get('key_len')} rows={plan.get('rows')}",
        )

        # 对照：把范围比较换成 date_format（就是 AGENTS §10 / 排期 1-2 点名的反例）
        counter_sql = forced.replace(
            "o.create_time >= :start", "date_format(o.create_time, '%Y%m%d') >= :start_d"
        ).replace("o.create_time < :end", "date_format(o.create_time, '%Y%m%d') <= :end_d")
        counter_params = {
            "inner_codes": tuple(codes),
            "start_d": start.strftime("%Y%m%d"),
            "end_d": end.strftime("%Y%m%d"),
            "status": settings.sales_order_status,
            "limit": settings.read_row_limit,
        }
        plan2 = await _explain(session, counter_sql, counter_params, expanding=expanding)
        len_real = int(plan.get("key_len") or 0)
        len_counter = int(plan2.get("key_len") or 0)
        check(
            "对照：函数包裹索引列后只能用索引前半段（key_len 变小）",
            len_counter < len_real,
            f"范围比较 key_len={len_real} vs date_format key_len={len_counter}"
            "（key_len 是索引定义算出来的，不受本表行数影响）",
        )

        # ---- 2) 分批：IN 列表必须被切开（数真实发出的 SQL 条数） ----
        original_batch_size = settings.read_batch_size
        settings.read_batch_size = 2
        try:
            recorder2 = _RecordingSession(session)
            await read_tools.aggregate_channel_sales(
                recorder2, inner_codes=codes, start=start, end=end
            )
            expected_batches = -(-len(codes) // 2)
            batch_sizes = [len(p.get("inner_codes") or ()) for _, p in recorder2.sqls]
            check(
                "分批：IN 列表按 read_batch_size 拆成多条 SQL",
                len(recorder2.sqls) == expected_batches,
                f"设备 {len(codes)} 台 / 批 2 → 实际 SQL {len(recorder2.sqls)} 条（期望 {expected_batches}）",
            )
            check(
                "分批：每批设备数不超批次上限",
                bool(batch_sizes) and all(n <= 2 for n in batch_sizes),
                f"各批设备数={batch_sizes}",
            )
        finally:
            settings.read_batch_size = original_batch_size

    # ---- 3) 超时熔断：用**独立引擎**跑一条重查询（被取消的连接可能不可用，不能污染上面的检查）----
    original_timeout = settings.read_query_timeout_s
    settings.read_query_timeout_s = 0.3
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(settings.dsn, pool_size=1, max_overflow=0)
        try:
            isolated = async_sessionmaker(engine, expire_on_commit=False)
            async with isolated() as session2:
                # 为什么用 sleep 而不是“真业务慢查询”：本机 tb_order 只有 29 行，
                # 即使四表交叉（29^4≈70 万行聚合）也在 0.3s 内跑完——**造不出真实的慢查询**。
                # 而本项要验的是**熔断机制**（超时→切断→转成可读 QueryTimeoutError），
                # 这个机制与“查询为何慢”无关，因此用 sleep 确定性地把耗时拉长即可。
                # ⚠️ 仍然挂真实白名单表名（tb_order）：白名单校验也是被验的一部分，
                #    不能靠“随便写个表名”绕过去。
                heavy_sql = "select sleep(:secs) from tb_order limit 1"
                try:
                    await _fetch_all(
                        session2, heavy_sql, {"secs": 2}, tables=("tb_order",)
                    )
                    check(
                        f"超时熔断：慢查询在 {settings.read_query_timeout_s:.1f}s 内被切断",
                        False,
                        "未超时（sleep 未被生效？）",
                    )
                except QueryTimeoutError as exc:
                    check(
                        f"超时熔断：慢查询在 {settings.read_query_timeout_s:.1f}s 内被切断",
                        True,
                        f"抛出 QueryTimeoutError：{str(exc)[:70]}",
                    )
                    check(
                        "超时错误文案可行动（告诉调用方缩小窗口/减少设备数）",
                        "缩小" in str(exc) or "减少" in str(exc),
                        str(exc)[:80],
                    )
                except Exception as exc:  # noqa: BLE001 —— 形态不对也要如实报告
                    check(
                        "超时熔断：慢查询被切断并转成可读错误",
                        False,
                        f"抛出的不是 QueryTimeoutError，而是 {type(exc).__name__}: {str(exc)[:80]}",
                    )
        finally:
            await engine.dispose()
    finally:
        settings.read_query_timeout_s = original_timeout

    print(
        "  [信息] 未覆盖（如实说明）：真实业务数据量下的慢查询（时间窗口 + 分批 + 超时三者同时生效的实景）"
        " 需 M2 真机环境；本相位验证的是护栏本身的真实行为（真 MySQL）。"
    )
    # 释放只读引擎（否则进程退出时 aiomysql 的 __del__ 会在已关闭的 event loop 上报噪音）
    await dispose_engine()


async def main() -> int:
    parser = argparse.ArgumentParser(description="3-9 剩余演练（对话并发 / Python 宕机降级 / PG 恢复 / SQLite 损坏）")
    parser.add_argument(
        "--phase",
        required=True,
        choices=["concurrent", "python-down", "pg-recover", "sqlite-corrupt", "slow-query"],
    )
    parser.add_argument(
        "--auto-manage-service",
        action="store_true",
        help="仅 python-down 相：由脚本自己停掉 :8090、验完再重启（默认关闭，需显式给出）",
    )
    args = parser.parse_args()

    if args.phase == "concurrent":
        await phase_concurrent()
    elif args.phase == "python-down":
        await phase_python_down(auto_manage=args.auto_manage_service)
    elif args.phase == "sqlite-corrupt":
        await phase_sqlite_corrupt()
    elif args.phase == "slow-query":
        await phase_slow_query()
    else:
        await phase_pg_recover()

    print(f"\n结果：PASS={_pass} FAIL={_fail}")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
