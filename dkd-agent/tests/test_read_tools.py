"""只读工具层单测（排期任务 1-1 / 1-2）。

原则（AGENTS §8）：
- **不连 MySQL**：用假会话断言 SQL 形状与调用次数；真实库抽样放在
  `test_read_tools_live.py`（-m live）；
- 拒绝路径优先：越权表白名单、超时熔断、空入参、窗口非法、行数超限都必须**真的被拒**。

为什么专门断言“SQL 形状”：1-2 的验收要点是范围比较 + 分批 + 超时，这三条都无法靠“结果对不对”看出来
（小数据量下 `date_format` 也能跑出正确结果，只是索引失效）。所以要把写法本身钉进测试。
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Any

import pytest

from app.config import get_settings
from app.db import TableNotAllowedError
from app.tools import read_tools
from app.tools.read_tools import (
    ChannelSales,
    ChannelStockItem,
    InflightTask,
    QueryTimeoutError,
    ToolError,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    """记录每次执行的 SQL 与参数；可按调用序返回预置行。"""

    def __init__(
        self, rows_by_call: list[list[dict[str, Any]]] | None = None, delay: float = 0.0
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows_by_call = rows_by_call or []
        self._delay = delay

    async def execute(self, stmt: Any, params: dict[str, Any]) -> FakeResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append((str(stmt), dict(params)))
        index = len(self.calls) - 1
        rows = self._rows_by_call[index] if index < len(self._rows_by_call) else []
        return FakeResult(rows)

    @property
    def sql_text(self) -> str:
        return " ".join(sql for sql, _ in self.calls)


def _tables_in(sql: str) -> set[str]:
    return set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", sql.lower()))


# --------------------------------------------------------------------------------------
# 1-2 核心：SQL 形状（范围比较 / 口径 / LIMIT）
# --------------------------------------------------------------------------------------


async def test_sales_aggregation_uses_range_comparison_not_date_format():
    session = FakeSession()
    await read_tools.aggregate_channel_sales(
        session,
        inner_codes=["A1"],
        start=datetime(2023, 9, 1),
        end=datetime(2023, 9, 16),
    )
    sql = session.sql_text.lower()
    assert "date_format" not in sql, "禁用函数包裹索引列（OrderMapper.xml:44 的反例）"
    assert "year(" not in sql and "month(" not in sql, "同样禁止其它包裹索引列的函数"
    assert "o.create_time >= :start" in sql and "o.create_time < :end" in sql, (
        "必须是半开区间范围比较"
    )
    assert "limit :limit" in sql, "必须带 LIMIT 兜底（禁止无上限扫描）"
    _, params = session.calls[0]
    assert params["status"] == 2, "销量口径必须是 status=2（出货成功，ReportServiceImpl.java:28）"
    assert params["start"] == datetime(2023, 9, 1) and params["end"] == datetime(2023, 9, 16)


async def test_inventory_and_task_queries_keep_limit_and_join_keys():
    session = FakeSession()
    await read_tools.list_channel_stock(session, inner_codes=["A1"])
    stock_sql = session.calls[0][0].lower()
    assert "join tb_channel c on c.id = i.channel_id" in stock_sql
    assert "join tb_vending_machine v on v.id = i.vm_id" in stock_sql
    assert "limit :limit" in stock_sql

    session2 = FakeSession()
    await read_tools.list_inflight_tasks(session2, inner_codes=["A1"])
    task_sql = session2.calls[0][0].lower()
    assert "left join tb_task_details d on d.task_id = t.task_id" in task_sql
    assert "t.task_status in :statuses" in task_sql
    assert session2.calls[0][1]["statuses"] == (1, 2), "在途 = 待接单 + 进行中"


# --------------------------------------------------------------------------------------
# 分批 / 超时 / 限额
# --------------------------------------------------------------------------------------


async def test_channel_stock_is_batched_by_configured_size(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_READ_BATCH_SIZE", "50")
    get_settings.cache_clear()
    codes = [f"V{i:03d}" for i in range(120)]
    session = FakeSession()

    await read_tools.list_channel_stock(session, inner_codes=codes)

    assert len(session.calls) == 3, "120 个设备 / 每批 50 → 3 批"
    sizes = [len(params["inner_codes"]) for _, params in session.calls]
    assert sizes == [50, 50, 20]
    get_settings.cache_clear()


async def test_sales_aggregation_merges_all_batches(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_READ_BATCH_SIZE", "2")
    get_settings.cache_clear()
    row = {
        "inner_code": "A1",
        "channel_code": "1-1",
        "sku_id": 1,
        "order_count": 3,
        "qty": 3,
        "amount": 9,
        "last_order_at": None,
    }
    session = FakeSession(rows_by_call=[[row], [row]])

    sales = await read_tools.aggregate_channel_sales(
        session,
        inner_codes=["A1", "A2", "A3"],
        start=datetime(2023, 9, 1),
        end=datetime(2023, 9, 2),
    )

    assert len(session.calls) == 2, "3 个设备 / 每批 2 → 2 批"
    assert len(sales) == 2, "分批结果必须合并返回"
    assert all(isinstance(item, ChannelSales) for item in sales)
    get_settings.cache_clear()


async def test_query_timeout_is_fused(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_READ_QUERY_TIMEOUT_S", "0.05")
    get_settings.cache_clear()
    session = FakeSession(delay=0.3)

    with pytest.raises(QueryTimeoutError, match="熔断"):
        await read_tools.list_channel_stock(session, inner_codes=["A1"])
    get_settings.cache_clear()


async def test_row_limit_is_enforced(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_READ_ROW_LIMIT", "1")
    get_settings.cache_clear()
    rows = [
        {
            "vm_id": 1,
            "inner_code": "A1",
            "channel_id": 1,
            "channel_code": "1-1",
            "inventory_sku_id": 1,
            "channel_sku_id": 1,
            "current_stock": 1,
            "min_stock": 1,
            "is_alert": 0,
            "channel_max_capacity": 10,
            "inventory_max_stock": 10,
            "last_supply_time": None,
            "last_sell_time": None,
        },
        {
            "vm_id": 1,
            "inner_code": "A1",
            "channel_id": 2,
            "channel_code": "1-2",
            "inventory_sku_id": 2,
            "channel_sku_id": 2,
            "current_stock": 1,
            "min_stock": 1,
            "is_alert": 0,
            "channel_max_capacity": 10,
            "inventory_max_stock": 10,
            "last_supply_time": None,
            "last_sell_time": None,
        },
    ]
    session = FakeSession(rows_by_call=[rows])

    with pytest.raises(ToolError, match="行数上限"):
        await read_tools.list_channel_stock(session, inner_codes=["A1"])
    get_settings.cache_clear()


# --------------------------------------------------------------------------------------
# 拒绝路径（该拒的必须拒）
# --------------------------------------------------------------------------------------


async def test_non_whitelisted_table_is_rejected_before_sql(monkeypatch):
    """把 tb_order 从白名单摘掉后，聚合查询必须在**发 SQL 之前**被拒。"""
    monkeypatch.setenv("DKD_AGENT_TABLE_WHITELIST", "tb_inventory,tb_channel,tb_vending_machine")
    get_settings.cache_clear()
    session = FakeSession()

    with pytest.raises(TableNotAllowedError, match="不在只读白名单"):
        await read_tools.aggregate_channel_sales(
            session, inner_codes=["A1"], start=datetime(2023, 9, 1), end=datetime(2023, 9, 2)
        )
    assert session.calls == [], "拒绝必须发生在访问数据库之前"
    get_settings.cache_clear()


async def test_every_tool_only_touches_whitelisted_tables():
    """护栏的元测试：所有工具 SQL 里出现的表都必须在默认白名单内。"""
    session = FakeSession()
    await read_tools.get_machine_profile(session, "A1")
    await read_tools.list_operating_machines(session)
    await read_tools.list_channel_stock(session, inner_codes=["A1"])
    await read_tools.aggregate_channel_sales(
        session, inner_codes=["A1"], start=datetime(2023, 9, 1), end=datetime(2023, 9, 2)
    )
    await read_tools.list_inflight_tasks(session, inner_codes=["A1"])

    whitelist = get_settings().whitelist_tables
    used = _tables_in(session.sql_text)
    assert used, "至少要解析出表名，否则这个元测试是空转"
    assert used <= whitelist, f"工具访问了白名单外的表：{used - whitelist}"


async def test_empty_and_invalid_input_are_rejected():
    session = FakeSession()
    assert await read_tools.list_channel_stock(session, inner_codes=[]) == []
    assert await read_tools.list_inflight_tasks(session, inner_codes=["", "  "]) == []
    assert (
        await read_tools.aggregate_channel_sales(
            session, inner_codes=[], start=datetime(2023, 9, 1), end=datetime(2023, 9, 2)
        )
        == []
    )
    assert session.calls == [], "空入参不得产生任何 SQL"

    with pytest.raises(ToolError, match="不能为空"):
        await read_tools.get_machine_profile(session, "  ")
    with pytest.raises(ToolError, match="end 必须晚于 start"):
        await read_tools.aggregate_channel_sales(
            session, inner_codes=["A1"], start=datetime(2023, 9, 2), end=datetime(2023, 9, 1)
        )
    with pytest.raises(ToolError, match="days 必须为正整数"):
        read_tools.default_sales_window(days=0)


# --------------------------------------------------------------------------------------
# 业务语义（口径与去重）
# --------------------------------------------------------------------------------------


def test_capacity_prefers_channel_profile_and_flags_mismatch():
    item = ChannelStockItem(
        vm_id=1,
        inner_code="A1",
        channel_id=1,
        channel_code="1-1",
        inventory_sku_id=1,
        channel_sku_id=9,
        current_stock=2,
        min_stock=1,
        is_alert=True,
        channel_max_capacity=10,
        inventory_max_stock=4,
    )
    assert item.capacity == 10, "容量以货道档案为准（模块 docstring 有 file:line 依据）"
    assert item.data_consistent is False, "两侧档案不一致必须暴露出来"

    consistent = item.model_copy(update={"channel_sku_id": 1, "inventory_max_stock": 10})
    assert consistent.data_consistent is True


async def test_inflight_tasks_merge_detail_channels():
    rows = [
        {
            "task_id": 7,
            "task_code": "T7",
            "inner_code": "A1",
            "task_status": 1,
            "product_type_id": 2,
            "user_id": 3,
            "create_time": None,
            "channel_code": "1-1",
        },
        {
            "task_id": 7,
            "task_code": "T7",
            "inner_code": "A1",
            "task_status": 1,
            "product_type_id": 2,
            "user_id": 3,
            "create_time": None,
            "channel_code": "1-2",
        },
        {
            "task_id": 8,
            "task_code": "T8",
            "inner_code": "A1",
            "task_status": 2,
            "product_type_id": 2,
            "user_id": 3,
            "create_time": None,
            "channel_code": None,
        },
    ]
    session = FakeSession(rows_by_call=[rows])

    tasks = await read_tools.list_inflight_tasks(session, inner_codes=["A1"])

    by_id = {task.task_id: task for task in tasks}
    assert set(by_id) == {7, 8}, "同一工单的多条明细必须合并成一条"
    assert by_id[7].channels == ["1-1", "1-2"]
    assert by_id[8].channels == []
    assert isinstance(by_id[7], InflightTask)


def test_default_sales_window_is_half_open():
    start, end = read_tools.default_sales_window(days=7, today=date(2026, 9, 21))
    assert start == datetime(2026, 9, 14) and end == datetime(2026, 9, 21), (
        "半开区间 [today-7, today)"
    )
