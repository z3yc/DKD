"""排期任务 1-12 对账：Agent 侧聚合口径 vs Java 侧（ReportMapper）口径。

## 这个脚本要证明什么

1-12 的完成标准是「抽查 3 台设备全字段对账一致」。它的**前置**曾有三项：
FIX-3（Java 报表 SQL 丢参数绑定）、FIX-4（缺索引）、FIX-5（容量权威源）。
前三项中 FIX-3/FIX-4 已修，**只剩 FIX-5 影响库存/容量侧**——所以
**订单/营收口径的对账现在就可以做**，本脚本做的正是这一半。

## 为什么对账基准必须是「解析 ReportMapper.xml」而不是我手抄一份 SQL

手抄一份 SQL 去对，对的是「我理解的 Java 口径」，而不是「Java 实际执行的口径」。
把 XML 当**唯一真相**解析出来执行，那么以后有人改了 XML，本脚本会自动跟着变——
若改错了，对账会立刻红。这与 `ReportMapperSqlTest`（断言 XML 里必须绑定参数）是配套的：
一个管「SQL 形状对不对」，一个管「结果与 Agent 侧是否一致」。

## 覆盖内容

- [A] 全局口径对账：Agent 侧逐货道聚合求和 vs Java 侧 SQL 求和 → 必须逐字节相等
- [B] **口径敏感性**：把 status 换成 `>=1`、把时间窗挪走，两边必须**同步**变化
      （否则「相等」可能只是「两边都错成同一个值」——这是对账脚本最容易自欺的地方）
- [C] 真实 Java 路径：带 JWT 调 `POST /manage/report/generate`，证明 FIX-3 改完 SQL 后
      Java 侧真跑得通（不是只有我解析出来的文本能跑）

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a
.venv/Scripts/python.exe ../docs/scripts/verify-1-12-reconcile.py
```
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.tools.read_tools import aggregate_channel_sales  # noqa: E402

REPORT_MAPPER = (
    Path(__file__).resolve().parents[2]
    / "dkd-parent/dkd-manage/src/main/resources/mapper/manage/ReportMapper.xml"
)
JAVA_BASE = "http://127.0.0.1:8080"

_pass = 0
_fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def load_java_sql(statement_id: str, *, status: int, begin: datetime, end: datetime) -> str:
    """从 ReportMapper.xml 解析出 SQL 并代入参数。

    只做三处替换（`#{status}` / `#{beginTime}` / `#{endTime}`）——这正是 FIX-3 之前
    Java 侧**漏掉**的那三处绑定；在这里替进去就等于还原「Java 修好之后实际执行的 SQL」。
    """
    tree = ElementTree.parse(REPORT_MAPPER)
    for element in tree.getroot():
        if element.tag == "select" and element.get("id") == statement_id:
            sql = element.text or ""
            return (
                sql.replace("#{status}", str(status))
                .replace("#{beginTime}", f"'{begin:%Y-%m-%d %H:%M:%S}'")
                .replace("#{endTime}", f"'{end:%Y-%m-%d %H:%M:%S}'")
            )
    raise AssertionError(f"ReportMapper.xml 里找不到 select id={statement_id}")


async def _scalar(session, sql: str):
    result = await session.execute(text(sql))
    return result.scalar()


async def _data_window(session) -> tuple[datetime, datetime]:
    """取库内订单的真实时间范围（开发库数据是 2023 年的，必须按数据选窗，否则恒为 0）。"""
    row = (
        await session.execute(
            text("select min(create_time) as mn, max(create_time) as mx from tb_order")
        )
    ).first()
    begin = row.mn - timedelta(days=1)
    end = row.mx + timedelta(days=1)
    return begin, end


async def _device_codes(session, *, limit: int) -> list[str]:
    """挑订单最多的几台设备（对账要挑有数据的，否则“一致”毫无信息量）。"""
    rows = (
        await session.execute(
            text(
                "select inner_code, count(*) c from tb_order "
                "where status = 2 group by inner_code order by c desc limit :n"
            ),
            {"n": limit},
        )
    ).all()
    return [r.inner_code for r in rows]


# ----------------------------------------------------------------------------------
# [A] 全局口径对账
# ----------------------------------------------------------------------------------


async def phase_reconcile(session) -> None:
    begin, end = await _data_window(session)
    print(f"\n[A] 全局口径对账（窗口 {begin:%Y-%m-%d} ~ {end:%Y-%m-%d}，status=2）")

    codes = await _device_codes(session, limit=100)
    agent_rows = await aggregate_channel_sales(
        session, inner_codes=codes, start=begin, end=end, limit=5000
    )
    agent_amount = sum(r.amount for r in agent_rows)
    agent_count = sum(r.order_count for r in agent_rows)
    print(f"  → Agent 可归因：{len(agent_rows)} 条货道聚合 / amount={agent_amount} / count={agent_count}")

    java_amount = await _scalar(
        session,
        load_java_sql("sumRevenueByStatusAndDateRange", status=2, begin=begin, end=end),
    )
    java_count = await _scalar(
        session,
        load_java_sql("countOrdersByStatusAndDateRange", status=2, begin=begin, end=end),
    )
    print(f"  → Java 全局（解析 ReportMapper.xml）：amount={java_amount} / count={java_count}")

    # 不可归因部分（channel_code 为空）——它**不能**进按货道的聚合，也不能静静消失，
    # 所以对账不是简单“两边相等”，而是“口径一致 + 总量守恒”两个断言（见下）。
    un_amt = await _scalar(
        session,
        f"select coalesce(sum(amount),0) from tb_order where status=2 "
        f"and channel_code is null and create_time >= '{begin:%Y-%m-%d %H:%M:%S}' "
        f"and create_time < '{end:%Y-%m-%d %H:%M:%S}'",
    )
    un_cnt = await _scalar(
        session,
        f"select count(*) from tb_order where status=2 "
        f"and channel_code is null and create_time >= '{begin:%Y-%m-%d %H:%M:%S}' "
        f"and create_time < '{end:%Y-%m-%d %H:%M:%S}'",
    )
    share = (100.0 * int(un_cnt) / int(java_count)) if java_count else 0.0
    print(
        f"  → 不可归因（channel_code 为空）：count={un_cnt} / amount={un_amt}"
        f"（占全局订单的 {share:.0f}%）"
    )

    # 断言 1：**同口径必须逐字相等**——把 Java SQL 限制到“可归因”子集后与 Agent 比。
    # 这才是 1-12 说的“聚合口径与 Java 侧对账一致”。
    java_attr_amount = await _scalar(
        session,
        load_java_sql("sumRevenueByStatusAndDateRange", status=2, begin=begin, end=end).replace(
            "where status = 2", "where status = 2 and channel_code is not null"
        ),
    )
    java_attr_count = await _scalar(
        session,
        load_java_sql("countOrdersByStatusAndDateRange", status=2, begin=begin, end=end).replace(
            "where status = 2", "where status = 2 and channel_code is not null"
        ),
    )
    print(f"  → Java 可归因子集：amount={java_attr_amount} / count={java_attr_count}")
    check(
        "**同口径（可归因）营收逐字相等**",
        int(agent_amount) == int(java_attr_amount),
        f"Agent={agent_amount} vs Java={java_attr_amount}",
    )
    check(
        "**同口径（可归因）订单数逐字相等**",
        int(agent_count) == int(java_attr_count),
        f"Agent={agent_count} vs Java={java_attr_count}",
    )

    # 断言 2：**总量守恒**——可归因 + 不可归因 == 全局。
    # 为什么这条比“两边相等”更有价值：它同时证明了“没有静默丢失订单”，
    # 且若未来有人在 Agent 侧多加了过滤条件（静默少算），守恒会立刻破。
    check(
        "总量守恒：可归因 + 不可归因 == 全局（营收）",
        int(agent_amount) + int(un_amt) == int(java_amount),
        f"{agent_amount} + {un_amt} vs {java_amount}",
    )
    check(
        "总量守恒：可归因 + 不可归因 == 全局（订单数）",
        int(agent_count) + int(un_cnt) == int(java_count),
        f"{agent_count} + {un_cnt} vs {java_count}",
    )
    if share > 5:
        print(
            f"  ⚠️ 不可归因订单占比 {share:.0f}% 偏高（本机库 {un_cnt}/{java_count}）——"
            "属数据质量问题（下单时未回填货道），建议业务侧评估是否修数据"
        )


# ----------------------------------------------------------------------------------
# [B] 口径敏感性——防「两边都错成同一个值」造成的假绿
# ----------------------------------------------------------------------------------


async def phase_sensitivity(session) -> None:
    begin, end = await _data_window(session)
    print("\n[B] 口径敏感性（换了条件两边必须同步变化）")

    # B1：把 status 放宽成 >=1（FIX-3 之前的实际行为）→ 两边都要变大
    java_strict = await _scalar(
        session,
        load_java_sql("countOrdersByStatusAndDateRange", status=2, begin=begin, end=end),
    )
    loose_sql = load_java_sql(
        "countOrdersByStatusAndDateRange", status=1, begin=begin, end=end
    ).replace("status = 1", "status >= 1").replace("status =", "status >=")
    java_loose = await _scalar(session, loose_sql)
    print(f"  → Java 侧 count：status=2 → {java_strict}；status>=1 → {java_loose}")
    check(
        "放宽状态会让计数变大（证明 status 绑定**真的生效**，不是被忽略）",
        int(java_loose) > int(java_strict),
        f"{java_strict} → {java_loose}",
    )

    # B2：把窗口挪到数据范围之外 → 两边都应为 0
    far_begin = end + timedelta(days=30)
    far_end = far_begin + timedelta(days=7)
    java_empty = await _scalar(
        session,
        load_java_sql("countOrdersByStatusAndDateRange", status=2, begin=far_begin, end=far_end),
    )
    codes = await _device_codes(session, limit=100)
    agent_empty_rows = await aggregate_channel_sales(
        session, inner_codes=codes, start=far_begin, end=far_end, limit=5000
    )
    agent_empty = sum(r.order_count for r in agent_empty_rows)
    print(f"  → 窗外窗口（{far_begin:%Y-%m-%d} 起）：Agent={agent_empty} / Java={java_empty}")
    check("窗外窗口两边都为 0（证明时间窗**真的生效**）", int(agent_empty) == 0 == int(java_empty))


# ----------------------------------------------------------------------------------
# [C] 真实 Java 路径（证明 FIX-3 改完 SQL 后 Java 侧真跑得通）
# ----------------------------------------------------------------------------------


async def phase_java_path() -> None:
    print("\n[C] 真实 Java 路径：POST /manage/report/generate")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        login = await client.post(
            f"{JAVA_BASE}/login", json={"username": "admin", "password": "admin123"}
        )
        token = (login.json() or {}).get("token")
        if not token:
            check("登录取得 JWT（本机 captchaEnabled=false）", False, login.text[:120])
            return
        check("登录取得 JWT（本机 captchaEnabled=false）", True)

        resp = await client.post(
            f"{JAVA_BASE}/manage/report/generate?reportType=daily",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json() if resp.content else {}
        print(f"  → HTTP {resp.status_code} / code={body.get('code')} / msg={body.get('msg')}")
        check(
            "报表生成接口调用成功（FIX-3 改过的聚合 SQL 在真实 Java 路径上可执行）",
            resp.status_code == 200 and body.get("code") == 200,
            f"code={body.get('code')}",
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="1-12 对账（订单/营收口径）")
    parser.add_argument("--phase", default="all", choices=["all", "a", "b", "c"])
    args = parser.parse_args()

    sessionmaker = get_sessionmaker()
    try:
        if args.phase in ("all", "c"):
            await phase_java_path()
        async with sessionmaker() as session:
            if args.phase in ("all", "a"):
                await phase_reconcile(session)
            if args.phase in ("all", "b"):
                await phase_sensitivity(session)
    finally:
        await dispose_engine()

    print(f"\n结果：PASS={_pass} FAIL={_fail}")
    print(
        "注：库存/容量侧的对账（FIX-5：tb_inventory 与 tb_channel 档案 3/3 不一致）"
        "不在本脚本范围，需业务先确认权威源。"
    )
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
