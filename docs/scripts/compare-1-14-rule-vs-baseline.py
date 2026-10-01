"""排期任务 1-14：规则版 vs 基线引擎的**建议量双跑对比**（只读，不写任何数据）。

## 这个脚本解决什么

排期 1-14 要求「双跑 3 周、埋点采集对比数据」，3-8 要据此输出**缺货率 / 建议偏差 / 人工调整率**
三项指标。但“双跑”如果没有统一的**配对口径**，两边各自的数字没法放在一起看——本脚本把口径
固定下来并输出可复跑的报表：

  · **同一天、同一台设备、同一货道**才算一对（不按设备汇总后再比——那会把货道错配的误差抹平）；
  · 规则版 = Java `GET /manage/inventory/restock`（`InventoryServiceImpl.generateRestockSuggestions`）；
  · 基线引擎 = 当日 `agent_restock_plan` 的持久化结果（同一套基线代码 + 校准开关的产物）；
  · 偏差只对**两边都有建议**的货道算百分比；只有一边有的（规则版没建议 / 基线没建议）单独计数，
    不混进百分比（否则“除以 0”会让统计数字骗人）。

口径的完整定义、字段来源与不可比因素见 `docs/agent-restock-1-14-comparison-caliber.md`。

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a
.venv/Scripts/python.exe ../docs/scripts/compare-1-14-rule-vs-baseline.py
# 指定日期 / 只看某台设备
.venv/Scripts/python.exe ../docs/scripts/compare-1-14-rule-vs-baseline.py --date 2026-10-01 --device A1000001
```

## 前置与限制（不隐瞒）

1. **本机数据量很小**（tb_order 29 单 / 有效计划 3 条），所以本机跑出来的偏差率**只是口径验证**，
   不是业务结论。真正的 1-14 双跑需要 **FIX-6（生产/测试库导出）** 的数据量。
2. 规则版只看容量（不看销量），基线引擎看销量 → 两者**天生不同源**，
   本脚本的价值是把这个差异**量化**，而不是证明谁对。
3. 规则版的入口是管理后台权限接口，脚本用 admin 账号登录（只读调用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from datetime import date
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import dispose_engine, get_sessionmaker  # noqa: E402

JAVA_BASE = os.environ.get("DKD_AGENT_JAVA_BASE", "http://127.0.0.1:8080")
ADMIN_USER = os.environ.get("DKD_M2_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("DKD_M2_ADMIN_PASSWORD", "admin123")


async def java_login(client: httpx.AsyncClient) -> str | None:
    resp = await client.post(
        f"{JAVA_BASE}/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}, timeout=15.0
    )
    return (resp.json() or {}).get("token")


async def fetch_rule_suggestions(client: httpx.AsyncClient, token: str) -> list[dict]:
    """规则版建议（Java）。只读 GET，不需要写权限。"""
    resp = await client.get(
        f"{JAVA_BASE}/manage/inventory/restock",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    body = resp.json() if resp.content else {}
    if resp.status_code != 200 or body.get("code") != 200:
        raise RuntimeError(f"规则版接口失败：HTTP {resp.status_code} {str(body)[:120]}")
    return list(body.get("data") or [])


async def fetch_baseline_rows(session, plan_date: date, device: str | None) -> list[dict]:
    """基线引擎建议（当日 `agent_restock_plan` 持久化结果）。"""
    sql = (
        "select id, inner_code, total_quantity, status, items from agent_restock_plan "
        "where plan_date = :d and del_flag = '0'"
    )
    params: dict = {"d": plan_date}
    if device:
        sql += " and inner_code = :code"
        params["code"] = device
    rows = (await session.execute(text(sql), params)).mappings().all()
    out: list[dict] = []
    for row in rows:
        items = row["items"]
        if isinstance(items, str):
            items = json.loads(items)
        for item in items or []:
            out.append(
                {
                    "planId": int(row["id"]),
                    "innerCode": row["inner_code"],
                    "channelCode": item.get("channelCode"),
                    "suggestedQuantity": int(item.get("suggestedQuantity") or 0),
                    "currentQuantity": int(item.get("currentQuantity") or 0),
                    "maxCapacity": int(item.get("maxCapacity") or 0),
                    "priority": item.get("priority"),
                }
            )
    return out


def _key(inner_code: str, channel_code: str | None) -> tuple[str, str]:
    return (str(inner_code or "").strip(), str(channel_code or "").strip())


def compare(rule_rows: list[dict], base_rows: list[dict]) -> dict:
    rule = {_key(r.get("innerCode"), r.get("channelCode")): int(r.get("suggestedQuantity") or 0) for r in rule_rows}
    base = {_key(r["innerCode"], r["channelCode"]): r for r in base_rows}

    both, rule_only, base_only = [], [], []
    for key, qty in rule.items():
        if key in base:
            both.append((key, qty, base[key]["suggestedQuantity"]))
        else:
            rule_only.append(key)
    for key in base:
        if key not in rule:
            base_only.append(key)

    paired = []
    for key, rule_qty, base_qty in both:
        delta = base_qty - rule_qty
        pct = (delta / rule_qty * 100) if rule_qty > 0 else None
        paired.append(
            {"innerCode": key[0], "channelCode": key[1], "rule": rule_qty, "baseline": base_qty,
             "delta": delta, "deltaPct": pct}
        )

    pcts = [p["deltaPct"] for p in paired if p["deltaPct"] is not None]
    return {
        "paired": paired,
        "ruleOnly": rule_only,
        "baselineOnly": base_only,
        "ruleTotal": sum(rule.values()),
        "baselineTotal": sum(r["suggestedQuantity"] for r in base.values()),
        "ruleChannelCount": len(rule),
        "baselineChannelCount": len(base),
        "meanDeltaPct": round(statistics.fmean(pcts), 2) if pcts else None,
        "medianDeltaPct": round(statistics.median(pcts), 2) if pcts else None,
    }


async def metrics(session, plan_date: date) -> dict:
    """3-8 的三项指标（口径见 docs/agent-restock-1-14-comparison-caliber.md）。

    这里只做**当日**口径演示；3 周双跑由本脚本按天重复调用后汇总（FIX-6 数据到位后）。
    """
    out: dict = {}
    # ① 缺货率：缺货货道 / **有库存记录的**货道（口径见 caliber 文档 §4.1）
    #
    # ⚠️ 两个实测细节（不是猜的，2026-10-01 核对 information_schema）：
    #   · `tb_inventory` **没有 del_flag 列**（它不属 RuoYi 审计四件套的那批新表），
    #     加 del_flag 过滤会直接报 1054 —— 本脚本第一次跑就是这么挂的；
    #   · 缺货率的分母只能是 `tb_inventory` 有记录的货道（本机 3 行 vs `tb_channel` 272 行），
    #     所以必须把**覆盖率**一并输出，否则一个 3/3 的“缺货率”会被当成全网数据。
    stock = (
        await session.execute(
            text(
                "select count(*) as total, "
                "sum(case when current_stock = 0 then 1 else 0 end) as out_of_stock "
                "from tb_inventory"
            )
        )
    ).mappings().first()
    channel_total = int(
        (await session.execute(text("select count(*) from tb_channel"))).scalar() or 0
    )
    inv_total = int((stock or {}).get("total") or 0)
    out["inventoryRows"] = inv_total
    out["channelRows"] = channel_total
    out["stockCoverage"] = round(inv_total / channel_total, 4) if channel_total else None
    out["outOfStockChannels"] = int((stock or {}).get("out_of_stock") or 0)
    out["outOfStockRate"] = round(out["outOfStockChannels"] / inv_total, 4) if inv_total else None
    # ② 建议偏差：由 compare() 输出（这里留空，报告里用配对结果）
    # ③ 人工调整率：有 adjust/skip 动作的计划数 / 当日计划数
    #    target_type 实测是 'plan'（不是 'restock_plan'）——写错会静默得到 0，
    #    所以这里用**实测值**而不是命名字面上的直觉。
    adjusted = (
        await session.execute(
            text(
                "select count(distinct target_id) from agent_decision_log "
                "where action in ('restock.adjust', 'restock.skip') and target_type = 'plan' "
                "and create_time >= :d and create_time < :d + interval 1 day"
            ),
            {"d": plan_date},
        )
    ).scalar()
    total = (
        await session.execute(
            text("select count(*) from agent_restock_plan where plan_date = :d and del_flag = '0'"),
            {"d": plan_date},
        )
    ).scalar()
    out["adjustedPlans"] = int(adjusted or 0)
    out["totalPlans"] = int(total or 0)
    out["manualAdjustRate"] = round(int(adjusted or 0) / int(total), 4) if total else None
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="1-14 规则版 vs 基线引擎建议量对比（只读）")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--device", default=None, help="只看某台设备（inner_code）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON（供 3-8 报表消费）")
    args = parser.parse_args()
    plan_date = date.fromisoformat(args.date)

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            baseline_rows = await fetch_baseline_rows(session, plan_date, args.device)
            async with httpx.AsyncClient() as client:
                token = await java_login(client)
                if not token:
                    print("⛔ Java 登录失败：规则版入口需要管理后台权限（只读调用）")
                    return 2
                rule_rows = await fetch_rule_suggestions(client, token)
                if args.device:
                    rule_rows = [r for r in rule_rows if str(r.get("innerCode")) == args.device]
            stats = await metrics(session, plan_date)
    finally:
        await dispose_engine()

    result = compare(rule_rows, baseline_rows)
    result["metrics"] = stats
    result["planDate"] = plan_date.isoformat()

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    print(f"\n== 1-14 建议量双跑对比（只读） plan_date={plan_date} ==")
    print(f"规则版（Java 按容量 85%）货道数 : {result['ruleChannelCount']}，合计建议 {result['ruleTotal']} 件")
    print(f"基线引擎（agent_restock_plan）: {result['baselineChannelCount']}，合计建议 {result['baselineTotal']} 件")
    print(f"可配对（同设备同货道）        : {len(result['paired'])} 条")
    if result["paired"]:
        print(f"\n  {'设备':<10} {'货道':<6} {'规则版':>6} {'基线':>6} {'偏差':>6} {'偏差%':>8}")
        for p in result["paired"]:
            pct = "—" if p["deltaPct"] is None else f"{p['deltaPct']:+.1f}%"
            print(f"  {p['innerCode']:<10} {p['channelCode']:<6} {p['rule']:>6} {p['baseline']:>6} "
                  f"{p['delta']:>+6} {pct:>8}")
        print(f"\n  偏差均值={result['meanDeltaPct']}%  中位数={result['medianDeltaPct']}%")
        print("  说明：偏差 = 基线 - 规则版；负值表示基线引擎更保守（补得更少）")
    print(f"\n只有规则版有建议（基线未提）: {len(result['ruleOnly'])} 条")
    print(f"只有基线有建议（规则版未提）: {len(result['baselineOnly'])} 条")

    print("\n== 3-8 三项指标（当日口径演示）==")
    print(
        f"① 缺货率: {stats['outOfStockChannels']}/{stats['inventoryRows']} "
        f"= {stats['outOfStockRate']}（⚠️ 库存覆盖率 {stats['stockCoverage']}："
        f"有库存记录的货道 {stats['inventoryRows']} / 全部货道 {stats['channelRows']}）"
    )
    print(f"② 建议偏差: 均值 {result['meanDeltaPct']}% / 中位数 {result['medianDeltaPct']}%")
    print(f"③ 人工调整率: {stats['adjustedPlans']}/{stats['totalPlans']} = {stats['manualAdjustRate']}")
    print("\n⚠️ 本机数据量极小（见 docs/agent-restock-1-14-comparison-caliber.md §6），")
    print("   上面的偏差率只能验证口径，不能当业务结论；1-14 的真实双跑前置是 FIX-6。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
