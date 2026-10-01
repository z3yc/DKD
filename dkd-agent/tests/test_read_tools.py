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
        self.statements: list[Any] = []
        self._rows_by_call = rows_by_call or []
        self._delay = delay

    async def execute(self, stmt: Any, params: dict[str, Any]) -> FakeResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append((str(stmt), dict(params)))
        self.statements.append(stmt)
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


async def test_daily_sales_aggregation_groups_by_day_but_filters_by_range():
    """1-4 的输入查询：分组用 date()，但 WHERE 仍是范围比较（`date()` 只作用在索引筛出的行上）。"""
    session = FakeSession(
        rows_by_call=[
            [
                {
                    "inner_code": "A1",
                    "channel_code": "1-1",
                    "sale_date": date(2023, 9, 11),
                    "order_count": 3,
                    "qty": 4,
                }
            ]
        ]
    )
    rows = await read_tools.aggregate_channel_daily_sales(
        session,
        inner_codes=["A1"],
        start=datetime(2023, 9, 1),
        end=datetime(2023, 9, 16),
    )
    sql = session.calls[0][0].lower()
    assert "date(o.create_time) as sale_date" in sql, "必须按自然日分组（分位数需要逐日样本）"
    assert "group by o.inner_code, o.channel_code, date(o.create_time)" in sql
    assert "group by" in sql and "date_format" not in sql, "分组用 DATE()，不要格式化成字符串"
    # 关键：函数不得出现在 WHERE 里（否则索引失效，survey §2.2 量化证据：扫描行 2 → 800）
    where_clause = sql.split("where", 1)[1].split("group by")[0]
    assert "date(" not in where_clause and "date_format" not in where_clause
    assert "o.create_time >= :start" in where_clause and "o.create_time < :end" in where_clause
    assert "limit :limit" in sql
    # 2026-10-01 修复的真 bug 回归锚点：不过滤 `channel_code is not null` 时，
    # 分组结果会含 channel_code=None 的行 → `ChannelDailySales` 的必填 str 校验失败
    # → 工具直接抛错。本机开发库实测：7 张出货成功订单里 2 张 channel_code 为空（占 29%），
    # 即“只含这类订单的设备”会让 1-4 基线与 06:00 定时任务整个挂掉。
    assert "o.channel_code is not null" in where_clause, (
        "WHERE 必须过滤 channel_code is null（不可归因到货道的行会撞必填校验并带崩整条链路）"
    )
    _, params = session.calls[0]
    assert params["status"] == 2, "销量口径与 1-2 保持一致（出货成功）"
    assert rows[0].qty == 4 and rows[0].sale_date == date(2023, 9, 11)
    assert rows[0].order_count == 3, "单量（order_count）与件量（qty）分开返回，口径不混"


async def test_daily_sales_aggregation_rejects_inverted_window_and_skips_empty_codes():
    session = FakeSession()
    with pytest.raises(ToolError, match="end 必须晚于 start"):
        await read_tools.aggregate_channel_daily_sales(
            session, inner_codes=["A1"], start=datetime(2023, 9, 16), end=datetime(2023, 9, 1)
        )
    assert session.calls == [], "窗口非法时不得发 SQL"

    session2 = FakeSession()
    assert (
        await read_tools.aggregate_channel_daily_sales(
            session2, inner_codes=["  "], start=datetime(2023, 9, 1), end=datetime(2023, 9, 2)
        )
        == []
    )
    assert session2.calls == [], "空设备列表不得发 SQL（避免无谓的全表扫描）"


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
    assert "t.task_status in (__[postcompile_statuses])" in task_sql
    assert session2.statements[0]._bindparams["inner_codes"].expanding is True
    assert session2.statements[0]._bindparams["statuses"].expanding is True
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
    for statement in session.statements:
        for name in ("inner_codes", "region_ids", "statuses"):
            if name in statement._bindparams:
                assert statement._bindparams[name].expanding is True


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


# --------------------------------------------------------------------------------------
# 1-8：接单人分配的两条查询（设备区域 + 同区域运营人员）
# --------------------------------------------------------------------------------------


