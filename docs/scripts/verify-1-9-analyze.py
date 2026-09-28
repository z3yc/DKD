"""排期任务 1-9 端到端验收脚本（真 MySQL + 真实 FastAPI 应用，同一事件循环）。

验收标准原文：**手动触发成功；连续失败告警到运维；任务重入安全**。

分工：
- **本脚本**证明 Python 侧的三件事——① 服务间密钥鉴权与 200 信封；② 定时入口真的消费
  `agent_restock_pause`（暂停时**不跑图、不产生计划**）；③ 同一日期重复触发幂等 + 每次触发
  都在 `agent_decision_log` 留有可检索的痕迹（含 `request_id`，供 Java 侧作业日志对照）。
- **Java 侧的告警路径**由 `AgentTaskTest`（10 项单测）与 SQL 注册脚本证明：
  最终失败会抛异常 → RuoYi 写入 `sys_job_log` 失败记录；`docs/ddl/agent_restock_job.sql` 注册 06:00 任务。

```
cd dkd-agent
set -a; source ../.env; set +a
.venv/Scripts/python.exe ../docs/scripts/verify-1-9-analyze.py
```

脚本默认**关闭 LLM 校准**（`--calibration` 可开，会真实调用 DeepSeek 并产生费用）：
本机验收的既有约定见 verify-1-6/1-7；真实校准链路的 live 验证由 FIX-11 单独安排。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import dispose_engine, get_sessionmaker  # noqa: E402

# 必须在 create_app / get_settings 之前设置：Settings 读环境变量（.env 里默认可能是开的）
os.environ.setdefault("DKD_AGENT_RESTOCK_CALIBRATION_ENABLED", "0")

TODAY = date.today().isoformat()
RUN_TAG = f"verify-1-9-{int(time.time())}"
SECRET = os.environ.get("DKD_AGENT_SERVICE_SECRET", "")

_ok = 0
_fail = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _ok, _fail
    if condition:
        _ok += 1
        print(f"  PASS  {label}" + (f" —— {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  FAIL  {label}" + (f" —— {detail}" if detail else ""))


def section(title: str) -> None:
    print()
    print(title)


async def plan_rows() -> list[dict]:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                "select id, vm_id, inner_code, status, total_quantity, del_flag, update_time "
                "from agent_restock_plan where plan_date = :d order by vm_id"
            ),
            {"d": TODAY},
        )
        return [dict(row) for row in result.mappings().all()]


async def audit_rows(request_id: str) -> list[dict]:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                "select action, trigger_type, target_type, target_id, user_id, result, "
                "request_id, llm_output, input_context from agent_decision_log "
                "where request_id = :rid order by id"
            ),
            {"rid": request_id},
        )
        return [dict(row) for row in result.mappings().all()]


def as_dict(raw: object) -> dict:
    """把留痕的 input_context 转成 dict。

    为什么需要：本脚本用裸 SQL 查 MySQL 的 JSON 列，驱动按**文本**返回（不是 dict）；
    解析失败就返回空 dict，让下面的断言**失败并打印实际值**——不静默当作通过。
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def read_pause_row() -> dict | None:
    """读开关行原值（脚本结束时要**原样还原**，不能把别人的暂停状态弄丢）。"""
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                "select paused, reason, paused_by, paused_time, resumed_by, resumed_time, del_flag "
                "from agent_restock_pause where scope = 'global' limit 1"
            )
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def write_pause_row(paused: bool, reason: str) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "insert into agent_restock_pause "
                "  (scope, paused, reason, paused_by, paused_time, create_by, create_time, "
                "   update_by, update_time, del_flag) "
                "values ('global', :paused, :reason, :by, now(), :by, now(), :by, now(), '0') "
                "on duplicate key update paused = values(paused), reason = values(reason), "
                "  paused_by = :by, paused_time = now(), update_by = :by, update_time = now(), "
                "  del_flag = '0'"
            ),
            {"paused": 1 if paused else 0, "reason": reason, "by": RUN_TAG},
        )
        await session.commit()


