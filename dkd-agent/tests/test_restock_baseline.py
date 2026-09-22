"""补货基线引擎单测（排期任务 1-4）。

原则（AGENTS §8）：
- **纯函数、零 IO**：不连库、不调 LLM、不看真实时钟（`today` 全部显式传入），
  因此每个断言都可用纸笔复算——这是 1-4 能被 3-6 回测复现的前提；
- **拒绝路径优先**：容量溢出、负数建议、超长理由、非法参数都必须真的被拦住；
- **边界不是防御性编程**：无样本降级、缺货自锁、零动销三条边界各有独立用例，
  它们都是真实会发生且结论方向完全相反的场景。
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from app.config import Settings, get_settings
from app.graphs import restock_baseline as rb
from app.tools.read_tools import ChannelDailySales, ChannelStockItem

TODAY = date(2026, 9, 21)
INNER = "A1000001"


def _settings(**overrides: object) -> Settings:
    """隔离配置：不读 .env、不读同名环境变量（否则本机 .env 会让断言随环境漂移）。"""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _stock(
    *,
    code: str = "1-1",
    current: int = 2,
    capacity: int = 10,
    min_stock: int | None = None,
    sku: int = 1,
    channel_id: int = 4732,
) -> ChannelStockItem:
    return ChannelStockItem(
        vm_id=80,
        inner_code=INNER,
        channel_id=channel_id,
        channel_code=code,
        inventory_sku_id=sku,
        channel_sku_id=sku,
        current_stock=current,
        min_stock=min_stock,
        channel_max_capacity=capacity,
        inventory_max_stock=capacity,
    )


def _series(*, last: int, value: int, length: int = 30) -> list[int]:
    """构造“最近 last 天每天卖 value 件、其余天为 0”的序列（末位=昨天）。"""
    return [0] * (length - last) + [value] * last


# --------------------------------------------------------------------------------------
# 分位数：引擎的算术基石，插值方式直接决定建议量
# --------------------------------------------------------------------------------------


def test_quantile_uses_linear_interpolation():
    assert rb.quantile([1, 2, 3, 4], 0.75) == pytest.approx(3.25)
    assert rb.quantile([0, 0, 0, 10], 0.75) == pytest.approx(2.5)
    assert rb.quantile([5, 1, 3], 0.0) == 1.0
    assert rb.quantile([5, 1, 3], 1.0) == 5.0
    assert rb.quantile([7], 0.75) == 7.0
    # 空序列给 0（“没有销量 = 需求为 0”），不抛异常
    assert rb.quantile([], 0.75) == 0.0


def test_quantile_rejects_out_of_range_q():
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        rb.quantile([1, 2], 1.5)


# --------------------------------------------------------------------------------------
# 序列对齐：补 0 的口径错了，后面所有需求都是错的
# --------------------------------------------------------------------------------------


def test_align_daily_series_zero_fills_and_anchors_last_element_to_yesterday():
    rows = [
        ChannelDailySales(
            inner_code=INNER, channel_code="1-1", sale_date=TODAY, qty=99
        ),  # 今天：应被丢弃
        ChannelDailySales(
            inner_code=INNER, channel_code="1-1", sale_date=TODAY.replace(day=20), qty=3
        ),  # 昨天
        ChannelDailySales(
            inner_code=INNER, channel_code="1-1", sale_date=date(2026, 8, 21), qty=5
        ),  # 窗口第一天 today-31? 不：today-31 越界
        ChannelDailySales(
            inner_code=INNER, channel_code="1-1", sale_date=date(2026, 8, 22), qty=7
        ),  # today-30 = 窗口首日
    ]
    series = rb.align_daily_series(rows, days=30, today=TODAY)

    values = series[(INNER, "1-1")]
    assert len(values) == 30
    assert values[-1] == 3, "末位必须是昨天（今天的订单不完整，不参与统计）"
    assert values[0] == 7, "首位必须是 today-30（与 default_sales_window 的半开区间一致）"
    assert sum(values) == 10, "越界日期（today-31 与今天）必须被丢弃，不能错位累计"


def test_align_daily_series_raises_on_non_positive_days():
    with pytest.raises(ValueError, match="正整数"):
        rb.align_daily_series([], days=0, today=TODAY)


# --------------------------------------------------------------------------------------
# 多窗口加权需求
# --------------------------------------------------------------------------------------


def test_weighted_demand_blends_three_windows():
    settings = _settings()
    # 近 7 天每天 5 件；更早 7 天每天 1 件；再往前 16 天为 0
    values = [0] * 16 + [1] * 7 + [5] * 7
    demand, stats = rb.estimate_demand(values, settings=settings)

    assert stats[7].demand == pytest.approx(5.0)
    assert stats[7].observed_days == 7
    assert stats[14].demand == pytest.approx(5.0), "14 天窗口 = 7 个 5 + 7 个 1，p75 落在 5"
    assert stats[30].demand == pytest.approx(1.0), "30 天窗口含 16 个 0，p75 落在 1"
    assert stats[30].mean == pytest.approx(1.4), "30 天均值 42/30=1.4（不占主导，但会兜底）"
    # 0.5×5 + 0.3×5 + 0.2×1 = 4.2 ＞ 长窗均值 1.4 → 取 4.2
    assert demand == pytest.approx(4.2)


def test_sparse_movement_uses_long_window_mean_as_floor():
    """稀疏动销：30 天只卖了一天 6 件 → p75=0，只看分位会让这类货道永远得不到补货（慢性缺货）。"""
    settings = _settings()
    demand, stats = rb.estimate_demand([0] * 29 + [6], settings=settings)

    assert stats[30].demand == pytest.approx(0.0), "分位确实归零"
    assert demand == pytest.approx(0.2), "长窗均值 6/30=0.2 兜底，保证“确实在卖”的货道有非零需求"


def test_weights_redistribute_when_short_window_has_no_sample():
    settings = _settings()
    values = [0] * 23 + [1] * 7  # 最近 7 天有动销
    values[23:] = [0] * 7  # 但把最近 7 天改成 0 → 7 天窗口无样本
    values[7:23] = [1] * 16
    demand, stats = rb.estimate_demand(values, settings=settings)

    assert stats[7].observed_days == 0
    assert stats[14].observed_days == 7
    # 权重只在 14/30 之间归一化：两者分位都是 1 → 需求仍是 1，而不是被 0.5 权重稀释成 0.5
    assert demand == pytest.approx(1.0)


def test_demand_is_zero_when_no_window_has_sample():
    settings = _settings()
    demand, stats = rb.estimate_demand([0] * 30, settings=settings)
    assert demand == 0.0
    assert stats[30].observed_days == 0


# --------------------------------------------------------------------------------------
# 建议量：容量夹取、下限抬升、预计撑至日期
# --------------------------------------------------------------------------------------


def test_suggestion_is_clamped_to_free_space_never_overflows():
    stock = _stock(current=9, capacity=10)
    suggestion = rb.compute_baseline(stock=stock, daily_qty=_series(last=7, value=5), today=TODAY)
    assert suggestion.suggested_quantity == 1, "只剩 1 个空位，不能给出 10 件"
    assert suggestion.after_restock_quantity == 10
    assert suggestion.after_restock_quantity <= suggestion.max_capacity


def test_min_stock_multiple_raises_target_floor():
    settings = _settings()
    stock = _stock(current=2, capacity=20, min_stock=6)
    # 需求很低（30 天只卖了 6 件），若只看需求几乎不用补
    suggestion = rb.compute_baseline(
        stock=stock, daily_qty=_series(last=6, value=1), today=TODAY, settings=settings
    )
    assert suggestion.suggested_quantity == 10, "目标库存被 min_stock×2=12 抬升 → 12-2=10"
    assert suggestion.after_restock_quantity == 12


def test_estimated_days_and_runout_date_are_derived_from_demand():
    # 现库存 10，日均需求 5 → 撑 2 天
    stock = _stock(current=10, capacity=20)
    suggestion = rb.compute_baseline(stock=stock, daily_qty=_series(last=30, value=5), today=TODAY)
    assert suggestion.daily_demand == pytest.approx(5.0)
    assert suggestion.estimated_days == 2
    assert suggestion.runout_date == date(2026, 9, 23)
    assert suggestion.suggested_quantity == 2, "目标 5×2×1.2=12 → 12-10=2"
    assert "撑至 2026-09-23" in suggestion.reason


def test_priority_out_of_stock_is_urgent_and_near_stockout_is_high():
    out_of_stock = rb.compute_baseline(
        stock=_stock(current=0, capacity=10), daily_qty=_series(last=7, value=3), today=TODAY
    )
    # 日均需求 = 0.5×3 + 0.3×3 + 0.2×0 = 2.4 → 目标 ceil(2.4×2×1.2)=6，容量 10 有空位
    assert out_of_stock.daily_demand == pytest.approx(2.4)
    assert out_of_stock.priority == 4
    assert out_of_stock.suggested_quantity == 6

    near = rb.compute_baseline(
        stock=_stock(current=3, capacity=20), daily_qty=_series(last=7, value=2), today=TODAY
    )
    # 日均需求 1.6，现库存 3 → 撑 1 天 < 补货周期 2 天 → 高优先级
    assert near.estimated_days == 1
    assert near.priority == 3


# --------------------------------------------------------------------------------------
# 三条边界：无样本 / 缺货自锁 / 零动销（结论方向不同，必须分开测）
# --------------------------------------------------------------------------------------


def test_no_sample_device_falls_back_to_rule_version_target():
    suggestion = rb.compute_baseline(
        stock=_stock(current=2, capacity=10),
        daily_qty=[0] * 30,
        today=TODAY,
        device_has_sample=False,
    )
    assert suggestion.degraded is True
    assert suggestion.suggested_quantity == 6, "规则版兜底：floor(10×0.85)=8 → 8-2=6"
    assert suggestion.daily_demand == 0.0
    assert suggestion.runout_date is None, "无需求数据时不给撑至日期，而不是编一个 9999 天"
    assert "降级" in suggestion.reason
    assert suggestion.confidence == "none"


def test_no_sample_with_min_stock_uses_rule_floor():
    suggestion = rb.compute_baseline(
        stock=_stock(current=2, capacity=10, min_stock=5),
        daily_qty=[0] * 30,
        today=TODAY,
        device_has_sample=False,
    )
    # 规则版：max(8, min_stock×2=10) = 10 → 10-2=8
    assert suggestion.suggested_quantity == 8


def test_out_of_stock_without_history_degrades_instead_of_self_locking():
    """缺货自锁：现库存 0 且无动销，无法区分“不卖”与“因缺货卖不出去”→ 必须兜底补货。"""
    suggestion = rb.compute_baseline(
        stock=_stock(current=0, capacity=10),
        daily_qty=[0] * 30,
        today=TODAY,
        device_has_sample=True,  # 设备有订单，只是这个货道没有 → 仍走降级分支
    )
    assert suggestion.degraded is True
    assert suggestion.suggested_quantity == 8, "不能给出“永远不补”的结论，否则货道会永久空置"
    assert "缺货" in (suggestion.degrade_reason or "")


def test_zero_movement_with_remaining_stock_suggests_nothing():
    suggestion = rb.compute_baseline(
        stock=_stock(current=5, capacity=10),
        daily_qty=[0] * 30,
        today=TODAY,
        device_has_sample=True,
    )
    assert suggestion.degraded is False, "有样本但本货道零动销 ≠ 无样本"
    assert suggestion.suggested_quantity == 0
    assert suggestion.priority == 1
    assert "不卖商品" in suggestion.reason


# --------------------------------------------------------------------------------------
# 契约：给下游（1-5 校准 / 1-6 计划）的字段必须能直接过 RestockItem 校验
# --------------------------------------------------------------------------------------


def test_to_restock_item_passes_validation_and_maps_quantity_not_capacity():
    suggestion = rb.compute_baseline(
        stock=_stock(current=2, capacity=10, code="5-6", channel_id=999),
        daily_qty=_series(last=7, value=5),
        today=TODAY,
    )
    item = suggestion.to_restock_item()
    payload = item.to_task_detail_payload()

    assert item.channel_code == "5-6"
    assert item.max_capacity == 10
    assert payload["expectCapacity"] == item.suggested_quantity, (
        "expectCapacity 必须是补货数量（dkd-app 会把它累加进库存）——0-13 冻结稿 §4.2 的语义陷阱"
    )
    assert payload["expectCapacity"] != item.max_capacity


def test_reason_is_capped_at_500_chars():
    stock = _stock(current=1, capacity=9999, min_stock=1)
    suggestion = rb.compute_baseline(stock=stock, daily_qty=_series(last=30, value=3), today=TODAY)
    assert len(suggestion.reason) <= 500, "reason 字段有 max_length=500 的契约约束"
    # 依据里必须能看出算的是什么东西（3-6 复盘要按依据归因）
    assert "出货成功" in suggestion.reason
    assert "目标库存" in suggestion.reason


def test_device_baseline_sorts_by_priority_then_quantity():
    stocks = [
        _stock(code="1-1", current=5, capacity=10, channel_id=1),  # 零动销 → 优先 1，建议 0
        _stock(code="1-2", current=0, capacity=10, channel_id=2),  # 缺货 → 优先 4
        _stock(code="1-3", current=1, capacity=30, channel_id=3),  # 快断货 → 优先 3
    ]
    rows = [
        # 1-1 一条行都没有（真零动销）；今天的订单不属于统计窗口，只用来让“设备有样本”成立
        ChannelDailySales(inner_code=INNER, channel_code="9-9", sale_date=TODAY, qty=9),
    ]
    for day_offset in range(1, 8):  # 1-2 与 1-3 近 7 天持续动销
        sale_date = TODAY - timedelta(days=day_offset)
        rows.append(
            ChannelDailySales(inner_code=INNER, channel_code="1-2", sale_date=sale_date, qty=4)
        )
        rows.append(
            ChannelDailySales(inner_code=INNER, channel_code="1-3", sale_date=sale_date, qty=4)
        )
    suggestions = rb.compute_device_baseline(stock_items=stocks, daily_rows=rows, today=TODAY)

    assert len(suggestions) == 3
    assert [s.channel_code for s in suggestions] == ["1-2", "1-3", "1-1"], (
        "紧迫的排前面（运营从上往下处理），同优先级按建议量降序"
    )
    assert [s.priority for s in suggestions] == [4, 3, 1]
    assert suggestions[-1].suggested_quantity == 0, "零动销货道排最后，不占补货工时"


def test_device_with_any_order_is_treated_as_having_sample():
    stocks = [_stock(code="1-1", current=2, capacity=10, channel_id=1)]
    rows = [
        ChannelDailySales(
            inner_code=INNER, channel_code="9-9", sale_date=TODAY.replace(day=15), qty=3
        )
    ]
    suggestions = rb.compute_device_baseline(stock_items=stocks, daily_rows=rows, today=TODAY)
    assert suggestions[0].degraded is False, "同设备别的货道有订单 → 设备数据是可信的"
    assert suggestions[0].suggested_quantity == 0, "本货道零动销且仍有库存 → 不补"


# --------------------------------------------------------------------------------------
# 不变量：随机序列下永不溢出容量、永不给负数（这是能给运营看的硬保证）
# --------------------------------------------------------------------------------------


def test_random_series_never_violates_capacity_or_sign():
    rng = random.Random(20260921)  # 固定种子：失败可复现
    settings = _settings()
    for _ in range(200):
        capacity = rng.randint(1, 40)
        current = rng.randint(0, capacity)
        values = [rng.choice([0, 0, 0, 1, 2, 5, 9]) for _ in range(30)]
        suggestion = rb.compute_baseline(
            stock=_stock(current=current, capacity=capacity, min_stock=rng.choice([None, 1, 5])),
            daily_qty=values,
            today=TODAY,
            settings=settings,
            device_has_sample=rng.random() > 0.2,
        )
        assert suggestion.suggested_quantity >= 0
        assert suggestion.suggested_quantity <= capacity - current
        assert suggestion.after_restock_quantity == current + suggestion.suggested_quantity
        assert suggestion.after_restock_quantity <= capacity
        assert len(suggestion.reason) <= 500


# --------------------------------------------------------------------------------------
# 配置护栏
# --------------------------------------------------------------------------------------


def test_window_weights_parse_and_reject_bad_format():
    assert _settings().restock_weights == {7: 0.5, 14: 0.3, 30: 0.2}
    assert _settings(DKD_AGENT_RESTOCK_WINDOW_WEIGHTS="7:1,14:1").restock_weights == {
        7: 1.0,
        14: 1.0,
    }
    with pytest.raises(ValueError):
        _ = _settings(DKD_AGENT_RESTOCK_WINDOW_WEIGHTS="").restock_weights


def test_defaults_are_documented_business_parameters():
    """默认值必须能通过配置覆盖（排期 3-6/5-2 要按复盘结果调参，改参数不该等于改代码）。"""
    settings = get_settings()
    assert 0 < settings.restock_quantile < 1
    assert settings.restock_coverage_days > 0
    assert settings.restock_service_factor >= 1
    assert 0 < settings.restock_fill_ratio <= 1
