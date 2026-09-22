"""排期任务 1-6 端到端验收脚本（真 MySQL + 真 SQLite checkpointer，**分两个进程跑**）。

验收标准原文：**调整/跳过/恢复走状态机并留痕 `agent_restock_plan`；中断-恢复在服务重启后仍可续**。
所以本脚本刻意拆成两次独立进程调用（而不是一个进程里关一下再开）：

```
# 1) 分析 → 落库(status=1) → 在人工确认处中断，进程退出
python ../docs/scripts/verify-1-6-restock-plan.py --phase analyze --date 2026-09-21

# 2) 新进程：从 SQLite 检查点恢复 → 应用人工决策 → 回写状态（含审计列）
python ../docs/scripts/verify-1-6-restock-plan.py --phase resume --date 2026-09-21 \
       --action adjust --reason "验收：人工下调数量" --quantity 3 --user-id 7

# 3) 收尾：软删本次验收的计划行（AGENTS §7.4，账号本身也没有 DELETE 权限）
python ../docs/scripts/verify-1-6-restock-plan.py --phase cleanup --date 2026-09-21
```

每个阶段都直接查 MySQL 打印真实行状态，证据不依赖脚本自述。
LLM 校准默认关闭（`--calibration` 可开），避免验收时产生真实 API 花费。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from sqlalchemy import text  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.checkpoint import CheckpointStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.graphs.restock_graph import (  # noqa: E402
    SqlRestockDeps,
    build_restock_graph,
    new_restock_state,
)
from app.graphs.restock_plan_store import SqlPlanStore  # noqa: E402

CHECKPOINT = Path("var/checkpoints-verify-1-6.db")


def _thread(date_text: str) -> str:
    return f"restock-{date_text}"


async def _print_rows(plan_date: str) -> int:
    """打印 agent_restock_plan 的真实行（只读），返回行数。"""
    sql = (
        "select id, vm_id, inner_code, status, sku_count, total_quantity, adjust_reason, "
        "adjusted_by, adjusted_time, assignee_id, task_id, del_flag "
        "from agent_restock_plan where plan_date = :d order by vm_id"
    )
    async with get_sessionmaker()() as session:
        result = await session.execute(text(sql), {"d": plan_date})
        rows = result.mappings().all()
    print(f"[db] agent_restock_plan(plan_date={plan_date}) 共 {len(rows)} 行：")
    for row in rows:
        print(
            "     "
            f"id={row['id']} vm={row['vm_id']} {row['inner_code']} status={row['status']} "
            f"sku={row['sku_count']} qty={row['total_quantity']} "
            f"reason={row['adjust_reason']!r} by={row['adjusted_by']} "
            f"at={row['adjusted_time']} task={row['task_id']} del={row['del_flag']}"
        )
    return len(rows)


async def _open_graph(settings, plan_date: str):  # noqa: ANN001, ANN201 —— 验收脚本
    store = CheckpointStore(CHECKPOINT)
    saver = await store.open()
    deps = SqlRestockDeps(store=SqlPlanStore())
    graph = build_restock_graph(deps=deps, checkpointer=saver, settings=settings)
    return graph, store


async def phase_analyze(args: argparse.Namespace) -> int:
    settings = get_settings()
    graph, store = await _open_graph(settings, args.date)
    config = {"configurable": {"thread_id": _thread(args.date)}}
    try:
        result = await graph.ainvoke(
            new_restock_state(plan_date=args.date, request_id="verify-1-6-analyze"), config
        )
    finally:
        await store.close()

    interrupts = result.get("__interrupt__")
    print(f"[图] 中断处：{'命中人工确认' if interrupts else '无待确认计划（可能没有需补货货道）'}")
    if interrupts:
        payload = interrupts[0].value
        for plan in payload.get("plans", []):
            print(
                f"     待确认 vm={plan['vmId']} {plan['innerCode']} "
                f"状态={plan['status']} 货道={plan['skuCount']} 建议总量={plan['totalQuantity']} "
                f"接单人={plan['assigneeName']}"
            )
            for item in plan["items"]:
                print(
                    f"        - {item['channelCode']} 现库存={item['currentQuantity']}/"
                    f"{item['maxCapacity']} 建议={item['suggestedQuantity']} "
                    f"优先级={item['priority']} 依据={item['reason'][:70]}…"
                )
    rows = await _print_rows(args.date)
    print(f"[结论] 分析阶段：中断={'是' if interrupts else '否'}，库中计划行={rows}")
    return 0 if (interrupts and rows) else 1


async def phase_resume(args: argparse.Namespace) -> int:
    settings = get_settings()
    graph, store = await _open_graph(settings, args.date)
    config = {"configurable": {"thread_id": _thread(args.date)}}
    decision: dict[str, object] = {
        "vmId": args.vm_id,
        "action": args.action,
        "reason": args.reason,
        "userId": args.user_id,
    }
    if args.action == "adjust":
        decision["items"] = [
            {"channelCode": args.channel_code, "suggestedQuantity": args.quantity}
        ]
    elif args.action == "assign":
        decision["assigneeId"] = args.assignee_id
        decision["assigneeName"] = args.assignee_name
    try:
        result = await graph.ainvoke(Command(resume=[decision]), config)
    finally:
        await store.close()

    print(f"[图] 决策={args.action} vm={args.vm_id}")
    for plan in result.get("plans", []):
        print(
            f"     结果状态 vm={plan['vm_id']} 状态={plan['status']} "
            f"总量={sum(i['suggested_quantity'] for i in plan['items'])}"
        )
    print(f"[图] created_tasks={result.get('created_tasks')}")
    print(f"[图] failures={result.get('failures')}")
    await _print_rows(args.date)
    return 0 if not result.get("failures") else 1


async def phase_cleanup(args: argparse.Namespace) -> int:
    """软删本次验收的计划行（**不是 DELETE**：AGENTS §7.4，且账号也没有 DELETE 权限）。"""
    sql = "update agent_restock_plan set del_flag = '1', update_by = 'verify-1-6' where plan_date = :d"
    async with get_sessionmaker()() as session:
        await session.execute(text(sql), {"d": args.date})
        await session.commit()
    print("[cleanup] 已软删（del_flag=1）本次验收计划行")
    await _print_rows(args.date)
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()
        print(f"[cleanup] 已删除本地验收检查点 {CHECKPOINT}")
    return 0


async def main(args: argparse.Namespace) -> int:
    date.fromisoformat(args.date)  # 早失败：日期格式错就不该继续
    try:
        if args.phase == "analyze":
            return await phase_analyze(args)
        if args.phase == "resume":
            return await phase_resume(args)
        return await phase_cleanup(args)
    finally:
        await dispose_engine()


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="1-6 补货计划状态机与中断恢复验收")
    parser.add_argument("--phase", choices=["analyze", "resume", "cleanup"], required=True)
    parser.add_argument("--date", default="2026-09-21", help="计划归属日期（thread_id 也由它决定）")
    parser.add_argument("--action", default="skip", choices=["confirm", "adjust", "skip", "assign"])
    parser.add_argument("--reason", default="验收：人工跳过")
    parser.add_argument("--vm-id", type=int, default=80)
    parser.add_argument("--channel-code", default="1-1")
    parser.add_argument("--quantity", type=int, default=3)
    parser.add_argument("--user-id", type=int, default=7)
    parser.add_argument("--assignee-id", type=int, default=10)
    parser.add_argument("--assignee-name", default="孙权")
    return parser.parse_args()


sys.exit(asyncio.run(main(_parse())))
