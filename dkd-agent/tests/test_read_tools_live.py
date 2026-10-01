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


# --------------------------------------------------------------------------------------
# 任务 2-1：异常检测在真库上的可执行性与“不误报”
# --------------------------------------------------------------------------------------
#
# 这一层能证明的和不能证明的，必须分清（AGENTS §9.3）：
#   ✅ 能证明：SQL 在真库上可执行（列名/函数/展开参数都对）、真数据上**识别到了**已知异常形态、
#      正常订单（status=2 出货成功）**没有**被误报；
#   ❌ 不能证明：`tb_inventory_log` 的异常识别——**该表本机 0 行**，只能靠构造样本（单测）。
#      所以下面的库存用例只断言“可执行 + 空结果”，并在输出里写明原因。


async def test_live_order_anomalies_finds_stale_paid_orders_without_false_positives():
    """真库：卡单（status=1，2023 年历史数据）会被识别；出货成功的订单不得被误报。"""
    codes = await _sample_inner_codes(limit=10)
    async with get_sessionmaker()() as session:
        found = await read_tools.list_order_anomalies(
            session,
            inner_codes=codes,
            start=WINDOW_START,
            end=WINDOW_END,
            stale_minutes=1,
        )

    print(f"[anomaly] 订单异常 {len(found)} 条；kind 分布={sorted({a.kind for a in found})}")  # noqa: T201
    assert found, "本库 2023-09 有 10 张 status=1（支付完成未出货）的历史订单，应被识别"
    assert all(a.kind in ("paid_not_vended", "vend_failed") for a in found)
    assert all(a.reason for a in found), "每条异常都必须带可复核的理由"

    # 反向断言：出货成功（status=2）的订单绝不能被算成异常
    async with get_sessionmaker()() as session:
        ok_rows = await session.execute(
            text(
                "select count(*) from tb_order where status = 2 "
                "and create_time >= :s and create_time < :e"
            ),
            {"s": WINDOW_START, "e": WINDOW_END},
        )
        ok_count = int(ok_rows.scalar() or 0)
    flagged_ids = {a.order_id for a in found}
    async with get_sessionmaker()() as session:
        ok_id_rows = await session.execute(
            text(
                "select id from tb_order where status = 2 "
                "and create_time >= :s and create_time < :e"
            ),
            {"s": WINDOW_START, "e": WINDOW_END},
        )
        ok_ids = {int(r[0]) for r in ok_id_rows.all()}
    print(f"[anomaly] 同期出货成功订单 {ok_count} 条，被误报 {len(flagged_ids & ok_ids)} 条")  # noqa: T201
    assert not (flagged_ids & ok_ids), "出货成功的订单不得出现在异常结果里（误报比漏报更伤信任）"


async def test_live_inventory_anomalies_executes_on_empty_table():
    """真库：`tb_inventory_log` 当前 0 行 → 只能验证 SQL 可执行且返回空（不得报错）。"""
    codes = await _sample_inner_codes(limit=5)
    async with get_sessionmaker()() as session:
        found = await read_tools.list_inventory_anomalies(
            session,
            inner_codes=codes,
            start=datetime(2000, 1, 1),
            end=datetime.now(),
        )
    print(f"[anomaly] 库存异常 {len(found)} 条（该表本机 0 行，构造样本见单测）")  # noqa: T201
    assert found == [], "空表应返回空列表；有数据时也由同一套 SQL 判定"


# --------------------------------------------------------------------------------------
# 任务 2-2：历史维修工单规则检索（真库）
# --------------------------------------------------------------------------------------


async def test_live_history_cases_are_real_and_honest():
    """真库：结果必须逐字来自 DB；且本机“可用案例 = 0”时必须给保守提示。

    这条用例同时防住一个**已经真实发生过的错误**：2-7 抽样脚本曾把 `product_type_id=1`
    （投放工单）当成维修工单，于是把 8 条投放工单当作“故障案例”。所以这里除了“不得编造”，
    还要断言**返回的每一条都是真正的维修工单（product_type_id = 3）**。
    """
    from sqlalchemy import bindparam

    codes = await _sample_inner_codes(limit=1)
    async with get_sessionmaker()() as session:
        result = await read_tools.search_history_cases(
            session, inner_code=codes[0], start=datetime(2000, 1, 1), end=datetime.now()
        )

    print(  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
        f"[cases] 设备 {codes[0]}：命中 {result.total_cases} 条，"
        f"可用 {result.usable_case_count} 条，匹配分布={result.matched_by_counts}"
    )

    if result.cases:
        ids = tuple(case.task_id for case in result.cases)
        async with get_sessionmaker()() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "select task_id, `desc`, product_type_id from tb_task "
                            "where task_id in :ids"
                        ).bindparams(bindparam("ids", expanding=True)),
                        {"ids": ids},
                    )
                )
                .mappings()
                .all()
            )
        db = {int(r["task_id"]): r for r in rows}
        assert set(db) == set(ids), "不得返回库里不存在的工单（编造证据）"
        for case in result.cases:
            row = db[case.task_id]
            assert (case.phenomenon or "") == (row["desc"] or ""), (
                f"工单 {case.task_id} 的现象文本必须与库中原样一致（不得改写/润色/编造）"
            )
            assert int(row["product_type_id"]) == read_tools.REPAIR_PRODUCT_TYPE_ID == 3, (
                f"工单 {case.task_id} 的 product_type_id={row['product_type_id']}，"
                "不是维修工单——返回非维修工单就是把流程/投放单当成了故障案例"
            )

    # 本机既有事实：维修工单描述全是占位符/流程备注（'111'×4 + '后台工作人员取消'）
    # → 可用案例必须为 0，且必须给出“不可据此下诊断结论”的保守提示（FIX-22 的降级落地）
    assert result.usable_case_count == 0, (
        "本机维修工单描述均不可用；若这里变成 >0，说明 phenomenon_usable 的判定被放松了"
        f"（实际：{[c.phenomenon for c in result.cases]}）"
    )
    assert result.no_evidence_hint, "全部不可用时必须给出提示，否则调用方会把“有工单”当成“有证据”"
    assert "没有一条可用作故障现象" in result.no_evidence_hint
    assert "规则库检索（非语义检索）" in result.caliber, "口径必须随行（否则会被当成语义检索结果）"


async def test_live_history_cases_falls_back_to_model_or_region():
    """设备本身没有维修单时，必须降级到同型号/同区域，而不是直接返回空。"""
    async with get_sessionmaker()() as session:
        machines = await read_tools.list_operating_machines(session, limit=50)
    async with get_sessionmaker()() as session:
        result = await read_tools.search_history_cases(
            session,
            inner_code=machines[-1].inner_code,
            start=datetime(2000, 1, 1),
            end=datetime.now(),
        )
    print(  # noqa: T201 —— live 抽样的验收证据输出（需 -s 查看）
        f"[cases] 降级检查：设备 {machines[-1].inner_code} 命中 {result.total_cases} 条，"
        f"分布={result.matched_by_counts}"
    )
    # 不假设一定有历史数据（开发库可能为空）；只断言“不抛错 + 匹配级别合法 + 有命中就得有级别”
    assert all(
        case.matched_by
        in (
            read_tools.CASE_MATCH_SAME_DEVICE,
            read_tools.CASE_MATCH_SAME_MODEL,
            read_tools.CASE_MATCH_SAME_REGION,
        )
        for case in result.cases
    )
    if result.cases:
        assert result.matched_by_counts, "有命中就必须给出匹配分布（否则无法复核“为什么算相似”）"