async def test_list_machine_profiles_uses_expanding_bound_for_in_values():
    session = FakeSession()
    await read_tools.list_machine_profiles(session, inner_codes=["A1", "A2"])
    statement = session.statements[0]
    assert statement._bindparams["inner_codes"].expanding is True


async def test_list_machine_profiles_does_not_filter_vm_status():
    """回归护栏：区域是**档案事实**，不能因为设备此刻不在运营状态就查不到区域。

    若这里筛了 vm_status=1，一台停用设备会被判成“没有区域”→“找不到接单人”，
    于是运营看到“待指派”，而真实原因完全不同（计划本就不该为停用设备生成）。
    """
    rows = [
        {
            "vm_id": 80,
            "inner_code": "A1",
            "addr": "北京",
            "vm_status": 3,
            "node_id": 5,
            "node_name": "五道口",
            "region_id": 3,
            "region_name": "华北",
        }
    ]
    session = FakeSession(rows_by_call=[rows])

    profiles = await read_tools.list_machine_profiles(session, inner_codes=["A1"])

    assert profiles[0].region_id == 3
    sql = session.sql_text.lower()
    # 只选列不筛条件：查询里不得出现 `vm_status =` / `vm_status in` 这类谓词
    assert "vm_status =" not in sql and "vm_status in" not in sql, (
        "不得筛设备状态（区域是档案事实）"
    )
    assert "where v.inner_code in (__[postcompile_inner_codes])" in sql
    assert _tables_in(sql) == {"tb_vending_machine", "tb_node", "tb_region"}


async def test_list_machine_profiles_batches_and_normalizes_codes():
    session = FakeSession()
    await read_tools.list_machine_profiles(session, inner_codes=[" A1 ", "A1", "", "A2"])
    assert len(session.calls) == 1
    assert session.calls[0][1]["inner_codes"] == ("A1", "A2"), "去重去空白后一次批量取"


async def test_list_region_assignees_filters_role_and_status():
    """三个过滤条件缺一不可（与 `EmpController.businessList` 逐条对齐）。"""
    rows = [
        {"emp_id": 6, "user_name": "周晨", "region_id": 1},
        {"emp_id": 7, "user_name": "柯涵", "region_id": 1},
        {"emp_id": 2, "user_name": "李四", "region_id": 2},
    ]
    session = FakeSession(rows_by_call=[rows])

    grouped = await read_tools.list_region_assignees(session, region_ids=[1, 2])

    assert [a.emp_id for a in grouped[1]] == [6, 7]
    assert [a.emp_id for a in grouped[2]] == [2]
    sql, params = session.calls[0]
    assert "role_code = :role_code" in sql and params["role_code"] == "1002", (
        "必须只取运营人员（1003 是维修，派错队列）"
    )
    assert "status = :status" in sql and params["status"] == 1, "停用员工不能派单"
    assert "order by region_id, id" in sql.lower().replace("\n", " "), "确定性顺序"
    assert _tables_in(sql) == {"tb_emp"}


async def test_list_region_assignees_empty_input_does_not_query():
    session = FakeSession()
    assert await read_tools.list_region_assignees(session, region_ids=[]) == {}
    assert session.calls == [], "空入参不得发出无 WHERE 的全表查询"


