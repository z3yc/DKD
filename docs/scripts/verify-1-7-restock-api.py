"""排期任务 1-7 端到端验收脚本（真 MySQL + 真 HTTP 接口）。

验收标准原文：**原型 V2 全部干预操作后端化；原因必填校验（拒绝路径有测试）**。
本脚本用真实数据库与真实 FastAPI 应用跑一遍，把每一步的 HTTP 状态码与库中行状态都打印出来。

```
cd dkd-agent
set -a; source ../.env; set +a
.venv/Scripts/python.exe ../docs/scripts/verify-1-7-restock-api.py
```

脚本自动完成：① 用 1-6 的图跑一次分析做种子数据 → ② 走 HTTP 接口做干预 →
③ 直接查库核对状态与审计列 → ④ 软删种子数据（不污染开发库）。
与 verify-1-6 的分工：1-6 验「图 + 中断恢复」，本脚本验「HTTP 契约 + 干预规则」。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402
from langgraph.types import Command  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.checkpoint import CheckpointStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.graphs.restock_graph import (  # noqa: E402
    SqlRestockDeps,
    build_restock_graph,
    new_restock_state,
)
from app.graphs.restock_plan_store import SqlPlanStore  # noqa: E402

CHECKPOINT = Path("var/checkpoints-verify-1-7.db")
USER_HEADERS = {"X-Agent-User": "7", "X-Agent-Username": "zhangsan"}
TODAY = date.today().isoformat()

_ok = 0
_fail = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _ok, _fail
    if condition:
        _ok += 1
        print(f"  PASS  {label}" + (f" —— {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  FAIL  {label}" + (f" —— {detail}" if detail else ""))


def section(title: str) -> None:
    print()
    print(title)


async def seed_plans() -> int:
    """种子数据：跑一次真实分析（1-6 的图），拿到几行待确认计划。"""
    store = CheckpointStore(CHECKPOINT)
    saver = await store.open()
    deps = SqlRestockDeps(store=SqlPlanStore())
    graph = build_restock_graph(deps=deps, checkpointer=saver, settings=get_settings())
    config = {"configurable": {"thread_id": f"restock-{TODAY}"}}
    try:
        result = await graph.ainvoke(
            new_restock_state(plan_date=TODAY, request_id="verify-1-7-seed"), config
        )
        if not result.get("__interrupt__"):
            return 0
        # 分析结果已落库；此处用空决策恢复中断，避免脚本停在待确认状态（不产生业务副作用）
        await graph.ainvoke(Command(resume=[]), config)
        return len(result.get("plans", []))
    finally:
        await store.close()


async def reset_fixture() -> None:
    """开发库夹具复位：复活同日计划行并置回 1-建议（不物理删除）。"""
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "update agent_restock_plan set del_flag = '0', status = 1, adjust_reason = NULL, "
                "adjusted_by = NULL, adjusted_time = NULL, task_id = NULL, task_code = NULL "
                "where plan_date = :d"
            ),
            {"d": TODAY},
        )
        await session.commit()


async def db_rows() -> list[dict]:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                "select id, vm_id, status, total_quantity, adjust_reason, adjusted_by "
                "from agent_restock_plan where plan_date = :d and del_flag = '0' order by vm_id"
            ),
            {"d": TODAY},
        )
        return [dict(row) for row in result.mappings().all()]


async def decision_rows(
    *, since: datetime, request_id: str | None = None
) -> list[dict]:
    """本次脚本运行期间写入的补货干预留痕（1-7b；可按 request_id 精确过滤）。"""
    sql = (
        "select request_id, scene, user_id, trigger_type, action, target_type, target_id, "
        "result, error_msg, llm_output from agent_decision_log "
        "where action like 'restock.%' and create_time >= :since"
    )
    params: dict[str, object] = {"since": since}
    if request_id is not None:
        sql += " and request_id = :rid"
        params["rid"] = request_id
    sql += " order by id"
    async with get_sessionmaker()() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]


async def cleanup() -> None:
    """软删验收数据（非 DELETE：AGENTS §7.4，账号也没有 DELETE 权限）。"""
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "update agent_restock_plan set del_flag = '1', update_by = 'verify-1-7' "
                "where plan_date = :d"
            ),
            {"d": TODAY},
        )
        await session.execute(
            text(
                "update agent_restock_pause set del_flag = '1', update_by = 'verify-1-7' "
                "where scope = 'global'"
            )
        )
        await session.commit()
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()


async def run() -> int:
    try:
        return await _run_body()
    finally:
        # 无论中途成败都要清场：否则残留的 3/4 状态行会让下一次验收全部撞 409
        await cleanup()


async def _run_body() -> int:
    """单事件循环内完成全部步骤。

    为什么不用 `TestClient` + 多个 `asyncio.run`（第一版踩过的坑）：
    TestClient 会为应用单开一个事件循环（阻塞门户线程），而 aiomysql 连接池绑定在**创建它的循环**上；
    脚本若在另一个循环里直连查库，就会拿到属于别的循环的连接，表现为
    `AttributeError: 'NoneType' object has no attribute 'send'` /
    `RuntimeError: Event loop is closed`（看着像数据库故障，其实是事件循环错配）。
    改用 ASGITransport + 手动 lifespan 后，应用与脚本查询同循环。
    """
    from app.main import create_app

    app = create_app()
    # 留痕核对的时间下界：稍往前留 5s 余量，避免边界上漏掉第一条
    run_started = datetime.now() - timedelta(seconds=5)
    print("=" * 84)
    print(f"[1-7 验收] 目标日期={TODAY}（真 MySQL + 真实 FastAPI 应用，同一事件循环）")
    print("=" * 84)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent.local") as client:
            # 起跑前复位开发库夹具：把同日残留行复活并重置为 1-建议。
            # 为什么需要：本脚本会反复跑；上一轮留下的行若已是 3-已跳过/4-已建单，
            # upsert 按设计**不会**覆盖 items/status（“已建单/已复盘不得被重跑覆盖”），
            # 后续干预就全部撞 409 —— 那是**正确**的产品行为，但会让验收自乱。
            # 这是测试夹具复位，只对开发库执行。
            await reset_fixture()
            seeded = await seed_plans()
            print(f"[种子] 分析生成计划 {seeded} 份（落库 status=1）")
            rows = await db_rows()
            if not rows:
                print("  [SKIP] 开发库没有可补货的货道，无法继续验收（需先准备 tb_inventory 数据）")
                return 1
            plan_id = int(rows[0]["id"])
            print(
                f"[种子] 取第一条做验收集：plan_id={plan_id} vm={rows[0]['vm_id']} "
                f"status={rows[0]['status']}"
            )

            section("[A] 读接口（工作台数据源）")
            resp = await client.get("/agent/restock/plans", params={"plan_date": TODAY})
            body = resp.json()
            check(
                "GET /agent/restock/plans 返回 200 信封",
                resp.status_code == 200 and body["code"] == 200,
            )
            check(
                "清单含全部计划与统计卡",
                len(body["data"]["plans"]) == len(rows),
                f"plans={len(body['data']['plans'])}",
            )
            check("每条含原型字段（依据）", "reason" in body["data"]["plans"][0]["items"][0])

            section("[B] 写接口鉴权（AGENTS §7.5）")
            # 用**另一条**计划做鉴权放行验证（并立即恢复），避免把主验收集提前打成终态
            other_id = int(rows[-1]["id"])
            resp = await client.post(f"/agent/restock/plans/{other_id}/skip", json={"reason": "x"})
            check("无 X-Agent-User → 401", resp.status_code == 401, f"实际 {resp.status_code}")
            resp = await client.post(
                f"/agent/restock/plans/{other_id}/skip",
                json={"reason": "鉴权验证"},
                headers=USER_HEADERS,
            )
            check("有身份头 → 放行", resp.status_code == 200, f"实际 {resp.status_code}")
            resp = await client.post(
                f"/agent/restock/plans/{other_id}/restore",
                json={"reason": "鉴权验证后复原"},
                headers=USER_HEADERS,
            )
            check(
                "复原验收集外的那条计划",
                resp.status_code == 200 and resp.json()["data"]["status"] == 1,
                f"实际 {resp.status_code}",
            )

            section("[C] 原因必填（拒绝路径）")
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/adjust", json={"quantity": 1}, headers=USER_HEADERS
            )
            check(
                "调整缺原因 → 400/422",
                resp.status_code in (400, 422),
                f"实际 {resp.status_code}: {resp.json()['msg']}",
            )
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/adjust",
                json={"reason": "  ", "quantity": 1},
                headers=USER_HEADERS,
            )
            check("调整原因全空白 → 400", resp.status_code == 400, resp.json()["msg"][:60])

            section("[D] 按人工参数重算（预测窗口 7 天 + 服务水平 0.95）")
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/adjust",
                json={"reason": "最近一周卖得猛", "windowDays": 7, "serviceLevel": 0.95},
                headers=USER_HEADERS,
            )
            detail = resp.json()["data"]
            check("重算接口返回 200", resp.status_code == 200, f"实际 {resp.status_code}")
            item = detail["plan"]["items"][0]
            check("状态 → 2-已调整", detail["status"] == 2, f"status={detail['status']}")
            check(
                "依据含人工指定参数",
                "人工指定参数" in item["reason"] and "预测窗口=7天" in item["reason"],
                item["reason"][:80],
            )
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/adjust",
                json={"reason": "越界试试", "serviceLevel": 1.5},
                headers=USER_HEADERS,
            )
            check(
                "服务水平越界 → 400（人工输入不静默夹取）",
                resp.status_code == 400,
                resp.json()["msg"][:50],
            )

            section("[E] 跳过 → 恢复（原型「恢复建议」，仅当天）")
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/skip",
                json={"reason": "点位即将撤机"},
                headers=USER_HEADERS,
            )
            check("跳过成功", resp.status_code == 200 and resp.json()["data"]["status"] == 3)
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/skip",
                json={"reason": "再跳一次"},
                headers=USER_HEADERS,
            )
            check("重复跳过 → 409（终态）", resp.status_code == 409, resp.json()["msg"][:50])
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/restore",
                json={"reason": "误点"},
                headers=USER_HEADERS,
            )
            check("恢复建议 → 1-建议", resp.status_code == 200 and resp.json()["data"]["status"] == 1)
            resp = await client.post(
                f"/agent/restock/plans/{plan_id}/restore",
                json={"reason": "再恢复"},
                headers=USER_HEADERS,
            )
            check("非跳过状态恢复 → 409", resp.status_code == 409, resp.json()["msg"][:50])

            section("[F] 暂停 / 恢复自动分析（真实写 agent_restock_pause）")
            resp = await client.post(
                "/agent/restock/pause", json={"reason": "盘点一周"}, headers=USER_HEADERS
            )
            check("暂停成功", resp.status_code == 200 and resp.json()["data"]["paused"] is True)
            state = (await client.get("/agent/restock/pause")).json()["data"]
            check(
                "回读已暂停且含操作人",
                state["paused"] is True and state["paused_by"] == "7",
                str(state),
            )
            resp = await client.post(
                "/agent/restock/resume", json={"reason": "盘点完成"}, headers={"X-Agent-User": "8"}
            )
            check("恢复成功", resp.status_code == 200 and resp.json()["data"]["paused"] is False)
            check(
                "保留暂停人（审计完整）",
                resp.json()["data"]["paused_by"] == "7"
                and resp.json()["data"]["resumed_by"] == "8",
                "恢复不清掉“谁暂停的”",
            )
            resp = await client.post(
                "/agent/restock/pause", json={"reason": "再次暂停"}, headers=USER_HEADERS
            )
            check("再次暂停（UPSERT 幂等）", resp.status_code == 200)

            section("[G] 库中行状态核对")
            final_rows = await db_rows()
            for row in final_rows:
                print(
                    f"     plan_id={row['id']} vm={row['vm_id']} status={row['status']} "
                    f"qty={row['total_quantity']} reason={row['adjust_reason']!r} "
                    f"by={row['adjusted_by']}"
                )
            restored = [r for r in final_rows if int(r["id"]) == plan_id][0]
            check(
                "验收行最终为 1-建议且留痕最近一次干预原因",
                restored["status"] == 1 and restored["adjust_reason"] is not None,
            )

            section("[H] 决策留痕（1-7b / FIX-2：AGENTS §6.3「写操作必留痕」+ §6.4 request_id）")
            # 额外跑一次带显式 X-Request-Id 的干预，验证网关/Middleware 的 rid 真的落到了留痕里
            rid = "verify-1-7b-rid"
            resp = await client.post(
                f"/agent/restock/plans/{other_id}/skip",
                json={"reason": "留痕 request_id 验证"},
                headers={**USER_HEADERS, "X-Request-Id": rid},
            )
            check("带 X-Request-Id 的干预成功", resp.status_code == 200, f"实际 {resp.status_code}")
            await client.post(
                f"/agent/restock/plans/{other_id}/restore",
                json={"reason": "留痕验证后复原"},
                headers=USER_HEADERS,
            )
            current_rows = await db_rows()
            check(
                "复原验收集外的计划（留痕验证后未污染 [G] 快照）",
                all(r["status"] == 1 for r in current_rows),
                f"statuses={[r['status'] for r in current_rows]}",
            )

            ledger = await decision_rows(since=run_started)
            for row in ledger:
                print(
                    f"     {row['action']:<18} target={row['target_type']}:{row['target_id']} "
                    f"user={row['user_id']} result={row['result']} "
                    f"err={(row['error_msg'] or '')[:24]!r}"
                )
            actions = {row["action"] for row in ledger}
            check("留痕已落到 agent_decision_log", len(ledger) >= 6, f"本次 {len(ledger)} 条")
            check(
                "四类计划干预 + 开关都有留痕",
                {
                    "restock.adjust",
                    "restock.skip",
                    "restock.restore",
                    "restock.pause",
                    "restock.resume",
                }
                <= actions,
                f"actions={sorted(actions)}",
            )
            check(
                "全部为人工干预口径（scene=2 / trigger_type=3）",
                all(row["scene"] == 2 and row["trigger_type"] == 3 for row in ledger),
            )
            rejected = [row for row in ledger if row["result"] == 3]
            check(
                "被拒绝的决策也留痕且原因原样保存",
                bool(rejected) and all(row["error_msg"] for row in rejected),
                f"拒绝 {len(rejected)} 条",
            )
            check(
                "人工干预不写 llm_output（不得把人工操作伪装成模型建议）",
                all(row["llm_output"] is None for row in ledger),
            )
            plan_rows = [row for row in ledger if row["target_type"] == "plan"]
            check(
                "计划类留痕可定位（target_type=plan + 数字 target_id）",
                bool(plan_rows) and all(str(row["target_id"]).isdigit() for row in plan_rows),
            )
            traced = await decision_rows(since=run_started, request_id=rid)
            check(
                "X-Request-Id 贯穿到留痕（AGENTS §6.4）",
                bool(traced) and all(row["request_id"] == rid for row in traced),
                f"命中 {len(traced)} 条",
            )
            # 留痕行**故意保留**：审计证据不随验收清理（清理只软删计划/开关两条业务夹具）
            print("     [说明] 本次留痕行保留在库中（审计证据，非验收夹具）")

        section("[清理] 软删本次验收数据（非 DELETE）")

    print("=" * 84)
    print(f"结论：PASS={_ok} FAIL={_fail}")
    print("=" * 84)
    return 0 if _fail == 0 else 1


async def main() -> int:
    try:
        return await run()
    finally:
        await dispose_engine()


sys.exit(asyncio.run(main()))