async def restore_pause_row(original: dict | None) -> str:
    """还原开关行；原本不存在则软删本次插入的行（不物理删除，AGENTS §7.4）。"""
    async with get_sessionmaker()() as session:
        if original is None:
            await session.execute(
                text(
                    "update agent_restock_pause set del_flag = '1', update_by = :by "
                    "where scope = 'global'"
                ),
                {"by": RUN_TAG},
            )
            action = "本次无原行 → 软删脚本插入的开关行"
        else:
            await session.execute(
                text(
                    "update agent_restock_pause set paused = :paused, reason = :reason, "
                    "paused_by = :paused_by, paused_time = :paused_time, resumed_by = :resumed_by, "
                    "resumed_time = :resumed_time, del_flag = :del_flag, update_by = :by "
                    "where scope = 'global'"
                ),
                {**original, "by": RUN_TAG},
            )
            action = f"已还原原有开关行（paused={original['paused']}）"
        await session.commit()
    return action


async def cleanup(original_pause: dict | None) -> None:
    """验收数据软删（非 DELETE：AGENTS §7.4，账号也没有 DELETE 权限）。"""
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "update agent_restock_plan set del_flag = '1', update_by = :by where plan_date = :d"
            ),
            {"by": RUN_TAG, "d": TODAY},
        )
        await session.commit()
    print(f"  [清理] 软删 {TODAY} 的计划行；{await restore_pause_row(original_pause)}")
    print("  [清理] 留痕行**不清理**（agent_decision_log 是审计证据，与 verify-1-7 同约定）")


