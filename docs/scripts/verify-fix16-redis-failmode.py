"""FIX-16 演练：Redis 抖动时「Agent 建单全失败」到底是什么形态（排期 3-9 分项）。

## 为什么要有这个演练

FIX-16 登记：「`@RateLimiter` 的 Redis 依赖是 fail-closed——Redis 不可用时回调用例会全拒。
这属预期行为，但**未做演练**。影响：上线后 Redis 抖动可能表现为『Agent 建单全失败』，
运维会朝错误方向排查。」

`RateLimiterAspect` 的行为已在代码里写死（Redis 异常 → `RuntimeException("服务器限流异常，请稍候再试")`），
但**运维实际看到什么**取决于三层怎么传递这条信息：
Java 限流切面 → Java 全局异常处理器（HTTP 500）→ Python 回调客户端分类 → 前端/运营看到的文案。
只读代码回答不了“运维能不能定位到 Redis”，必须真跑一遍。

## 三个相位（Redis 的停/起由外部操作，脚本不自己动环境）

```bash
cd dkd-agent
export DKD_AGENT_SERVICE_SECRET=<从本机 .env 读>
export DKD_AGENT_JAVA_BASE=http://127.0.0.1:8080

python ../docs/scripts/verify-fix16-redis-failmode.py --phase baseline   # Redis 正常
/e/Redis-x64-3.2.100/redis-cli.exe shutdown                             # 停 Redis
python ../docs/scripts/verify-fix16-redis-failmode.py --phase outage     # Redis 挂掉
cd /e/Redis-x64-3.2.100 && ./redis-server.exe redis.windows.conf &      # 起 Redis
python ../docs/scripts/verify-fix16-redis-failmode.py --phase recovered  # 恢复
```

`--phase outage` 除了探测 Java 侧，还会经**补货确认接口**走一次完整链路，
把“运营真实看到的文案”打出来——这才是本演练要回答的问题。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402

JAVA_BASE = os.environ.get("DKD_AGENT_JAVA_BASE", "http://127.0.0.1:8080")
AGENT_BASE = os.environ.get("DKD_AGENT_BASE_URL", "http://127.0.0.1:8090")
SECRET = os.environ.get("DKD_AGENT_SERVICE_SECRET", "")

# 故意用一个**不存在的设备编号**：这样即使鉴权与限流都通过，也会在
# `insertTaskDto` 的第一道校验（售货机不存在）就被拒 —— **零写入**，
# 演练不会污染业务数据（对比 verify-1-3 会真建单，需人工清理）。
CALLBACK_BODY = {
    "innerCode": "FIX16-NOT-EXIST",
    "productTypeId": 2,
    "userId": 1,
    "taskDetails": [],
}

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


def probe_java_callback() -> tuple[int, str]:
    """直连 Java 回调（跳过网关），返回 (HTTP 状态码, 响应文本)。"""
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            f"{JAVA_BASE}/agent/callback/task",
            json=CALLBACK_BODY,
            headers={"X-Agent-Secret": SECRET, "Content-Type": "application/json"},
        )
        return resp.status_code, resp.text


def redis_is_reachable() -> tuple[bool, str]:
    """Redis 是否可连（不依赖 redis 客户端库，直接探 TCP）。

    ## 为什么“故障相位”必须先跑这个自检（血的教训，2026-10-01）

    第一版脚本没有自检，直接假定“操作者已经停掉 Redis”。实际执行时 redis-cli 因需要
    AUTH 而 **shutdown 失败**（NOAUTH），Redis 好好地活着——于是“故障相位”**在正常环境下"
    跑完了整条链路，把一次真实建单当成了“演练成功”的前提：

      · tb_task 多出了 task_id=574（inner_code='aim5xu4I'，status=1，真实写入）；
      · agent_restock_plan id=84 被改成 4-已建单；
      · 而脚本本身还以为在演练 Redis 故障（FAIL=4，完全没意识到“环境不对”）。

    教训：**一个会改数据、又依赖“环境已被人工改成某状态”的脚本，必须自己校验那个状态。**
    这正是本函数存在的理由：宁可大声拒绝执行，也不要静默地在一个错误前提上跑完全程。
    """
    host = os.environ.get("DKD_REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("DKD_REDIS_PORT", "6379"))
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return True, f"{host}:{port} 可连接"
    except OSError as exc:
        return False, f"{host}:{port} 不可连（{type(exc).__name__}）"


def describe(status: int, body: str) -> str:
    """把响应压成一行可核对的事实（不打印整个响应体，避免日志里塞业务数据）。"""
    try:
        payload = json.loads(body)
        return f"HTTP {status} / code={payload.get('code')} / msg={str(payload.get('msg'))[:80]}"
    except Exception:  # noqa: BLE001 —— 非 JSON 也要能报告
        return f"HTTP {status} / body={body[:80]}"


# ----------------------------------------------------------------------------------
# 相位一：基线（Redis 正常）——证明“除了 Redis，其它环节都是好的”
# ----------------------------------------------------------------------------------


def _is_business_rejection(body: str) -> tuple[bool, str]:
    """基线相位真正要判的东西：**是业务校验在拦，而不是鉴权/限流在拦**。

    为什么不比对具体中文文案：本题用的是空明细的探测载荷，先撞上哪一道校验取决于
    `insertTaskDto` 的校验顺序（实测先报“补货工单必须包含工单明细(details)”）。
    把断言绑死在某句话上，会因为无关的校验顺序调整而变红——那是测试在报警，不是产品出问题。
    真正的判据是：HTTP 200 + code 500 + **msg 里没有限流/密钥关键词**。
    """
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        return False, "响应不是 JSON"
    code = payload.get("code")
    msg = str(payload.get("msg") or "")
    is_business = code == 500 and not any(k in msg for k in ("限流", "Redis", "redis", "密钥"))
    return is_business, msg


def phase_baseline() -> None:
    print("\n[基线] Redis 正常：回调应“通到业务校验”并被业务拒绝（不是链路故障）")
    status, body = probe_java_callback()
    print(f"  → {describe(status, body)}")

    check("回调路径可达（鉴权 + 限流 + JSON 绑定都过了）", status == 200, f"HTTP {status}")
    is_business, msg = _is_business_rejection(body)
    check("拦截者是业务校验（msg 不是限流/密钥类）", is_business, f"msg={msg[:80]}")


# ----------------------------------------------------------------------------------
# 相位二：故障（Redis 已停）
# ----------------------------------------------------------------------------------


async def phase_outage() -> None:
    print("\n[故障] Redis 已停：记录 Java 侧形态，并看运营在补货工作台上看到什么")

    # ⚠️ 前置自检（见 redis_is_reachable docstring 里的那次真实事故）：
    # Redis 还活着就**拒绝执行**——否则本相位会在正常环境下“跑得很好看”，
    # 而它中途会真的建出一张工单（把演练变成写入事故）。
    reachable, detail = redis_is_reachable()
    if reachable:
        print(f"  ⛔ 拒绝执行：Redis 仍然可连（{detail}）。")
        print("     本相位必须先真的停掉 Redis；否则它会在正常环境下真的建单（历史事故见脚本内注释）。")
        print("     停 Redis（需密码）： redis-cli -a $DKD_REDIS_PASSWORD shutdown")
        print("\n结果：PASS=0 FAIL=1（已拒绝，避免误写）")
        raise SystemExit(1)
    print(f"  ✓ 前置自检通过：{detail}")

    status, body = probe_java_callback()
    print(f"  → Java 回调：{describe(status, body)}")

    # ⚠️ 第一条被实测推翻的假设：**RuoYi 不会因应用异常返回 HTTP 5xx**。
    # `GlobalExceptionHandler.@ExceptionHandler(RuntimeException)` → `AjaxResult.error(...)`
    # = **HTTP 200 + code=500**（实测：服务器限流异常走的就是这条）。
    # 所以判据不能写 `status >= 500`，而要按**信封**判：HTTP 200 时看 code。
    # 顺带一个推论：Agent 侧那个 `status_code >= 500` 分支永远不会因限流命中，
    # 限流失败实际是作为“业务拒绝（HTTP 200 + code=500）”被分类的——这本身值得登记。
    try:
        envelope = json.loads(body)
    except Exception:  # noqa: BLE001
        envelope = {}
    code = envelope.get("code")
    failed = status >= 500 or (status == 200 and code != 200)
    check("回调转为失败（fail-closed 生效，不是静默放行）", failed, f"HTTP {status} / code={code}")
    check(
        "Java 文案能指向限流/Redis（而不是笼统的服务异常）",
        "限流" in body or "Redis" in body or "redis" in body,
        f"msg 片段：{str(envelope.get('msg'))[:80]}",
    )

    # 再看 Python 侧怎么把这条失败传给运营 —— 这一步才是本演练的重点
    secret_header = {}
    if SECRET:
        secret_header["X-Agent-Secret"] = SECRET
    async with httpx.AsyncClient(timeout=30.0) as client:
        plans = await client.get(f"{AGENT_BASE}/agent/restock/plans")
        plan_id = None
        if plans.status_code == 200:
            data = plans.json().get("data") or {}
            for plan in data.get("plans") or []:
                if plan.get("status") in (1, 2, 6):
                    plan_id = plan.get("id")
                    break
        check("能取到一份待处理计划（否则本相位无法验完整链路）", plan_id is not None, f"plan_id={plan_id}")

        if plan_id is not None:
            resp = await client.post(
                f"{AGENT_BASE}/agent/restock/plans/{plan_id}/confirm",
                headers={"X-Agent-User": "7", "X-Request-Id": "fix16-outage"},
            )
            data = resp.json() if resp.status_code else {}
            msg = str(data.get("msg"))
            print(f"  → 补货确认接口：HTTP {resp.status_code} / msg={msg[:160]}")
            check("确认建单以 502 失败（不是假成功）", resp.status_code == 502, f"HTTP {resp.status_code}")
            # ⚠️ 本演练的核心验收点：运营看到的文案里**有没有可排查的线索**。
            # 实测发现两条路：
            #   · 快路：Java 立即失败（HTTP 200 + code=500“服务器限流异常”）
            #     → Python 归为业务拒绝，原文透传 → 可定位；
            #   · 慢路：Java 等 Redis 连接超时 > 10s，Agent 先行超时
            #     → Java 的精确原因**整个丢失**，运营只能看到“超时”。
            # 后者就是 FIX-16 担心的“朝错误方向排查”。本轮已把超时文案改成
            # 自带下一步动作 + 把 Redis 列为可能原因（见 task_tools.py），
            # 因此两条路现在都应包含可排查线索。
            check(
                "**运营看到的文案可排查**（含限流/Redis 线索，FIX-16 核心验收点）",
                ("限流" in msg) or ("Redis" in msg) or ("redis" in msg),
                f"运营看到的：{msg[:140]}",
            )
            check(
                "告诉运营下一步动作（先核对状态再决定重试）",
                ("刷新" in msg) or ("重试" in msg),
                f"运营看到的：{msg[:140]}",
            )

        # 关键断言：故障期间**零业务写入**（这正是 fail-closed 的意义）
        after = await client.get(f"{AGENT_BASE}/agent/restock/plans")
        after_plans = (after.json().get("data") or {}).get("plans") or []
        target = [p for p in after_plans if p.get("id") == plan_id]
        if target:
            check(
                "故障期间计划状态未变（未产生幽灵工单）",
                target[0].get("status") in (1, 2, 6) and not target[0].get("taskCode"),
                f"status={target[0].get('status')} taskCode={target[0].get('taskCode')}",
            )


# ----------------------------------------------------------------------------------
# 相位三：恢复
# ----------------------------------------------------------------------------------


def phase_recovered() -> None:
    print("\n[恢复] Redis 起回来：应自动回到基线形态（无需重启 Java）")
    status, body = probe_java_callback()
    print(f"  → {describe(status, body)}")
    check("回调自动恢复（未留下需要重启的粘滞状态）", status == 200, f"HTTP {status}")
    is_business, msg = _is_business_rejection(body)
    check("又回到业务校验拦截（不是限流）", is_business, f"msg={msg[:80]}")


async def phase_cleanup() -> None:
    """清理演练/误跑留下的写入（默认 dry-run，`--apply` 才真改）。

    为什么要这个相位：本演练的“故障”情形下本不会写入任何数据；但一旦环境不对
    （Redis 没真停），确认建单会**成功建单**。与其靠人记得去清，不如把清理做成脚本的一部分。
    """
    import httpx as _httpx

    print("\n[清理] 把“非预期成功”的确认回退（默认只列出，加 --apply 才真改）")
    async with _httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{AGENT_BASE}/agent/restock/plans")
        data = resp.json().get("data") or {}
        plans = data.get("plans") or []
        suspects = [p for p in plans if p.get("status") == 4 and p.get("adjustReason") is None]
        for plan in suspects:
            print(
                f"  · plan_id={plan.get('id')} vm={plan.get('innerCode')} "
                f"status=4 taskCode={plan.get('taskCode')}"
            )
        print(f"  → 共 {len(suspects)} 份“已建单”计划（未列出的请人工核对 agent_decision_log）")
        print("  → 回退方式（需人工执行，确认确属误跑后）：")
        print("     UPDATE agent_restock_plan SET status=1, task_id=NULL, task_code=NULL WHERE id=<plan_id>;")
        print("     UPDATE tb_task SET task_status=3 WHERE task_code='<task_code>';  -- 软取消，不物理删除")
        print("     ⚠️ 两者必须成对做：只回退计划会让工单仍占着“活跃”名额（与 FIX-1 的唯一约束冲突）")
    check("清理清单已输出（人工确认后执行）", True, "本脚本不代为修改业务数据")


async def main() -> int:
    parser = argparse.ArgumentParser(description="FIX-16 Redis fail-closed 演练")
    parser.add_argument(
        "--phase",
        required=True,
        choices=["baseline", "outage", "recovered", "cleanup"],
    )
    args = parser.parse_args()

    if not SECRET:
        print("⚠️  DKD_AGENT_SERVICE_SECRET 未设置：回调会因缺密钥被 401 拒，演练结论无效。")
        print("    请先 `set -a; source ../.env; set +a`（在 dkd-agent 目录下）。")

    if args.phase == "baseline":
        phase_baseline()
    elif args.phase == "outage":
        await phase_outage()
    elif args.phase == "cleanup":
        await phase_cleanup()
    else:
        phase_recovered()

    print(f"\n结果：PASS={_pass} FAIL={_fail}")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
