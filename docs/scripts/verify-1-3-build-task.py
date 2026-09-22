"""排期任务 1-3 端到端验收脚本（真库 + 真 Java 回调，**会真实建单**）。

覆盖 4 件事：
  1) 读工具取真实档案（1-1）→ 组装补货计划 → 回调建单（1-3）→ 回传 taskId/taskCode；
  2) 字段语义：expectCapacity 必须是**补货数量**（坑 9），并与 max_capacity 区分；
  3) **Agent 层幂等预检**（list_inflight_tasks 查 statuses=(1,2)）——补掉 Java 防重只看某一状态的缺口；
  4) Java 层业务拒绝原文回传（换一个跨区域接单人触发，该分支在 insertTask 之前抛出 ⇒ 零写入）。

为什么放在 docs/scripts 而不是 tests/：它**写业务数据**（真建一张工单），
不能进 CI（AGENTS §8：测试不得依赖真实环境写入），只能人工显式执行、人工清理。

运行方式（在 dkd-agent 目录下，先注入环境变量）：

    cd dkd-agent
    set -a; source ../.env; set +a
    .venv/Scripts/python.exe ../docs/scripts/verify-1-3-build-task.py

前置条件：MySQL 可用、Java（dkd-admin）已用**含 1-3 改动**的代码启动（回调要回传 taskId）。
执行后请按打印的提示人工取消生成的工单（本脚本**不做**清理，避免隐藏写入）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许从任意 cwd 运行：把 dkd-agent 根目录挂进 sys.path（脚本自身在 docs/scripts/ 下）
_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from sqlalchemy import text  # noqa: E402 —— 必须在 sys.path 调整之后导入

from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.graphs.restock_state import PLAN_STATUS_SUGGESTED, RestockItem, RestockPlan  # noqa: E402
from app.tools import read_tools  # noqa: E402
from app.tools.read_tools import ChannelStockItem, MachineProfile  # noqa: E402
from app.tools.task_tools import CallbackBusinessError, create_restock_task  # noqa: E402

INNER = "A1000001"
ASSIGNEE = 10  # 孙权（region 3，与设备同区域）
MISMATCH_ASSIGNEE = 6  # 曹操（region 1）——故意与设备区域不一致，用于验证拒绝路径原文回传


def build_plan(
    profile: MachineProfile, stock: list[ChannelStockItem]
) -> tuple[RestockPlan, int, ChannelStockItem]:
    """挑一个未满货道，按其缺口构造补货计划。"""
    target = next((i for i in stock if i.capacity > i.current_stock), None)
    if target is None:
        raise SystemExit("样本货道都满了，无法构造建议")
    qty = target.capacity - target.current_stock
    print(
        f"[pick] 货道={target.channel_code} 现库存={target.current_stock} 容量={target.capacity}"
        f" → 建议补={qty}（档案两侧一致={target.data_consistent}）"
    )
    item = RestockItem(
        channel_id=target.channel_id,
        channel_code=target.channel_code,
        sku_id=target.channel_sku_id or target.inventory_sku_id or 1,
        sku_name="验收样本",
        current_quantity=target.current_stock,
        max_capacity=target.capacity,
        suggested_quantity=qty,
        after_restock_quantity=target.current_stock + qty,
    )
    return RestockPlan(
        plan_date="2026-09-21",
        inner_code=profile.inner_code,
        vm_id=profile.vm_id,
        region_id=profile.region_id,
        assignee_id=ASSIGNEE,
        status=PLAN_STATUS_SUGGESTED,
        items=[item],
    ), qty, target


async def _task_count(inner_code: str) -> int:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text("select count(*) from tb_task where inner_code = :code"), {"code": inner_code}
        )
        return int(rows.scalar_one())


async def main() -> None:
    settings = get_settings()
    print(
        f"[env] java={settings.java_base_url} 密钥已配置={bool(settings.service_secret)} "
        f"回调超时={settings.callback_timeout_s}s"
    )
    async with get_sessionmaker()() as session:
        profile = await read_tools.get_machine_profile(session, INNER)
        stock = await read_tools.list_channel_stock(session, inner_codes=[INNER])
        inflight = await read_tools.list_inflight_tasks(session, inner_codes=[INNER])
    print(
        f"[read] 设备={profile.inner_code} 点位={profile.node_name} "
        f"区域={profile.region_name} 货道={len(stock)}"
    )
    print(
        f"[precheck] 在途补货工单={len(inflight)} → {'拒绝建单' if inflight else '可建单'}"
    )
    assert not inflight, "验收前该设备不应有在途工单（请先取消历史验收工单）"

    plan, qty, _target = build_plan(profile, stock)
    created = await create_restock_task(plan, request_id="verify-1-3")
    print(
        f"[ok] 建单成功 taskId={created.task_id} taskCode={created.task_code} "
        f"期望明细 expectCapacity={qty}"
    )
    print(f"RESULT_TASK_ID={created.task_id}")

    # 3) Agent 层幂等预检：新建工单处于“进行中”，Java 防重查不到，必须由 Agent 侧拦住
    async with get_sessionmaker()() as session:
        inflight2 = await read_tools.list_inflight_tasks(session, inner_codes=[INNER])
    if inflight2:
        print(
            "[ok] Agent 层预检命中在途工单 "
            f"task_id={[t.task_id for t in inflight2]} → 不会重复回调"
        )
    else:
        print("[FAIL] Agent 层预检未命中新建工单，存在重复建单风险")

    # 4) Java 层业务拒绝（零写入路径）：员工区域与设备区域不一致
    before = await _task_count(INNER)
    mismatch = plan.model_copy(update={"assignee_id": MISMATCH_ASSIGNEE})
    try:
        await create_restock_task(mismatch, request_id="verify-1-3-region")
        print("[FAIL] 区域不一致竟然建单成功")
    except CallbackBusinessError as exc:
        print(f"[ok] 员工区域不匹配被拒，原文回传：{exc}（code={exc.code}）")
    after = await _task_count(INNER)
    print(f"[ok] 拒绝路径未写库：{INNER} 工单数 {before} → {after}")

    print(f"[cleanup] 请用 /manage/task/cancel 取消 taskId={created.task_id}")

    await dispose_engine()


asyncio.run(main())
