"""只读工具层的**真实库**抽样与对账（任务 1-1 / 1-2 的验收手段）。

默认不执行（pyproject 的 addopts 排除了 `-m live`）。手动跑法：

    set -a; source ../.env; set +a
    uv run pytest -m live tests/test_read_tools_live.py -v -s

为什么要单独一层：单测只能证明 SQL 形状（禁 date_format、分批、LIMIT），
真正要知道的是**口径与真实数据对得上**（1-1：抽样核对；1-2：与 Java 侧口径对账）。
本机开发库的订单数据是 2023 年 9 月的历史数据（见 docs/ddl/business_tables_survey.md），
因此窗口显式取 2023-09，而不是“近 30 天”（那样会得到空结果却“测试通过”）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.tools import read_tools

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("DKD_AGENT_DB_PASSWORD"),
        reason="需注入 .env（DKD_AGENT_DB_PASSWORD）才能连真实库",
    ),
]


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test() -> AsyncIterator[None]:
    """每个用例后释放连接池。

    为什么必需（Windows + aiomysql 实测踩坑）：`get_engine()` 是 **lru_cache 的进程级引擎**，
    而 pytest-asyncio 每个用例跑在**新的事件循环**里——上一个循环已关闭，缓存的连接在 GC 时
    会报 `AttributeError: 'NoneType' object has no attribute 'send'` / `Event loop is closed`。
    生产环境不受影响（uvicorn 全程一个循环，关停时调 dispose_engine）。
    """
    yield
    await dispose_engine()


# 开发库订单数据的真实区间（见勘查记录），用固定窗口保证可复现
WINDOW_START = datetime(2023, 9, 1)
WINDOW_END = datetime(2023, 9, 16)


async def _sample_inner_codes(limit: int = 3) -> list[str]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "select inner_code from tb_vending_machine "
                "where vm_status = 1 order by id limit :limit"
            ),
            {"limit": limit},
        )
        return [row[0] for row in rows.all()]


async def test_live_machine_profile_sampling():
    codes = await _sample_inner_codes()
    assert codes, "开发库应有运行中设备"

    async with get_sessionmaker()() as session:
        for code in codes:
            profile = await read_tools.get_machine_profile(session, code)
            assert profile is not None
            assert profile.inner_code == code
            assert profile.vm_status == read_tools.VM_STATUS_RUNNING
            print(  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
                f"[sample] {profile.inner_code} node={profile.node_name} "
                f"region={profile.region_name}"
            )


async def test_live_channel_stock_sampling_and_consistency_report():
    codes = await _sample_inner_codes(limit=5)
    async with get_sessionmaker()() as session:
        items = await read_tools.list_channel_stock(session, inner_codes=codes)

    assert items, "按设备编号应能取到货道库存"
    assert all(item.channel_code for item in items)
    assert all(item.current_stock is not None for item in items)

    mismatched = [item for item in items if not item.data_consistent]
    total = len(items)
    print(f"[sample] 样本 {total} 条货道库存，其中两侧档案不一致 {len(mismatched)} 条")  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
    if mismatched:
        first = mismatched[0]
        print(  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
            "  例："
            f"{first.inner_code}/{first.channel_code} "
            f"sku(inv={first.inventory_sku_id}, ch={first.channel_sku_id}) "
            f"cap(channel={first.channel_max_capacity}, inventory={first.inventory_max_stock})"
        )
    # 不断言“必须一致”——本库实测就是不一致（数据质量问题），工具职责是**如实暴露**而不是掩盖
    assert all(isinstance(item.capacity, int) for item in items)


async def test_live_sales_aggregation_reconciles_with_independent_sql():
    """1-2 对账：工具聚合结果 vs 独立写的原始 SQL（同口径 status=2 + 半开区间）。

    注意：Java 侧 `ReportMapper.xml:122-130` 的 `sumRevenueByStatusAndDateRange` **丢了参数绑定**
    （SQL 只有 `where status >= 1`，既无时间窗也无 =2），所以对账基准取
    `ReportServiceImpl.java:28` 的口径定义 + 独立 SQL，而不是那个有缺陷的报表实现。
    """
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "select distinct inner_code from tb_order "
                "where create_time >= :start and create_time < :end limit 50"
            ),
            {"start": WINDOW_START, "end": WINDOW_END},
        )
        codes = [row[0] for row in rows.all()]

    if not codes:
        pytest.skip("该窗口内没有订单数据（开发库为历史数据），跳过对账")

    async with get_sessionmaker()() as session:
        tool_sales = await read_tools.aggregate_channel_sales(
            session, inner_codes=codes, start=WINDOW_START, end=WINDOW_END
        )
        independent = await session.execute(
            text(
                """
                select coalesce(sum(o.amount), 0) as amount, count(*) as cnt
                from tb_order o
                where o.inner_code in :codes
                  and o.create_time >= :start and o.create_time < :end
                  and o.status = :status
                """
            ),
            {
                "codes": tuple(codes),
                "start": WINDOW_START,
                "end": WINDOW_END,
                "status": get_settings().sales_order_status,
            },
        )
        expected = dict(independent.mappings().one())

    tool_amount = sum(item.amount for item in tool_sales)
    tool_count = sum(item.order_count for item in tool_sales)
    print(f"[reconcile] tool: amount={tool_amount} count={tool_count}; sql: {expected}")  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
    assert tool_amount == int(expected["amount"]), "金额必须与独立 SQL 对账一致"
    assert tool_count == int(expected["cnt"]), "订单数必须与独立 SQL 对账一致"

    # 边界回归：把 end 提前一天，结果必须变小或相等（半开区间语义）
    async with get_sessionmaker()() as session:
        narrowed = await read_tools.aggregate_channel_sales(
            session, inner_codes=codes, start=WINDOW_START, end=datetime(2023, 9, 10)
        )
    assert sum(item.amount for item in narrowed) <= tool_amount


async def test_live_inflight_tasks_returns_details():
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "select distinct inner_code from tb_task "
                "where product_type_id = 2 and task_status in (1,2) limit 10"
            )
        )
        codes = [row[0] for row in rows.all()]

    if not codes:
        pytest.skip("当前没有在途补货工单，跳过")

    async with get_sessionmaker()() as session:
        tasks = await read_tools.list_inflight_tasks(session, inner_codes=codes)

    assert tasks, "应能查到在途补货工单"
    assert all(task.task_status in (1, 2) for task in tasks)
    assert all(task.product_type_id == 2 for task in tasks)
    assert len({task.task_id for task in tasks}) == len(tasks), "同一工单不得重复出现"
    print(f"[sample] 在途补货工单 {len(tasks)} 条，首条渠道={tasks[0].channels}")  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