async def test_list_region_assignees_batches_by_read_batch_size(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_READ_BATCH_SIZE", "2")
    get_settings.cache_clear()
    try:
        session = FakeSession()
        await read_tools.list_region_assignees(session, region_ids=[1, 2, 3, 1])
        assert [call[1]["region_ids"] for call in session.calls] == [(1, 2), (3,)]
    finally:
        get_settings.cache_clear()


async def test_list_region_assignees_skips_unknown_region_without_error():
    """区域没人 → 返回字典里没有该键（由调用方降级为「待指派」），不是抛异常。"""
    session = FakeSession(rows_by_call=[[{"emp_id": 6, "user_name": "周晨", "region_id": 1}]])
    grouped = await read_tools.list_region_assignees(session, region_ids=[1, 99])
    assert grouped.get(99) is None


async def test_assignee_tables_are_readable_but_not_writable():
    """tb_emp 是**只读**白名单表：读得到、写不了（红线 §7.3）。"""
    from app.db import assert_table_allowed

    assert_table_allowed("tb_emp")
    with pytest.raises(TableNotAllowedError, match="业务表"):
        assert_table_allowed("tb_emp", writable=True)


# --------------------------------------------------------------------------------------
# 任务 2-1：异常检测（订单异常 / 库存异常变动）
# --------------------------------------------------------------------------------------


def _order_row(**over: Any) -> dict[str, Any]:
    row = {
        "order_id": 901,
        "order_no": "NO-901",
        "inner_code": "A1000001",
        "channel_code": "5-6",
        "status": 1,
        "pay_status": 1,
        "amount": 500,
        "create_time": datetime(2023, 9, 10, 8, 0),
        "age_minutes": 999,
        "kind": "paid_not_vended",
    }
    row.update(over)
    return row


def _log_row(**over: Any) -> dict[str, Any]:
    row = {
        "log_id": 77,
        "vm_id": 80,
        "inner_code": "A1000001",
        "channel_id": 4732,
        "sku_id": 1,
        "change_type": 1,
        "change_amount": -1,
        "before_stock": 3,
        "after_stock": 2,
        "order_id": None,
        "operator_name": None,
        "create_time": datetime(2023, 9, 10, 8, 0),
        "kind": "deduct_without_order",
    }
    row.update(over)
    return row


async def test_order_anomalies_flag_two_kinds_and_explain_them():
    """两类异常都要被识别，且理由里要带**可复核的数字**（订单号 / 时长 / pay_status）。

    为什么理由必须带数字：异常检测的输出会直接进诊断对话与告警，只说“有异常”等于没说——
    运维无法判断是刚下单的正常中转态还是真的卡单。
    """
    session = FakeSession(
        rows_by_call=[
            [
                _order_row(),
                _order_row(
                    order_id=902, order_no="NO-902", status=3, pay_status=1, kind="vend_failed"
                ),
            ]
        ]
    )
    found = await read_tools.list_order_anomalies(
        session,
        inner_codes=["A1000001"],
        start=datetime(2023, 9, 1),
        end=datetime(2023, 9, 16),
    )

    assert [a.kind for a in found] == ["paid_not_vended", "vend_failed"]
    assert "仍未出货" in found[0].reason and "999" in found[0].reason
    assert "出货失败" in found[1].reason and "pay_status=1" in found[1].reason

    sql = session.sql_text
    assert "date_format" not in sql, "时间过滤必须用范围比较（否则索引列被函数包裹）"
    assert "o.create_time >= :start" in sql and "o.create_time < :end" in sql
    assert "o.status = :paid" in sql and "o.status = :vend_failed" in sql
    # 出货失败必须看 pay_status：已退款的（3）不该再报成异常，否则告警会被噪音淹没
    assert "coalesce(o.pay_status, 0) in (:pay_paid, :pay_refunding)" in sql
    assert _tables_in(sql) == {"tb_order"}
    # 展开参数必须显式声明 expanding（历史踩坑：漏了会在真库上 fail）
    assert session.statements[0]._bindparams["inner_codes"].expanding is True


async def test_order_anomalies_stale_threshold_is_configurable():
    """卡单阈值必须可配且真的进了 SQL 参数（写死会让不同机型要么漏报要么噪音）。"""
    session = FakeSession(rows_by_call=[[]])
    await read_tools.list_order_anomalies(
        session,
        inner_codes=["A1000001"],
        start=datetime(2023, 9, 1),
        end=datetime(2023, 9, 16),
        stale_minutes=45,
    )
    params = session.calls[0][1]
    assert "stale_before" in params


async def test_order_anomalies_reject_bad_inputs():
    session = FakeSession()
    with pytest.raises(ToolError):
        await read_tools.list_order_anomalies(
            session,
            inner_codes=["A"],
            start=datetime(2023, 9, 16),
            end=datetime(2023, 9, 1),
        )
    with pytest.raises(ToolError):
        await read_tools.list_order_anomalies(
            session,
            inner_codes=["A"],
            start=datetime(2023, 9, 1),
            end=datetime(2023, 9, 16),
            stale_minutes=-1,
        )
    assert (
        await read_tools.list_order_anomalies(
            session, inner_codes=[], start=datetime(2023, 9, 1), end=datetime(2023, 9, 16)
        )
        == []
    )
    assert session.calls == [], "空入参/非法窗口不得产生任何 SQL"


async def test_inventory_anomalies_cover_three_kinds():
    """三种库存异常都要被识别；理由必须带数字（变动前后 / 日志 id / 操作人）。

    本机 `tb_inventory_log` 是**空表**，所以这里的“构造样本”就是 2-1 验收标准要求的验证方式；
    真库上只能验证 SQL 可执行（见 `test_read_tools_live.py`）。
    """
    session = FakeSession(
        rows_by_call=[
            [
                _log_row(),
                _log_row(
                    log_id=78,
                    change_amount=-2,
                    before_stock=5,
                    after_stock=5,
                    kind="ledger_mismatch",
                ),
                _log_row(
                    log_id=79,
                    change_type=3,
                    change_amount=-1,
                    before_stock=2,
                    after_stock=1,
                    kind="unexplained_deduction",
                ),
            ]
        ]
    )
    found = await read_tools.list_inventory_anomalies(
        session,
        inner_codes=["A1000001"],
        start=datetime(2023, 9, 1),
        end=datetime(2023, 9, 16),
    )

    assert [a.kind for a in found] == [
        "deduct_without_order",
        "ledger_mismatch",
        "unexplained_deduction",
    ]
    assert "无单扣减" in found[0].reason and "日志 id=77" in found[0].reason
    assert "自相矛盾" in found[1].reason and "变动前 5" in found[1].reason
    assert "未填备注" in found[2].reason

    sql = session.sql_text
    assert "date_format" not in sql
    assert "left join tb_order" in sql, "order_id 未命中也要算无单扣减（脏数据同样危险）"
    assert "join tb_vending_machine" in sql, "按 inner_code 过滤必须先映射 vm_id"
    # 账实不符必须 NULL 安全（三列都可能为空，空值不该被当成“对得上”）
    assert "l.before_stock is not null" in sql
    assert _tables_in(sql) == {"tb_inventory_log", "tb_vending_machine", "tb_order"}


async def test_inventory_anomalies_reject_bad_window():
    session = FakeSession()
    with pytest.raises(ToolError):
        await read_tools.list_inventory_anomalies(
            session,
            inner_codes=["A"],
            start=datetime(2023, 9, 16),
            end=datetime(2023, 9, 1),
        )
    assert session.calls == []


# --------------------------------------------------------------------------------------
# 任务 2-2：历史维修工单规则检索（降级为规则库，不建向量库）
# --------------------------------------------------------------------------------------


def _case_row(**over: Any) -> dict[str, Any]:
    row = {
        "task_id": 527,
        "task_code": "202303240001",
        "inner_code": "A1000001",
        "task_status": 4,
        "user_name": "张辽",
        "phenomenon": "111",
        "create_time": datetime(2023, 3, 24, 10, 41),
        "update_time": datetime(2023, 3, 24, 10, 41),
        "region_id": 1,
        "addr": "河南省-洛阳市-龙门石窟",
        "region_name": "洛阳市",
    }
    row.update(over)
    return row


def _profile_row(**over: Any) -> dict[str, Any]:
    """`get_machine_profile` 需要的完整行（search_history_cases 会先取设备档案）。"""
    row = {
        "vm_id": 80,
        "inner_code": "A1000001",
        "addr": "河南省-洛阳市-龙门石窟",
        "vm_status": 1,
        "node_id": 2,
        "node_name": "龙门石窟游客中心",
        "region_id": 1,
        "region_name": "洛阳市",
    }
    row.update(over)
    return row


def test_phenomenon_usable_rejects_process_notes_and_placeholders():
    """`phenomenon_usable` 是 2-2 的核心诚实点：把“111”当故障现象讲出来就是编造证据。"""
    assert not read_tools.phenomenon_usable(None)
    assert not read_tools.phenomenon_usable("")
    assert not read_tools.phenomenon_usable("111"), "纯数字是占位符"
    assert not read_tools.phenomenon_usable("111-222"), "纯数字+分隔符同样是占位符"
    assert not read_tools.phenomenon_usable("后台工作人员取消"), "流程备注不是故障现象"
    assert not read_tools.phenomenon_usable("加急"), "口头禅不是故障现象"
    assert not read_tools.phenomenon_usable("不出货"), "太短（<8 字）无法区分故障"
    assert read_tools.phenomenon_usable("货道 3-6 付款后不转动，退币正常")


def _cases_sql_calls(session: FakeSession) -> list[tuple[str, dict]]:
    """从调用序列里挑出“候选集”那条 SQL（避免用固定下标，改查询顺序时测试不会假绿）。"""
    return [(sql, params) for sql, params in session.calls if "as phenomenon" in sql]


async def test_history_cases_ladder_precedence_and_no_evidence_hint():
    """三级降级顺序 + 空命中的“无证据”提示必须一起返回（空列表区分不了“没查”与“没案例”）。

    ⚠️ 顺便锁住一个真库撞出来的坑：**不得用 inner_code 做列对列 join**——
    两表 collation 不同（utf8mb4_unicode_ci vs utf8mb4_0900_ai_ci），会 ERROR 1267（FIX-25）。
    所以断言“候选集那条 SQL 里不出现 join tb_vending_machine”。
    """
    session = FakeSession(
        rows_by_call=[
            [_profile_row()],  # 1 设备档案
            [{"vm_type_id": 1, "region_id": 1, "vm_type_name": "饮料机"}],  # 2 型号/区域
            [{"inner_code": "A1000001"}, {"inner_code": "5cy5BdUs"}],  # 3 同型号兄弟设备
            [  # 4 候选集：同设备 1 + 同型号 1 + 同区域 1
                _case_row(),
                _case_row(
                    task_id=530,
                    inner_code="5cy5BdUs",
                    phenomenon="货道 2-1 不出货",
                    create_time=datetime(2023, 3, 25, 9, 0),
                ),
                _case_row(
                    task_id=531,
                    inner_code="Ut548Hpf",
                    phenomenon="后台工作人员取消",
                    create_time=datetime(2023, 3, 26, 9, 0),
                ),
            ],
            [  # 5 命中设备的型号信息
                {"inner_code": "A1000001", "vm_type_id": 1, "vm_type_name": "饮料机"},
                {"inner_code": "5cy5BdUs", "vm_type_id": 1, "vm_type_name": "饮料机"},
                {"inner_code": "Ut548Hpf", "vm_type_id": 4, "vm_type_name": "果蔬机"},
            ],
            [],  # 6 订单异常
            [],  # 7 库存异常
        ]
    )
    result = await read_tools.search_history_cases(
        session,
        inner_code="A1000001",
        start=datetime(2000, 1, 1),
        end=datetime(2030, 1, 1),
    )

    assert [c.matched_by for c in result.cases] == [
        "same_device",
        "same_model",
        "same_region",
    ], "排序必须是设备 → 型号 → 区域"
    assert result.matched_by_counts == {"same_device": 1, "same_model": 1, "same_region": 1}
    assert result.total_cases == 3
    # 可用案例只有 1 条（'货道 2-1 不出货' 是 8 字且非流程话术）；占位/流程备注必须为 False
    assert result.usable_case_count == 1
    assert result.cases[0].phenomenon_usable is False, "'111' 不得被当成故障现象"
    # 置信度上限被“现象不可用”锁住：同设备但描述不可用 → low，且给出依据
    assert result.cases[0].confidence == "low"
    assert "描述不是故障现象" in result.cases[0].confidence_basis
    assert result.cases[1].confidence == "medium", "同型号 + 可用描述 → medium"
    assert result.cases[2].confidence == "low", "仅同区域 → low"
    assert result.cases[2].vm_type_name == "果蔬机", (
        "命中设备的型号信息要如实带上（不是目标的型号）"
    )
    assert result.no_evidence_hint is None, "有可用案例时不该出现“无证据”提示"
    assert "规则库检索（非语义检索）" in result.caliber
    assert "phenomenon_usable" in result.caliber

    # 查询条数固定（防止退化成 N+1），且候选集只有一条
    assert len(session.calls) == 7, (
        "设备档案 + 型号/区域 + 兄弟设备 + 候选集 + 命中型号 + 2 个异常查询 = 7 条 SQL"
        f"（固定条数，防止 N+1），实际 {len(session.calls)}"
    )
    cases_calls = _cases_sql_calls(session)
    assert len(cases_calls) == 1, "候选集必须在一条 SQL 里拿到"
    assert cases_calls[0][1]["repair_type_id"] == read_tools.REPAIR_PRODUCT_TYPE_ID == 3
    assert "join tb_vending_machine" not in cases_calls[0][0], (
        "候选集不得 join tb_vending_machine：两表 inner_code 的 collation 不一致（FIX-25）"
    )
    # 注意：expanding 参数在 str(TextClause) 里渲染成 __[POSTCOMPILE_x]（不是 :x）——
    # 这一点在 3-9 的 EXPLAIN 演练里踩过，见排期 §十 10.6 坑 10。
    assert "t.inner_code in (__[POSTCOMPILE_case_codes])" in cases_calls[0][0]
    assert session.statements[3]._bindparams["case_codes"].expanding is True


async def test_history_cases_no_case_returns_explicit_hint():
    """一条也没有时必须给出“无证据”提示——而不是让调用方把空列表当成“不需要检查”。"""
    session = FakeSession(
        rows_by_call=[
            [_profile_row()],
            [{"vm_type_id": 2, "region_id": 9, "vm_type_name": "综合机"}],
            [{"inner_code": "A1000001"}],
            [],
            [],
            [],
            [],
        ]
    )
    result = await read_tools.search_history_cases(
        session, inner_code="A1000001", start=datetime(2000, 1, 1), end=datetime(2030, 1, 1)
    )
    assert result.cases == []
    assert result.total_cases == 0
    assert result.no_evidence_hint and "没有任何历史维修工单" in result.no_evidence_hint
    assert "不要拿历史案例凑证据" in result.no_evidence_hint


async def test_history_cases_all_unusable_returns_conservative_hint():
    """查到工单但一条都不可用（本机真实形态）→ 必须明说“不可据此下诊断结论”。"""
    session = FakeSession(
        rows_by_call=[
            [_profile_row()],
            [{"vm_type_id": 1, "region_id": 1, "vm_type_name": "饮料机"}],
            [{"inner_code": "A1000001"}],
            [_case_row()],
            [{"inner_code": "A1000001", "vm_type_id": 1, "vm_type_name": "饮料机"}],
            [],
            [],
        ]
    )
    result = await read_tools.search_history_cases(
        session, inner_code="A1000001", start=datetime(2000, 1, 1), end=datetime(2030, 1, 1)
    )
    assert result.total_cases == 1 and result.usable_case_count == 0
    assert result.no_evidence_hint and "没有一条可用作故障现象" in result.no_evidence_hint
    assert "只能当“该设备/该型号有过维修记录”这一点用" in result.no_evidence_hint


async def test_history_cases_reject_bad_inputs():
    session = FakeSession()
    with pytest.raises(ToolError):
        await read_tools.search_history_cases(
            session, inner_code="A", start=datetime(2030, 1, 1), end=datetime(2000, 1, 1)
        )
    with pytest.raises(ToolError):
        await read_tools.search_history_cases(
            session, start=datetime(2000, 1, 1), end=datetime(2030, 1, 1)
        )
    assert session.calls == [], "非法入参不得产生任何 SQL"


async def test_history_cases_rejects_non_whitelist_table():
    """取表必须先过白名单（2-2 也一样，不能因为“日志表”就免检）。"""
    with pytest.raises(TableNotAllowedError):
        await read_tools._fetch_all(FakeSession(), "select 1", {}, tables=("tb_secret_table",))


async def test_history_cases_default_window_is_bounded_and_ordered():
    """默认窗口必须有界（单主库上禁止无窗口分析查询），且与 1-2 用同一套半开区间算法。"""
    start, end = read_tools.default_case_window(days=1095, today=date(2026, 10, 1))
    assert (end - start).days == 1095
    assert start < end
    assert end.date() == date(2026, 10, 1)