async def run() -> int:
    from app.main import create_app

    original_pause = await read_pause_row()
    app = create_app()
    print("=" * 88)
    print(f"[1-9 验收] planDate={TODAY} 校准={'开（会真实调用 LLM）' if _calibration else '关'}")
    print(f"[1-9 验收] run tag={RUN_TAG}（用于 request_id 与清理标记）")
    print("=" * 88)
    if not SECRET:
        print("  环境变量 DKD_AGENT_SERVICE_SECRET 为空：接口会按设计返回 503，本脚本无法继续")
        return 1

    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://agent.local") as client:
                section("[A] 鉴权（服务间密钥，AGENTS §7.5：与用户 JWT 是两套体系）")
                resp = await client.post("/agents/restock/analyze", json={"planDate": TODAY})
                check("无密钥 → 401", resp.status_code == 401, f"实际 {resp.status_code}")
                resp = await client.post(
                    "/agents/restock/analyze",
                    json={"planDate": TODAY},
                    headers={"X-Agent-Secret": "forged"},
                )
                check("伪造密钥 → 401", resp.status_code == 401, f"实际 {resp.status_code}")
                resp = await client.post(
                    "/agents/restock/analyze",
                    json={"planDate": "2026/09/21"},
                    headers={"X-Agent-Secret": SECRET},
                )
                check("日期格式非法 → 400", resp.status_code == 400, f"实际 {resp.status_code}")

                section("[B] 暂停开关被消费（FIX-10 的闭环：暂停时不得跑图、不得生成计划）")
                await write_pause_row(True, "验收：暂停自动分析")
                before = await plan_rows()
                rid_skip = f"{RUN_TAG}-skip"
                resp = await client.post(
                    "/agents/restock/analyze",
                    json={"planDate": TODAY},
                    headers={"X-Agent-Secret": SECRET, "X-Request-Id": rid_skip},
                )
                body = resp.json()
                check(
                    "被暂停 → HTTP 200 + data.status=skipped（不是错误）",
                    resp.status_code == 200 and body["data"]["status"] == "skipped",
                    f"实际 {resp.status_code}/{body.get('data', {}).get('status')}",
                )
                check(
                    "skipped 带出暂停原因与操作人",
                    bool(body["data"]["reason"]) and body["data"]["pausedBy"] == RUN_TAG,
                    f"reason={body['data']['reason']} pausedBy={body['data']['pausedBy']}",
                )
                after = await plan_rows()
                check(
                    "暂停期间计划行未被改动（不跑图）",
                    [r["update_time"] for r in before] == [r["update_time"] for r in after],
                    f"行数 {len(before)}→{len(after)}",
                )
                await write_pause_row(False, "验收：恢复自动分析")

                section("[C] 正常触发（计划在人工确认处中断，但已在中断前落库）")
                rid_run = f"{RUN_TAG}-run"
                resp = await client.post(
                    "/agents/restock/analyze",
                    json={"planDate": TODAY},
                    headers={"X-Agent-Secret": SECRET, "X-Request-Id": rid_run},
                )
                data = resp.json().get("data", {})
                check("触发成功 → 200 + analyzed", resp.status_code == 200 and data.get("status") == "analyzed")
                print(
                    f"  返回: 设备={data.get('deviceCount')} 货道={data.get('channelCount')} "
                    f"建议总数={data.get('totalQuantity')} 中断={data.get('interrupted')} "
                    f"threadId={data.get('threadId')}"
                )
                check("threadId 与 1-6 约定一致", data.get("threadId") == f"restock-{TODAY}")
                rows = await plan_rows()
                live = [r for r in rows if r["del_flag"] == "0"]
                check(
                    "库中确有计划行（工作台的数据源，不依赖返回值自证）",
                    len(live) == data.get("deviceCount"),
                    f"库中 {len(live)} 行 / 返回 {data.get('deviceCount')} 台设备",
                )

                section("[D] 任务重入安全（同一日期重复触发）")
                rid_repeat = f"{RUN_TAG}-repeat"
                resp = await client.post(
                    "/agents/restock/analyze",
                    json={"planDate": TODAY},
                    headers={"X-Agent-Secret": SECRET, "X-Request-Id": rid_repeat},
                )
                check("重复触发仍 200", resp.status_code == 200, f"实际 {resp.status_code}")
                again = await plan_rows()
                check(
                    "计划行数不变（(vm_id, plan_date) 唯一键 + 幂等 upsert）",
                    len(again) == len(rows),
                    f"{len(rows)} → {len(again)}",
                )

                section("[E] 决策留痕（AGENTS §6.3：定时分析也是写操作决策）")
                skip_rows = await audit_rows(rid_skip)
                check("暂停那次留痕 1 条", len(skip_rows) == 1, f"实际 {len(skip_rows)}")
                if skip_rows:
                    entry = skip_rows[0]
                    check(
                        "action/trigger_type/user_id 正确（定时任务无操作人）",
                        entry["action"] == "restock.analyze"
                        and int(entry["trigger_type"]) == 2
                        and entry["user_id"] is None,
                        f"action={entry['action']} trigger={entry['trigger_type']} user={entry['user_id']}",
                    )
                    check(
                        "暂停留痕指向开关行且 result=成功",
                        entry["target_type"] == "restock_pause"
                        and entry["target_id"] == "global"
                        and int(entry["result"]) == 1,
                    )
                    check("人工/定时入口不得写 llm_output", entry["llm_output"] is None)
                run_rows = await audit_rows(rid_run)
                check("分析那次留痕 1 条", len(run_rows) == 1, f"实际 {len(run_rows)}")
                if run_rows:
                    context = as_dict(run_rows[0]["input_context"])
                    check(
                        "留痕含设备/货道/总数与 effect",
                        context.get("effect") == "analyzed"
                        and context.get("deviceCount") == data.get("deviceCount"),
                        f"effect={context.get('effect')} deviceCount={context.get('deviceCount')}",
                    )
                    check(
                        "留痕对象是本轮运行（target_id=计划日期）",
                        run_rows[0]["target_type"] == "restock_run"
                        and run_rows[0]["target_id"] == TODAY,
                    )
                repeat_rows = await audit_rows(rid_repeat)
                check("重复触发同样留痕（每次触发都可审计）", len(repeat_rows) == 1, f"实际 {len(repeat_rows)}")
                check(
                    "三次触发的留痕都可按 request_id 检索到",
                    len(skip_rows) + len(run_rows) + len(repeat_rows) == 3,
                )
    finally:
        try:
            await cleanup(original_pause)
        finally:
            await dispose_engine()

    print()
    print("=" * 88)
    print(f"[1-9 验收] PASS={_ok} FAIL={_fail}")
    print("=" * 88)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="排期 1-9 定时分析端到端验收")
    parser.add_argument(
        "--calibration",
        action="store_true",
        help="打开 LLM 校准（会真实调用 DeepSeek 并产生费用；默认关闭）",
    )
    args = parser.parse_args()
    _calibration = args.calibration
    if _calibration:
        os.environ["DKD_AGENT_RESTOCK_CALIBRATION_ENABLED"] = "1"
    sys.exit(asyncio.run(run()))
