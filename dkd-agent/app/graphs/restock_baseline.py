"""补货基线引擎（Phase 1 / 任务 1-4）。

定位：**纯统计**的需求估计层。输入「货道库存档案 + 逐日销量序列」，输出每货道的
建议补货量与依据。**不调 LLM、不读库、不看时钟**（`today` 由调用方传入）——
1-5 的 LLM 校准只在这个结果上做加减与理由润色，所以这一层必须是可复现、可回测的。

## 算法（以及每个数字的来历）

1. **逐日销量序列**：`align_daily_series` 把工具层返回的稀疏「按日聚合」补齐成
   长度 = 窗口天数的日历序列（**没订单的日子补 0**，不是补 1 或跳过），
   最后一个元素 = **昨天**（今天数据不完整，不参与统计）。
2. **分位销量**：对每个窗口（默认 7/14/30 天）取该窗口内逐日销量的 **q 分位**（默认 0.75）。
   - 为什么用分位而不是均值：均值会被单日爆量（促销/补货后的集中消耗）拉高，
     算出一堆永远卖不完的货；分位是“多数日子不会超过的量”。
   - 为什么分位在**含 0 的全部天数**上算（而不是只算有动销的天）：只算动销日会系统性高估
     低流量货道的日均需求（30 天只有 3 天开张的货道会被当成“每天都卖”）。
   - **与长窗均值取大**（`max(加权分位, 有样本的最长窗口均值)`）：分位在**稀疏动销**货道上会归零
     （30 天只有 1 天卖了 6 件 → p75=0），单独用它会让这类货道永远得不到补货而慢性缺货；
     均值能保住“这个货道确实在卖”的非零基线。反过来的爆量场景由分位主导（均值被拉高也不会溢出，
     因为最后还有容量夹取与人工确认两道口）。
3. **多窗口加权**：近窗（7 天）权重高、长窗（30 天）权重低（默认 0.5/0.3/0.2）——
   既能跟上一周的趋势，又不被单日异常带偏。**只在有样本的窗口间归一化**：
   若 7 天窗口一条订单都没有（设备刚上线/数据延迟），权重全部落到有样本的窗口，
   否则整条货道的需求会凭空归零。
4. **容量与下限约束**（两层，顺序不能反）：
   `目标库存 = ceil(日均需求 × 补货周期天数 × 服务水平系数)` →
   与「最小预警值 × 倍数」取大（对齐现有规则版 `InventoryServiceImpl.generateRestockSuggestion`
   的 `min_stock × 2` 下限，避免运营习惯突变）→ 再与货道容量取小 →
   `建议量 = clamp(目标 - 现库存, 0, 容量 - 现库存)`。
5. **预计撑至日期**：`floor(现库存 / 日均需求)` 天后。需求为 0 时不给日期（无法估算），
   而不是编一个“9999 天”。

## 三个必须显式处理的边界（不是防御性编程，是真实会发生的）

- **无样本 ≠ 零需求**：整台设备近 30 天无订单（设备刚上线/断网/数据延迟）时不能得出“不用补货”，
  降级为「容量 × 85%」兜底（与规则版一致）并标记 `degraded=True`。
- **缺货自锁**：`现库存=0` 且窗口内无动销——**无法区分“真的不卖”与“因为缺货卖不出去”**。
  若判为“不卖”，货道会永远空着（自锁）。故同样降级为 85% 兜底并写明原因。
- **零动销但仍有库存**：判为“不卖”，建议量 0（理由里说明），不占用补货工时。

## 与规则版（`InventoryServiceImpl.generateRestockSuggestion`）的关系

规则版是「按容量 85% 补满」，完全不看销量；本引擎看销量，只在**没有可信样本时**退化为规则版。
排期 1-14 的双跑就是拿这两条链路做对照，
因此这里刻意保持兜底口径与规则版一致（85% + min_stock×2 下限）。

## 参数默认值的状态（AGENTS §9.3 不隐瞒）

`覆盖率天数 2 / 服务水平 1.2 / 分位 0.75 / 下限倍数 2` 是**工程默认值，业务未确认**，
已全部做成配置项（`DKD_AGENT_RESTOCK_*`），等待 1-13/5-2 用真实复盘数据标定。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.tools.read_tools import ChannelDailySales, ChannelStockItem

# 分位数在样点间的插值方式：linear（等价 numpy 默认 / R type 7）。
# 之所以不用最近邻：日销量序列长度常常很小（7 个点），最近邻会让 q=0.75 与 q=0.9 得到同一个值，
# 参数调了却没效果，排查时会怀疑人生。
INTERPOLATION = "linear"

# 样本置信度门槛（按 30 天窗口内的**动销天数**划分，不是订单数——
# 一天卖 50 件与 50 天各卖 1 件，对“日均”的可信度完全不同）
CONFIDENCE_HIGH_DAYS = 14
CONFIDENCE_MEDIUM_DAYS = 5

# 判定“设备整体有没有样本”用的统计窗口（与最长分析窗口一致）
SAMPLE_WINDOW_DAYS = 30


class WindowStat(BaseModel):
    """单个分析窗口的统计量（写进依据，便于人工复核与 3-6 复盘归因）。"""

    days: int
    observed_days: int = Field(description="窗口内有动销的天数")
    total_qty: int
    mean: float
    p50: float
    p75: float
    p90: float
    demand: float = Field(description="本窗口采用的日均需求（= q 分位，含 0 值天数）")


class BaselineSuggestion(BaseModel):
    """单货道的基线建议（1-5 会在此基础上做 LLM 校准）。"""

    channel_id: int
    channel_code: str
    sku_id: int | None = None
    sku_name: str | None = None
    current_quantity: int
    max_capacity: int
    suggested_quantity: int
    after_restock_quantity: int
    daily_demand: float = Field(description="加权分位日均需求（件/天）")
    estimated_days: int = Field(default=0, description="预计可支撑天数（向下取整）")
    runout_date: date | None = Field(default=None, description="预计撑至日期；无法估算时为 None")
    priority: int = Field(default=1, ge=1, le=4, description="1低 2中 3高 4紧急")
    confidence: str = Field(default="none", description="high/medium/low/none")
    degraded: bool = Field(default=False, description="是否降级为规则版兜底（无样本/缺货自锁）")
    degrade_reason: str | None = Field(default=None, description="降级原因（给运营看的）")
    reason: str = Field(default="", max_length=500, description="建议依据（对齐原型「依据」字段）")
    windows: dict[int, WindowStat] = Field(default_factory=dict, description="各窗口统计量")
    data_consistent: bool = Field(
        default=True, description="tb_inventory 与 tb_channel 档案是否一致"
    )

    def to_restock_item(self) -> Any:
        """转换为 `RestockItem`（做范围夹取与一致性校验；由它保证写入工单的字段合法）。

        延迟导入的原因：`restock_state` 不依赖本模块，反向也不必要，
        但两者互为契约（字段名必须一致），放在函数内可避免循环导入。
        """
        from app.graphs.restock_state import RestockItem

        return RestockItem(
            sku_id=self.sku_id or 0,
            sku_name=self.sku_name,
            channel_id=self.channel_id,
            channel_code=self.channel_code,
            current_quantity=self.current_quantity,
            max_capacity=self.max_capacity,
            suggested_quantity=self.suggested_quantity,
            after_restock_quantity=self.after_restock_quantity,
            estimated_days=self.estimated_days,
            priority=self.priority,
            reason=self.reason,
        )


def quantile(values: Sequence[float], q: float) -> float:
    """线性插值分位数（type 7）。`values` 无需有序，内部排序。

    空序列返回 0.0：调用方语义是“没有销量的货道需求为 0”，
    抛异常会逼上游在无数据时写 try/except，反而更容易吞错。
    """
    if not values:
        return 0.0
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"分位 q 必须在 [0,1]，收到 {q}")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def align_daily_series(
    rows: Iterable[ChannelDailySales], *, days: int, today: date
) -> dict[tuple[str, str], list[int]]:
    """把稀疏的「按日聚合」对齐成完整日历序列（缺失日补 0）。

    返回 {(inner_code, channel_code): [qty, ...]}，长度 = `days`，
    **最后一个元素是昨天**（`today - 1`），第一个是 `today - days`。
    与 `read_tools.default_sales_window(days=..., today=today)` 的窗口边界一致
    （半开区间 [today-days, today)），保证「取数窗口」与「序列下标」不会错位一格。
    """
    if days <= 0:
        raise ValueError("days 必须为正整数")
    series: dict[tuple[str, str], list[int]] = {}
    start = today - timedelta(days=days)
    for row in rows:
        key = (row.inner_code, row.channel_code)
        values = series.get(key)
        if values is None:
            values = [0] * days
            series[key] = values
        offset = (row.sale_date - start).days
        # 越界说明 SQL 窗口与序列窗口不一致——宁可丢弃也不要写错位置（错位会让需求算到别的日子上）
        if 0 <= offset < days:
            values[offset] += int(row.qty or 0)
    return series


def _window_stat(values: Sequence[int], *, days: int, q: float) -> WindowStat:
    window = list(values[-days:]) if days <= len(values) else list(values)
    return WindowStat(
        days=days,
        observed_days=sum(1 for v in window if v > 0),
        total_qty=int(sum(window)),
        mean=round(sum(window) / len(window), 4) if window else 0.0,
        p50=round(quantile(window, 0.5), 4),
        p75=round(quantile(window, 0.75), 4),
        p90=round(quantile(window, 0.9), 4),
        demand=round(quantile(window, q), 4),
    )


def estimate_demand(
    values: Sequence[int], *, settings: Settings, q: float | None = None
) -> tuple[float, dict[int, WindowStat]]:
    """多窗口加权分位日均需求。

    权重**只在有样本（窗口内有动销）的窗口间归一化**，理由见模块 docstring 第 3 条。
    全部窗口都无样本时返回 0.0（是否补货由上层按“无样本/零动销”两条边界决定）。
    """
    quantile_q = q if q is not None else settings.restock_quantile
    windows = settings.restock_weights
    stats = {days: _window_stat(values, days=days, q=quantile_q) for days in sorted(windows)}
    usable = {days: w for days, w in windows.items() if stats[days].observed_days > 0}
    if not usable:
        return 0.0, stats
    weight_total = sum(usable.values())
    blended = sum(stats[days].demand * weight / weight_total for days, weight in usable.items())
    # 稀疏动销兜底：分位会归零，用“有样本的最长窗口均值”做下限（理由见模块 docstring 第 2 条）
    mean_floor = stats[max(usable)].mean
    return round(max(blended, mean_floor), 4), stats


def _confidence(observed_days_30d: int) -> str:
    if observed_days_30d >= CONFIDENCE_HIGH_DAYS:
        return "high"
    if observed_days_30d >= CONFIDENCE_MEDIUM_DAYS:
        return "medium"
    if observed_days_30d >= 1:
        return "low"
    return "none"


def _rule_fallback_target(*, stock: ChannelStockItem, settings: Settings) -> int:
    """规则版兜底目标库存（对齐 `InventoryServiceImpl.generateRestockSuggestion`）。

    规则版：目标 = 容量 × 85%，下限 = 最小预警值 × 2。这里只复用它的“目标”语义，
    不复制它按库存水位猜 `estimated_days`（0/1/2/3）的做法——那是没有数据支撑的常量。
    """
    target = int(math.floor(stock.capacity * settings.restock_fill_ratio))
    if stock.min_stock:
        target = max(target, int(stock.min_stock * settings.restock_min_stock_multiple))
    return target


def _priority(
    *,
    current: int,
    suggested: int,
    demand: float,
    days_of_supply: int | None,
    min_stock: int | None,
    coverage_days: float,
) -> int:
    """优先级：缺货=4（沿用规则版习惯）→ 会断货=3 → 偏低=2 → 常规=1。"""
    if suggested <= 0:
        return 1
    if current == 0:
        return 4
    if demand <= 0:
        # 兜底路径没有需求数据，只能沿用规则版的水位阈值
        if min_stock and current <= min_stock * 0.5:
            return 3
        if min_stock and current <= min_stock:
            return 2
        return 1
    if days_of_supply is not None and days_of_supply < coverage_days:
        return 3
    if days_of_supply is not None and days_of_supply < coverage_days * 2:
        return 2
    return 1


def _compose_reason(
    *,
    stock: ChannelStockItem,
    demand: float,
    stats: dict[int, WindowStat],
    q: float,
    target: int,
    suggested: int,
    estimated_days: int,
    runout_date: date | None,
    degraded: bool,
    degrade_reason: str | None,
    confidence: str,
    settings: Settings,
    note: str | None = None,
) -> str:
    """拼建议依据（≤500 字，超长截断）。

    为什么要这么啰嗦：原型 V2 的「依据」是可展开查看的，运营要靠它判断“要不要信这条建议”；
    只写“库存偏低”等于让运营盲信模型（3-6 复盘时也无法归因）。
    """
    if degraded:
        parts = [
            f"【降级·规则兜底】{degrade_reason or '无销量样本'}；",
            f"目标库存={target}（容量 {stock.capacity} × {settings.restock_fill_ratio:.0%}"
            f" 与 最小预警值×{settings.restock_min_stock_multiple:g} 取大），",
            f"现库存={stock.current_stock}，建议补 {suggested}。",
        ]
        return _truncate("".join(parts))

    w7 = stats.get(7)
    sample_text = (
        f"近30天动销 {stats[30].observed_days} 天/共 {stats[30].total_qty} 件"
        if 30 in stats
        else "无样本"
    )
    window_text = "、".join(
        f"{days}天 p{int(q * 100)}={stats[days].demand:g}" for days in sorted(stats)
    )
    weight_text = "/".join(
        f"{d}:{settings.restock_weights[d]:g}" for d in sorted(settings.restock_weights)
    )
    parts = [
        f"日均需求 {demand:g} 件/天（{window_text}，权重 {weight_text}）；",
        f"销量口径=出货成功(status={settings.sales_order_status})，"
        f"样本置信度={confidence}（{sample_text}）；",
        f"按补货周期 {settings.restock_coverage_days:g} 天 × "
        f"服务水平 {settings.restock_service_factor:g} 得目标库存 {target}"
        f"（下限 最小预警值×{settings.restock_min_stock_multiple:g}，上限容量 {stock.capacity}），",
        f"现库存 {stock.current_stock}，建议补 {suggested}；",
    ]
    if demand > 0:
        parts.append(f"预计可支撑 {estimated_days} 天")
        parts.append(f"（撑至 {runout_date.isoformat()}）")
    else:
        parts.append("可支撑天数无法估算（窗口内需求为 0）")
    if w7 is not None and w7.observed_days == 0 and 30 in stats and stats[30].observed_days > 0:
        parts.append("；近 7 天无动销，权重已下移到长窗口")
    if note:
        parts.append(f"；{note}")
    parts.append("。")
    return _truncate("".join(parts))


def _truncate(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def compute_baseline(
    *,
    stock: ChannelStockItem,
    daily_qty: Sequence[int],
    today: date,
    settings: Settings | None = None,
    device_has_sample: bool = True,
) -> BaselineSuggestion:
    """单货道基线建议（核心入口，纯函数：无 IO、无随机、无时钟）。

    @param stock 货道 + 库存档案（1-1 输出；容量取 `tb_channel.max_capacity`，见其 docstring）
    @param daily_qty 该货道的日历序列（`align_daily_series` 输出，末位=昨天）
    @param today 分析基准日（**显式传入**，否则回测无法复现）
    @param device_has_sample 该设备近 30 天是否有任何订单
        （由调用方汇总，见 `compute_device_baseline`）
    """
    settings = settings or get_settings()
    capacity = stock.capacity
    current = int(stock.current_stock or 0)
    room = max(0, capacity - current)

    demand, stats = estimate_demand(daily_qty, settings=settings)
    observed_30d = stats[SAMPLE_WINDOW_DAYS].observed_days if SAMPLE_WINDOW_DAYS in stats else 0
    confidence = _confidence(observed_30d)

    # --- 三条边界（顺序有意为之：先判“不可信”，再判“可信但不需要”）---
    degraded = False
    degrade_reason: str | None = None
    if not device_has_sample:
        degraded = True
        degrade_reason = "该设备近 30 天无任何订单，无法估计需求（新上线/断网/数据延迟均可能）"
    elif observed_30d == 0 and current == 0:
        degraded = True
        degrade_reason = (
            "该货道近 30 天无动销且现库存为 0：无法区分“真的不卖”与“因缺货卖不出去”，"
            "按规则兜底避免缺货自锁"
        )

    if degraded:
        target = _rule_fallback_target(stock=stock, settings=settings)
        suggested = max(0, min(target - current, room))
    elif observed_30d == 0:
        # 有样本、但本货道零动销：判为“不卖”（理由见模块 docstring 第三条）
        target = current
        suggested = 0
    else:
        target = math.ceil(
            demand * settings.restock_coverage_days * settings.restock_service_factor
        )
        if stock.min_stock:
            target = max(target, int(stock.min_stock * settings.restock_min_stock_multiple))
        target = min(target, capacity)
        suggested = max(0, min(target - current, room))

    estimated_days = int(current // demand) if demand > 0 else 0
    runout_date = (today + timedelta(days=estimated_days)) if demand > 0 else None
    priority = _priority(
        current=current,
        suggested=suggested,
        demand=demand if not degraded else 0.0,
        days_of_supply=estimated_days if demand > 0 else None,
        min_stock=stock.min_stock,
        coverage_days=settings.restock_coverage_days,
    )
    # 零动销且仍有库存：判为“不卖商品”，必须把结论写进理由——
    # 否则运营看到一条“建议补 0 件”的建议会以为是系统算错了（实际上是有意为之）。
    zero_movement_note = (
        "近 30 天该货道无任何动销，判为不卖商品（非缺货导致），不建议补货"
        if not degraded and observed_30d == 0
        else None
    )
    reason = _compose_reason(
        stock=stock,
        demand=demand,
        stats=stats,
        q=settings.restock_quantile,
        target=target,
        suggested=suggested,
        estimated_days=estimated_days,
        runout_date=runout_date,
        degraded=degraded,
        degrade_reason=degrade_reason,
        confidence=confidence,
        settings=settings,
        note=zero_movement_note,
    )
    return BaselineSuggestion(
        channel_id=stock.channel_id,
        channel_code=stock.channel_code,
        sku_id=stock.channel_sku_id or stock.inventory_sku_id,
        current_quantity=current,
        max_capacity=capacity,
        suggested_quantity=suggested,
        after_restock_quantity=current + suggested,
        daily_demand=demand,
        estimated_days=estimated_days,
        runout_date=runout_date,
        priority=priority,
        confidence=confidence,
        degraded=degraded,
        degrade_reason=degrade_reason,
        reason=reason,
        windows=stats,
        data_consistent=stock.data_consistent,
    )


def compute_device_baseline(
    *,
    stock_items: Sequence[ChannelStockItem],
    daily_rows: Sequence[ChannelDailySales],
    today: date,
    settings: Settings | None = None,
    days: int | None = None,
) -> list[BaselineSuggestion]:
    """按设备批量算基线（1-4 的对外入口，供补货子图节点调用）。

    为什么“是否有样本”在设备级判定：货道级零动销可能只是这个货道不卖，
    而设备级零订单说明**整台机器**的数据不可信（离线/新装），两者的处理完全不同。
    """
    settings = settings or get_settings()
    window = days or max(settings.restock_weights)
    series = align_daily_series(daily_rows, days=window, today=today)
    device_has_sample = any(sum(values) > 0 for values in series.values())
    suggestions: list[BaselineSuggestion] = []
    for stock in stock_items:
        values = series.get((stock.inner_code, stock.channel_code), [0] * window)
        suggestions.append(
            compute_baseline(
                stock=stock,
                daily_qty=values,
                today=today,
                settings=settings,
                device_has_sample=device_has_sample,
            )
        )
    # 紧迫的排前面（运营先看缺货与快断货的）；同级按建议量降序（同样急，先补量大的）
    suggestions.sort(key=lambda s: (-s.priority, -s.suggested_quantity, s.channel_code))
    return suggestions
