"""排期任务 1-4 回测脚本（真库，只读）：

验收要求是「对历史数据回测：建议量与人工经验偏差 ≤±20%」。本脚本把这句验收**尽量拆成可执行的三项**，
并把**做不到的部分明确写出来**（AGENTS §9.3 不隐瞒）：

  [A] 容量安全（可验证）：所有建议量 0 ≤ 建议 ≤ 容量-现库存，且补货后不溢出；
  [B] 覆盖充分（可验证）：有需求样本的货道，补货后预计可支撑天数 ≥ 补货周期；
  [C] 与规则版（`InventoryServiceImpl.generateRestockSuggestion` 的 85% 补满）偏差（可计算）：
      规则版是当前运营人员实际看到的建议，把它当作“人工经验”的**代理指标**：
      但两者口径不同（规则版不看销量），偏差大是**预期结果**，不是失败——排期 1-14 的双跑就是要量化它。

  [D] 真正的“与实际人工补货量对比”**在当前开发库无法完成**，原因写在脚本输出里（数据不足，
      不是“用规则版冒充满足验收”）。

运行（在 dkd-agent 目录，先注入环境变量）：

    cd dkd-agent
    set -a; source ../.env; set +a
    .venv/Scripts/python.exe ../docs/scripts/backtest-restock-baseline-1-4.py

只读：本脚本不发任何写语句（`dkd_agent` 账号也只有 SELECT 权限）。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.graphs.restock_baseline import compute_device_baseline  # noqa: E402
from app.tools import read_tools  # noqa: E402

# 开发库订单数据的实际分布：2023-09-10 ~ 2023-09-15（见 docs/ddl/business_tables_survey.md）
ANCHOR = date(2023, 9, 16)  # 回测基准日 = 数据最后一天的下一天
WINDOW_DAYS = 30


def _rule_target(capacity: int, min_stock: int | None, fill_ratio: float, multiple: float) -> int:
    """规则版目标库存（复刻 `InventoryServiceImpl.generateRestockSuggestion` 的目标量语义）。"""
    target = int(capacity * fill_ratio)
    if min_stock:
        target = max(target, int(min_stock * multiple))
    return min(target, capacity)


async def main() -> int:
    settings = get_settings()
    start = datetime.combine(ANCHOR - timedelta(days=WINDOW_DAYS), time.min)
    end = datetime.combine(ANCHOR, time.min)

    async with get_sessionmaker()() as session:
        machines = await read_tools.list_operating_machines(session)
        codes = [m.inner_code for m in machines]
        stock = await read_tools.list_channel_stock(session, inner_codes=codes)
        daily = await read_tools.aggregate_channel_daily_sales(
            session, inner_codes=codes, start=start, end=end
        )

    print("=" * 78)
    print(f"[1-4 回测] 基准日={ANCHOR} 窗口=[{start.date()}, {end.date()}) 设备={len(machines)} 货道={len(stock)}")
    print(f"[样本] 窗口内「货道×日」销量行数={len(daily)}；订单件数={sum(r.qty for r in daily)}")
    print("=" * 78)

    by_vm: dict[str, list] = {}
    for item in stock:
        by_vm.setdefault(item.inner_code, []).append(item)

    rows: list[tuple[str, str]] = []
    a_ok = a_bad = 0
    b_ok = b_na = b_bad = 0
    rule_total = tool_total = 0
    deviations: list[float] = []

    for inner_code, items in sorted(by_vm.items()):
        suggestions = compute_device_baseline(
            stock_items=items,
            daily_rows=[r for r in daily if r.inner_code == inner_code],
            today=ANCHOR,
            settings=settings,
        )
        for s in suggestions:
            room = s.max_capacity - s.current_quantity
            rule = _rule_target(
                s.max_capacity, None, settings.restock_fill_ratio, settings.restock_min_stock_multiple
            )
            rule_qty = max(0, min(rule - s.current_quantity, room))
            rule_total += rule_qty
            tool_total += s.suggested_quantity
            if rule_qty > 0:
                deviations.append((s.suggested_quantity - rule_qty) / rule_qty)

            # [A] 容量安全
            if 0 <= s.suggested_quantity <= room:
                a_ok += 1
            else:
                a_bad += 1
                rows.append((s.channel_code, f"!! 容量越界 建议={s.suggested_quantity} 空位={room}"))

            # [B] 覆盖充分：有需求样本（日均>0）且建议>0 的货道，补货后应能撑过补货周期
            if s.daily_demand > 0 and s.suggested_quantity > 0:
                covered = s.after_restock_quantity / s.daily_demand
                if covered >= settings.restock_coverage_days:
                    b_ok += 1
                else:
                    b_bad += 1
                    rows.append(
                        (s.channel_code, f"!! 覆盖不足 {covered:.2f} 天 < {settings.restock_coverage_days:g} 天")
                    )
            else:
                b_na += 1

    print(f"[A] 容量安全：{a_ok} 通过 / {a_bad} 越界（要求 0 越界）")
    print(f"[B] 覆盖充分：{b_ok} 通过 / {b_bad} 不足 / {b_na} 不适用（需求为 0 或无需补货）")
    if deviations:
        avg = sum(deviations) / len(deviations)
        print(
            f"[C] 与规则版建议量偏差：均值 {avg:+.1%}（n={len(deviations)}，"
            f"规则版合计={rule_total} 件 / 基线引擎合计={tool_total} 件）"
        )
        print("    说明：规则版不看销量（按容量 85% 补满），偏差大属预期；1-14 双跑就是量化这个差距。")
    else:
        print("[C] 与规则版对比：窗口内无可比样本（规则版建议量全为 0）")
    for code, note in rows:
        print(f"    {code}: {note}")

    print("-" * 78)
    print("[D] 未验证项（如实登记，不能算通过）：")
    print("    · 与「人工实际补货量」的对比无法在本库完成：")
    print("      - tb_inventory 仅 3 行、无历史快照 → 回测只能用“今天的库存”推“历史某天的库存”（错）；")
    print("      - tb_task_details 仅 13 行 / tb_task 22 行且集中在 2025-11，与订单数据（2023-09）不重叠；")
    print("      - 订单仅 29 单、集中于 2023-09-10~15 共 6 天 → 30 天窗口内 24 天无数据。")
    print("    · 因此 ±20% 这项验收**当前不成立**，需生产/测试库数据导出后重跑本脚本（1-13 前置）。")
    print("=" * 78)

    await dispose_engine()
    return 1 if (a_bad or b_bad) else 0


sys.exit(asyncio.run(main()))
