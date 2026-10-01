"""排期任务 1-13：M2 验收「≥10 台真实设备全闭环」一键脚本 + 验收报告。

## 为什么要有这个脚本

M2 的验收标准是：**≥10 台真实设备走通「夜间分析 → 早晨确认 → 批量建单 → dkd-app 接单流转」**。
此前只能靠人手点页面 + 人肉对数：结论无法复跑（下一个人要重做一遍），也无法留下可比对的记录。
本脚本把这件事变成一条命令，并把**每一项判定的依据**打印出来。

## 默认只读（这是刻意的设计，不是保守）

本脚本**能建单**（`--confirm-write`）。两次真实事故的教训摆在那里（FIX-16 演练脚本在正常环境
把“故障相位”跑完并真建单 task 574；随后又有一次来源未查明的 task 575）——
共同结论是：**会改数据的脚本必须自带护栏**，不能靠“作者记得别点那个按钮”。

因此：
  · **默认只跑只读相位**（环境自检 + 真机盘点 + 计划核对），一行业务数据都不写；
  · 写相位需显式 `--confirm-write`，且跑前/跑后对活跃工单数做**精确对账**
    （新增数 ≠ 成功建单数 → FAIL 并列出差额），并把本次创建的 taskId/taskCode 全部打印；
  · 回退走 **Java REST**（`PUT /manage/task/cancel`，软删 `status=3`），
    **不做直连 SQL 改业务表**（AGENTS §1 约束 1：业务事实的唯一写入口是 Java）。

## 相位

| 相位 | 内容 | 是否写数据 |
| --- | --- | --- |
| [0] | 环境自检：MySQL / Python(:8090) / Java(:8080) / checkpointer 打开 | 否 |
| [1] | 真机盘点：运行中设备、区域覆盖、逐台货道缺口与容量一致性（FIX-5 数据质量） | 否 |
| [2] | 当日计划核对：`/agent/restock/plans` 与 `agent_restock_plan` 落库行数、数量夹取 | 否 |
| [3] | 闭环：逐台 `confirm` → 校验 `tb_task` + `task_id` 回写 → 幂等复点一次 | **是**（需 `--confirm-write`） |
| [4] | 验收报告：设备数 / 区域覆盖 / 闭环成功数 / 幂等 / 写护栏 / **未覆盖项** | 否 |

## 用法

```bash
cd dkd-agent
set -a; source ../.env; set +a

# 1) 只读预备验收（安全，随时可跑）
.venv/Scripts/python.exe ../docs/scripts/verify-1-13-m2-acceptance.py

# 2) 真跑闭环（会建工单！跑完会打印回退命令）
.venv/Scripts/python.exe ../docs/scripts/verify-1-13-m2-acceptance.py --confirm-write --max-devices 3

# 3) 回退本次创建的工单（走 Java 取消接口，软删）
.venv/Scripts/python.exe ../docs/scripts/verify-1-13-m2-acceptance.py --rollback-task 601 602
```

## 与「10 台」的关系（必读）

`--devices`（默认 10）是**验收目标**，不是脚本的开关。本机开发库里只有 3 台设备当天有补货计划，
所以**在本机跑不出 10 台闭环**——脚本会报告「闭环 N/10」并明确列出**缺什么**（没有计划 / 有在途工单 /
没有补货缺口），而不是把“设备数不够”伪装成脚本失败。M2 验收的真实前置是
**FIX-6（生产/测试库数据导出）**，拿到真实数据后本脚本才有意义。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.tools import read_tools  # noqa: E402

AGENT_BASE = os.environ.get("DKD_AGENT_BASE_URL", "http://127.0.0.1:8090")
JAVA_BASE = os.environ.get("DKD_AGENT_JAVA_BASE", "http://127.0.0.1:8080")
ADMIN_USER = os.environ.get("DKD_M2_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("DKD_M2_ADMIN_PASSWORD", "admin123")

TASK_TYPE_SUPPLY = 2
TASK_STATUS_ACTIVE = (1, 2)
DEVICE_TARGET_DEFAULT = 10

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


def info(message: str) -> None:
    print(f"  [信息] {message}")


async def refresh(session) -> None:
    """结束当前读事务，让后续查询能看到**别的连接刚写入**的数据。

    ## 为什么必须有这个（2026-10-01 首次跑写相位时真的撞到）

    本脚本的 session 在相位 1 的**第一次查询**就开启了 InnoDB 的 REPEATABLE READ 事务；
    而 confirm 建单是 Python 服务**另一条连接**写的。后果：
      · 读 `agent_restock_plan.task_id` 得到 NULL（写前快照，实际已回写 576）；
      · 活跃工单数“跑前 4 / 跑后 4” → **写护栏静默通过（0 == 0）**，而库里明明多了一张工单。

    第二条是真正危险的：护栏本身制造了假安全感——正是它要防的事。所以**写相位的每一次
    跨连接读数前都先 refresh**，而不是“记得最后再查一次”。
    """
    await session.commit()  # 语义是“这轮读到此为止”；rollback 也行，但 commit 更好读


# ----------------------------------------------------------------------------------
# 相位 0：环境自检（任一不通就停：后续所有判定的前提）
# ----------------------------------------------------------------------------------


async def phase_env(client: httpx.AsyncClient) -> dict:
    print("\n[0] 环境自检")
    out: dict = {"token": None, "blocked": []}

    try:
        health = (await client.get(f"{AGENT_BASE}/health", timeout=10.0)).json()
    except Exception as exc:  # noqa: BLE001
        check("Python 智能体可达（:8090/health）", False, f"{type(exc).__name__}: {exc}")
        out["blocked"].append("Python 服务不可达")
        return out
    check("Python 智能体可达（:8090/health）", True)
    check(
        "checkpointer 已打开（会话持久化可用）",
        health.get("checkpoint_open") is True,
        f"backend={health.get('checkpoint_backend')} 位置={str(health.get('checkpoint_location'))[:40]}",
    )
    out["health"] = health

    try:
        login = await client.post(
            f"{JAVA_BASE}/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
            timeout=15.0,
        )
        token = (login.json() or {}).get("token")
    except Exception as exc:  # noqa: BLE001
        token = None
        info(f"Java 登录异常：{type(exc).__name__}: {exc}")
    check("Java 后端登录成功（回退/取消都走 Java REST）", bool(token))
    out["token"] = token
    if not token:
        out["blocked"].append("Java 登录失败（检查 :8080 与账号密码）")
        return out

    status = await client.get(
        f"{JAVA_BASE}/agent/status", headers={"Authorization": f"Bearer {token}"}, timeout=15.0
    )
    data = ((status.json() or {}).get("data") or {}) if status.content else {}
    check(
        "Java 网关上报智能体已启用且 Python upstream=up",
        bool(data.get("enabled")) and data.get("upstream") == "up",
        f"enabled={data.get('enabled')} upstream={data.get('upstream')}",
    )
    return out


# ----------------------------------------------------------------------------------
# 相位 1：真机盘点（只读）
# ----------------------------------------------------------------------------------


async def phase_inventory(session, *, devices: int) -> list[dict]:
    print(f"\n[1] 真机盘点（只读）——目标是 {devices} 台")
    machines = await read_tools.list_operating_machines(session, limit=200)
    print(f"  → 运行中设备 {len(machines)} 台")
    check(
        f"运行中设备数 ≥ {devices}",
        len(machines) >= devices,
        f"实际 {len(machines)} 台（不足时 M2 需要更多真实设备数据）",
    )
    regions = sorted({m.region_id for m in machines if m.region_id})
    check(
        "设备跨 ≥2 个区域（1-8 的跨区域派单才有可验证性）",
        len(regions) >= 2,
        f"区域={regions}",
    )

    codes = [m.inner_code for m in machines]
    stock = await read_tools.list_channel_stock(session, inner_codes=codes)
    inflight = await read_tools.list_inflight_tasks(session, inner_codes=codes)
    inflight_codes = {t.inner_code for t in inflight}

    rows: list[dict] = []
    for machine in machines:
        mine = [s for s in stock if s.inner_code == machine.inner_code]
        gaps = [s for s in mine if (s.capacity or 0) > (s.current_stock or 0)]
        inconsistent = [s for s in mine if not s.data_consistent]
        rows.append(
            {
                "innerCode": machine.inner_code,
                "region": machine.region_name,
                "node": machine.node_name,
                "channels": len(mine),
                "gapChannels": len(gaps),
                "capacityInconsistent": len(inconsistent),
                "inflight": machine.inner_code in inflight_codes,
            }
        )

    print(f"  {'设备':<10} {'区域':<8} {'货道':>4} {'有缺口':>5} {'容量不一致':>7} {'在途':>4}")
    for row in rows:
        print(
            f"  {row['innerCode']:<10} {str(row['region'] or '-'):<8} {row['channels']:>4}"
            f" {row['gapChannels']:>5} {row['capacityInconsistent']:>7}"
            f" {'是' if row['inflight'] else '否':>4}"
        )
    check(
        "至少一台设备有补货缺口（否则当天不会有任何计划，闭环无从谈起）",
        any(r["gapChannels"] > 0 for r in rows),
        f"有缺口设备 {sum(1 for r in rows if r['gapChannels'] > 0)} 台",
    )
    inconsistent_total = sum(1 for r in rows if r["capacityInconsistent"] > 0)
    if inconsistent_total:
        info(
            f"{inconsistent_total} 台设备存在「库存表 vs 货道档案」容量不一致（FIX-5）："
            "建议量取哪个容量仍未拍板，本报告的容量判据以**货道档案**为准（read_tools 口径）"
        )
    return rows


# ----------------------------------------------------------------------------------
# 相位 2：当日计划核对（只读）
# ----------------------------------------------------------------------------------


async def phase_plans(session, client: httpx.AsyncClient, *, plan_date: date) -> tuple[list[dict], dict]:
    print(f"\n[2] 当日计划核对（只读）plan_date={plan_date.isoformat()}")
    resp = await client.get(
        f"{AGENT_BASE}/agent/restock/plans", params={"plan_date": plan_date.isoformat()}, timeout=30.0
    )
    body = resp.json() if resp.content else {}
    check("GET /agent/restock/plans 返回 200 信封", body.get("code") == 200, f"code={body.get('code')}")
    data = body.get("data") or {}
    plans = data.get("plans") or []

    await refresh(session)  # 见 refresh() 注释：跨连接读数必须重新开事务
    db_rows = (
        await session.execute(
            text(
                "select id, inner_code, status, task_id, items from agent_restock_plan "
                "where plan_date = :d and del_flag = '0'"
            ),
            {"d": plan_date},
        )
    ).mappings().all()
    check(
        "接口清单与库中计划行数一致",
        len(plans) == len(db_rows),
        f"接口 {len(plans)} 行 / 库 {len(db_rows)} 行",
    )

    # 数量夹取（AGENTS §7.6：LLM 输出必须容错 + 范围夹取）——这里的建议量来自规则兜底/校准，
    # 无论来源是什么，都必须满足 0 ≤ 建议量 ≤ 容量 - 现库存。越界说明夹取失效。
    bad: list[str] = []
    for plan in plans:
        for item in plan.get("items") or []:
            gap = int(item.get("maxCapacity") or 0) - int(item.get("currentQuantity") or 0)
            qty = int(item.get("suggestedQuantity") or 0)
            if qty < 0 or qty > max(gap, 0):
                bad.append(f"{plan.get('innerCode')}/{item.get('channelCode')}: {qty} > 缺口 {gap}")
    check(
        "建议量在合法区间内（0 ≤ 建议量 ≤ 容量-现库存）",
        not bad,
        f"越界 {len(bad)} 条" + (f"：{bad[:3]}" if bad else ""),
    )

    print(f"  → 当日计划 {len(plans)} 条（待处理 {data.get('stats', {}).get('pending')} / "
          f"紧急 {data.get('stats', {}).get('urgent')} / 合计 {data.get('stats', {}).get('totalQuantity')} 件）")
    return plans, data


# ----------------------------------------------------------------------------------
# 相位 3：闭环（写业务数据；需 --confirm-write）
# ----------------------------------------------------------------------------------


async def _active_task_count(session) -> int:
    """活跃**补货**工单数（写护栏的基线指标）。

    只看补货类型：本脚本只会建补货工单，用它当基线才能把“脚本建的”与“别的地方建的”区分开。
    不用全量 task 计数（那会把巡检/维修工单的变动算进来，护栏会误报）。
    """
    return int(
        (
            await session.execute(
                text(
                    "select count(*) from tb_task "
                    "where task_status in (1, 2) and product_type_id = :pt"
                ),
                {"pt": TASK_TYPE_SUPPLY},
            )
        ).scalar()
        or 0
    )


async def phase_closed_loop(
    session,
    client: httpx.AsyncClient,
    *,
    plans: list[dict],
    rows: list[dict],
    plan_date: date,
    max_devices: int,
    user_id: int,
) -> dict:
    print(f"\n[3] 闭环：确认建单（**写业务数据**，最多 {max_devices} 台）")
    ready = [r["innerCode"] for r in rows if r["gapChannels"] > 0 and not r["inflight"]]
    candidates = [p for p in plans if p.get("innerCode") in ready and p.get("status") in (1, 2, 6)]
    print(f"  → 具备建单条件的计划 {len(candidates)} 条（无在途工单 + 有补货缺口 + 计划未终结）")
    if not candidates:
        check(
            "存在可闭环的计划（否则无法验证建单链路）",
            False,
            "当天没有可确认的计划：可能已全部处理完、或设备都有在途工单",
        )
        return {"created": [], "skipped": True}

    await refresh(session)  # 基线必须在**当前时刻**读，而不是相位 1 开始时的快照
    before = await _active_task_count(session)
    info(f"写护栏基线：活跃补货工单 {before} 张（跑完必须只增加“成功建单”的数量）")
    created: list[dict] = []
    for plan in candidates[:max_devices]:
        plan_id = int(plan["id"])
        code = plan["innerCode"]
        resp = await client.post(
            f"{AGENT_BASE}/agent/restock/plans/{plan_id}/confirm",
            params={"plan_date": plan_date.isoformat()},
            headers={"X-Agent-User": str(user_id), "X-Request-Id": f"m2-accept-{plan_id}"},
            timeout=60.0,
        )
        body = resp.json() if resp.content else {}
        ok = resp.status_code == 200 and (body.get("code") == 200)
        check(
            f"{code} 确认建单成功（plan_id={plan_id}）",
            ok,
            f"HTTP {resp.status_code} " + json.dumps(body.get("data") or body.get("msg"), ensure_ascii=False)[:90],
        )
        if not ok:
            continue

        # 关键：confirm 是服务端另一条连接写的，必须重开事务才能读到（见 refresh 注释）
        await refresh(session)
        db_row = (
            await session.execute(
                text(
                    "select p.task_id, p.status, t.task_code, t.task_status, t.inner_code, t.user_id "
                    "from agent_restock_plan p left join tb_task t on t.task_id = p.task_id "
                    "where p.id = :pid"
                ),
                {"pid": plan_id},
            )
        ).mappings().first()
        task_id = int((db_row or {}).get("task_id") or 0)
        check(
            f"{code} 计划已回写 task_id（幂等对账依赖它）",
            task_id > 0,
            f"task_id={task_id}",
        )
        check(
            f"{code} 建出的工单在库中为 1-待接单且设备一致",
            bool(db_row)
            and int(db_row["task_status"] or 0) == 1
            and db_row["inner_code"] == code,
            f"taskCode={db_row and db_row['task_code']} status={db_row and db_row['task_status']}",
        )
        if task_id:
            created.append(
                {
                    "planId": plan_id,
                    "innerCode": code,
                    "taskId": task_id,
                    "taskCode": (db_row or {}).get("task_code"),
                }
            )

            # 幂等复点：FIX-15 契约——再点一次不得建出第二张工单
            again = await client.post(
                f"{AGENT_BASE}/agent/restock/plans/{plan_id}/confirm",
                params={"plan_date": plan_date.isoformat()},
                headers={"X-Agent-User": str(user_id), "X-Request-Id": f"m2-accept-again-{plan_id}"},
                timeout=60.0,
            )
            again_body = again.json() if again.content else {}
            await refresh(session)
            still_task_id = int(
                (
                    await session.execute(
                        text("select task_id from agent_restock_plan where id = :pid"),
                        {"pid": plan_id},
                    )
                ).scalar()
                or 0
            )
            same_task = again.status_code == 200 and still_task_id == task_id
            check(
                f"{code} 重复确认幂等（不会建出第二张工单）",
                same_task,
                f"HTTP {again.status_code} msg={str(again_body.get('msg'))[:40]} task_id 仍为 {still_task_id}",
            )

    await refresh(session)
    after = await _active_task_count(session)
    delta = after - before
    check(
        "写护栏：活跃补货工单新增数 == 成功建单数（不多也不少）",
        delta == len(created),
        f"新增 {delta} / 成功 {len(created)}"
        + ("" if delta == len(created) else "  ← 有未记录的业务写入，必须人工核查！"),
    )

    if created:
        print("\n  [本次创建的工单（回退命令见下）]")
        for item in created:
            print(f"    plan={item['planId']} 设备={item['innerCode']} taskId={item['taskId']} 工单号={item['taskCode']}")
        ids = " ".join(str(item["taskId"]) for item in created)
        print(f"    回退：--rollback-task {ids}（走 Java PUT /manage/task/cancel，软删 status=3）")
    return {"created": created, "skipped": False}


# ----------------------------------------------------------------------------------
# 回退：软取消（走 Java REST，不直连 SQL 改业务表）
# ----------------------------------------------------------------------------------


async def rollback_tasks(task_ids: list[int]) -> int:
    print(f"\n[回退] 软取消工单 {task_ids}（Java PUT /manage/task/cancel）")
    async with httpx.AsyncClient(timeout=30.0) as client:
        login = await client.post(
            f"{JAVA_BASE}/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
        )
        token = (login.json() or {}).get("token")
        if not token:
            check("回退前 Java 登录", False, login.text[:120])
            return 1
        for task_id in task_ids:
            resp = await client.put(
                f"{JAVA_BASE}/manage/task/cancel",
                json={"taskId": task_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json() if resp.content else {}
            check(
                f"工单 {task_id} 已软取消",
                resp.status_code == 200 and body.get("code") == 200,
                f"HTTP {resp.status_code} msg={body.get('msg')}",
            )
    print("  说明：取消是**软删**（task_status=3），工单与留痕都保留，符合 AGENTS §7.4。")
    return 1 if _fail else 0


# ----------------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description="M2 验收：10 台真机全闭环（默认只读）")
    parser.add_argument("--devices", type=int, default=DEVICE_TARGET_DEFAULT, help="验收目标设备数")
    parser.add_argument("--max-devices", type=int, default=5, help="本次最多闭环几台（写相位）")
    parser.add_argument("--confirm-write", action="store_true", help="真的建单（会写业务数据）")
    parser.add_argument("--plan-date", default=date.today().isoformat())
    parser.add_argument("--user-id", type=int, default=1, help="确认人（写 agent_decision_log 用）")
    parser.add_argument("--rollback-task", type=int, nargs="*", default=None)
    args = parser.parse_args()

    if args.rollback_task is not None:
        if not args.rollback_task:
            print("未指定工单号：--rollback-task 601 602 ...")
            return 2
        try:
            return await rollback_tasks(args.rollback_task)
        finally:
            await dispose_engine()

    plan_date = date.fromisoformat(args.plan_date)
    print("=" * 78)
    print(f"M2 验收（1-13）：目标 {args.devices} 台真机全闭环 | plan_date={plan_date}")
    print(f"模式：{'**写业务数据**（--confirm-write）' if args.confirm_write else '只读预备验收（不写任何业务数据）'}")
    print("=" * 78)

    report: dict = {"planDate": plan_date.isoformat(), "devices": args.devices, "mode": "write" if args.confirm_write else "read-only"}
    sessionmaker = get_sessionmaker()
    try:
        async with httpx.AsyncClient() as client:
            env = await phase_env(client)
            if env.get("blocked"):
                print(f"\n⛔ 环境自检未通过，停止（**没有写任何数据**）：{env['blocked']}")
                report["blocked"] = env["blocked"]
                return 2

            async with sessionmaker() as session:
                rows = await phase_inventory(session, devices=args.devices)
                plans, stats = await phase_plans(session, client, plan_date=plan_date)
                report["machineCount"] = len(rows)
                report["planCount"] = len(plans)
                report["stats"] = stats.get("stats")

                result = {"created": [], "skipped": True}
                if args.confirm_write:
                    result = await phase_closed_loop(
                        session,
                        client,
                        plans=plans,
                        rows=rows,
                        plan_date=plan_date,
                        max_devices=args.max_devices,
                        user_id=args.user_id,
                    )
                else:
                    ready = [r for r in rows if r["gapChannels"] > 0 and not r["inflight"]]
                    print("\n[3] 闭环相位已跳过（默认只读；加 --confirm-write 才会建单）")
                    print(f"  → 当前具备闭环条件：{len(ready)} 台"
                          f"（设备总 {len(rows)} 台 / 当日计划 {len(plans)} 条）")
                    report["closedLoopReady"] = len(ready)

                # [4] 验收报告
                print("\n[4] 验收报告")
                closed = len(result["created"])
                report["closedLoopCount"] = closed
                report["verdict"] = "PASS" if closed >= args.devices else "BLOCKED（未达目标）"
                print(f"  · 设备总数（运行中）    : {len(rows)}")
                print(f"  · 跨区域                : {len({r['region'] for r in rows if r['region']})} 个")
                print(f"  · 当日计划              : {len(plans)} 条")
                print(f"  · 本次闭环成功          : {closed} 台 / 目标 {args.devices} 台")
                print(f"  · 结论                  : {report['verdict']}")
                if closed < args.devices:
                    missing = args.devices - closed
                    print(
                        f"  · **未达目标的真实原因（不是脚本失败）**：还差 {missing} 台。"
                        "本机开发库只有少量设备当天有补货计划；"
                        "M2 的真实前置是 FIX-6（生产/测试库数据导出）。"
                    )
                print("  · 未覆盖（如实说明）：dkd-app 的**接单流转**需要移动端/真机人工操作，")
                print("    本脚本只验到「工单已建出且状态=1-待接单」；接单→完成由运维在 dkd-app 上做。")
                print(f"\n结果：PASS={_pass} FAIL={_fail}")
                report["checks"] = {"pass": _pass, "fail": _fail}
                print("\nREPORT_JSON " + json.dumps(report, ensure_ascii=False))
    finally:
        await dispose_engine()
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
