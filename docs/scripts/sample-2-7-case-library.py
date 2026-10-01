"""排期任务 2-7 前置：故障案例库**数据质量抽样**（也是知识库启用门槛的可复跑检查）。

## 为什么先做这个而不是先建向量表

排期 §三 2-7 的完成标准写着「**前置：先做 0.5d 数据质量抽样，不达标则改口径或降级为规则库**」，
PG 选型评审 §4.3 也写了「案例抽取口径尚未冻结，先建表会让口径被表结构反向绑架」。

本脚本就是那道抽样门槛。它回答一个问题：
**MySQL 里到底有没有足够的、可结构化的故障案例，能支撑 Phase 2 的“相似案例召回”？**

## 判定口径（不达标就降级为规则库，这是排期预先定好的处置，不是临时发挥）

| 要件 | 门槛 | 理由 |
| --- | --- | --- |
| 案例条数 | ≥50 | 排期 2-7 的验收标准 |
| 现象文本可用 | 平均长度 ≥15 字且非空率 ≥80% | 语义检索的输入，太短无法区分故障 |
| 原因/处置 | 存在可结构化的字段或稳定格式 | 检索出来要能“给处置建议”，只有现象没用 |
| 故障类型可分辨 | 存在故障码/型号等可分类维度 | 相似案例召回要有可比维度 |

任一条不达标 → **降级为规则库**（按设备/型号/近期异常特征做规则检索），
并把「启用知识库」写成带量化门槛的待办，而不是硬上。

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a
.venv/Scripts/python.exe ../docs/scripts/sample-2-7-case-library.py
```

脚本**只读**，不写任何表；输出量化报告 + 结论 + 建议的启用条件。
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import asyncio  # noqa: E402
import os  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.db import dispose_engine, get_sessionmaker  # noqa: E402

# 门槛（与排期 2-7 的验收标准对齐；改这里就等于改门槛，会直接改变结论）
MIN_CASES = 50
MIN_AVG_DESC_LEN = 15.0
MIN_NON_EMPTY_RATIO = 0.8

_pass = 0
_fail = 0
_gate = []  # (要件, 实测值, 是否达标)


def record(name: str, actual: str, ok: bool) -> None:
    global _pass, _fail
    if ok:
        _pass += 1
    else:
        _fail += 1
    _gate.append((name, actual, ok))
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {name}：{actual}")


async def main() -> int:
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            print("\n[1] 工单类型分布（故障案例只可能来自维修类工单）")
            rows = (
                await session.execute(
                    text(
                        "select product_type_id, count(*) c from tb_task "
                        "group by product_type_id order by product_type_id"
                    )
                )
            ).all()
            for row in rows:
                print(f"  · product_type_id={row.product_type_id} → {row.c} 条")

            # 维修类 = product_type_id 1（DkdContants.TASK_TYPE_*，2 为补货）
            repair = (
                await session.execute(
                    text("select count(*) from tb_task where product_type_id = 1")
                )
            ).scalar()
            record("维修类工单条数 ≥50", f"{repair} 条", int(repair) >= MIN_CASES)

            print("\n[2] 现象文本可用性（tb_task.desc）")
            desc_stats = (
                await session.execute(
                    text(
                        "select count(*) total, "
                        "sum(case when `desc` is null or `desc` = '' then 1 else 0 end) empty_cnt, "
                        "coalesce(avg(char_length(`desc`)), 0) avg_len "
                        "from tb_task where product_type_id = 1"
                    )
                )
            ).first()
            total = int(desc_stats.total or 0)
            empty_cnt = int(desc_stats.empty_cnt or 0)
            avg_len = float(desc_stats.avg_len or 0)
            non_empty_ratio = (1 - empty_cnt / total) if total else 0.0
            record(
                f"描述非空率 ≥{MIN_NON_EMPTY_RATIO:.0%}",
                f"{non_empty_ratio:.0%}（空 {empty_cnt}/{total}）",
                non_empty_ratio >= MIN_NON_EMPTY_RATIO,
            )
            record(
                f"平均描述长度 ≥{MIN_AVG_DESC_LEN:.0f} 字",
                f"{avg_len:.1f} 字",
                avg_len >= MIN_AVG_DESC_LEN,
            )

            print("\n[3] 实际内容抽样（数值不达标时，看内容才知道是“少”还是“没有”）")
            samples = (
                await session.execute(
                    text(
                        "select task_id, left(`desc`, 40) d from tb_task "
                        "where product_type_id = 1 order by task_id limit 10"
                    )
                )
            ).all()
            for row in samples:
                shown = (row.d or "").strip() or "（空）"
                print(f"  · task {row.task_id}: {shown}")

            print("\n[4] 原因/处置字段是否存在（案例库要能给出“怎么修”，只有现象没用）")
            columns = (
                await session.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = database() and table_name = 'tb_task'"
                    )
                )
            ).all()
            # 用位置取值而不是 row.column_name：information_schema 在不同 MySQL/驱动组合下
            # 返回的列名大小写不一致（实测本机取属性会 NoSuchColumnError）。
            names = {str(row[0]).lower() for row in columns}
            # 可能的“原因/处置”字段名（含中文注释型字段名不存在时的兜底）
            candidate = {"cause", "reason", "solution", "handle", "handle_desc", "repair",
                         "fault_code", "error_code", "vm_type_id", "device_type"}
            hits = sorted(names & candidate)
            record(
                "存在原因/处置/故障码类字段",
                f"命中的字段：{hits or '无'}（tb_task 全部字段：{sorted(names)}）",
                bool(hits),
            )

            print("\n[5] 可分类维度（相似案例召回需要可比维度）")
            type_scoped = (
                await session.execute(
                    text(
                        "select count(distinct inner_code) vm_cnt, "
                        "count(distinct task_code) code_cnt from tb_task where product_type_id = 1"
                    )
                )
            ).first()
            print(f"  · 涉及设备数 {type_scoped.vm_cnt}、工单号数 {type_scoped.code_cnt}")
    finally:
        await dispose_engine()

    print("\n================ 门槛汇总 ================")
    for name, actual, ok in _gate:
        print(f"  {'✔' if ok else '✘'} {name} —— {actual}")
    print(f"\n结果：达标 {_pass} 项 / 未达标 {_fail} 项")

    if _fail:
        print("\n⛔ 结论：**数据质量不达标 → 按排期 2-7 的预案降级为规则库**")
        print("   不要在向量表上建案例库：数据源不存在时，建出来的只会是“看起来很有技术含量”的空库，")
        print("   而 Phase 2 的“相似案例召回”会因此给出不可验证的检索结果（比没有更危险）。")
        print("\n   建议的启用条件（可复跑本脚本检查）：")
        print(f"   ① 维修类工单 ≥ {MIN_CASES} 条，且描述为真实故障现象（而非“投放/加急/不想干了”）；")
        print(f"   ② 描述非空率 ≥ {MIN_NON_EMPTY_RATIO:.0%}、平均长度 ≥ {MIN_AVG_DESC_LEN:.0f} 字；")
        print("   ③ 存在结构化的原因/处置字段（或稳定格式），并冻结抽取口径后再建表；")
        print("   ④ 有可分类维度（故障码 / 设备型号），否则“相似”无从定义。")
        print("\n   ⇒ 在那之前，2-2 的检索走规则库（设备 + 型号 + 近期异常特征），")
        print("     并在无案例命中时使用保守话术，不假装有历史案例支撑。")
    else:
        print("\n✅ 结论：数据质量达标，可以进入案例抽取口径冻结 → 建表 → 回填链路。")

    return 1 if _fail else 0


if __name__ == "__main__":
    os.environ.setdefault("DKD_AGENT_AUDIT_ENABLED", "0")
    sys.exit(asyncio.run(main()))
