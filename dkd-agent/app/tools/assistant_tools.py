"""AI 助手可调用的**只读**工具集（2026-10-01 补齐智能功能）。

## 为什么单独一个文件，而不是让助手复用 `read_tools`

`read_tools` 的调用方是**编排好的图**（1-1/1-2/1-4）：参数由代码决定，返回的是给程序用的完整
数据模型（`ChannelStockItem` 等）。而这里的调用方是**模型**：

- 参数来自自然语言 → 必须**容错**（可空、格式可能不对），不能因为模型多打一个空格就抛异常；
- 返回值**直接进 prompt** → 必须紧凑、有行数上限、并**自带口径说明**（否则模型会把
  “近 30 天出货成功订单”说成“最近销量”，口径就飘了）；
- 失败的表达方式不同：图的失败要**抛**（上层决策），模型的失败要**喂回去**（让它如实解释）。

所以这里做的是“给模型看的窄接口”，底层仍然复用同一批 `read_tools` 函数——
**没有第二条取数通道**，口径只有一处（不然会出现“页面一个数、助手另一个数”）。

## 三条硬约束（都写进代码，不只写在注释里）

1. **只读**：本文件导出的工具**没有任何写操作**。建单、确认、调整这些全部留在
   补货工作台里走**人工确认**（AGENTS §7.9：首期所有写操作必须 human-in-the-loop）。
   助手能做的最后一公里是“把数据说清楚 + 告诉你去哪个页面动手”。
2. **不编数**：所有数字都来自工具返回；工具报错时返回 `{"error": ...}`，
   提示词明确要求“工具报错就如实说，不要猜”。
3. **有界**：每个工具都有行数/设备数上限，并在返回值里标注是否被截断——
   既控制 token 成本，也避免“看到前 20 条就以为是全部”。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.constants import CASE_WINDOW_DAYS_ASSISTANT
from app.db import get_sessionmaker
from app.graphs.restock_decisions import summarize_plan
from app.graphs.restock_plan_store import SqlPauseStore, SqlPlanStore, plan_from_row
from app.graphs.restock_state import status_label
from app.prompts.assistant_chat import tool_names_for_scene
from app.services import time_hint
from app.services.diagnosis import compose_diagnosis
from app.tools import read_tools
from app.tools.read_tools import ToolError, default_sales_window

logger = logging.getLogger("dkd.agent.tools.assistant")

# 优先级中文名：必须与前端（views/manage/restock/index.vue 的 priorityLabel）
# 和 DDL 注释（agent_restock_plan.items 的 priority 1-4）保持一致
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "紧急"}

# 单次返回的规模上限（token 成本与“误导性截断”的平衡点）
#
# 为什么这些数字在 2026-10-01 被收紧（§十 任务 7）：实测一次带工具的问答 input ≈ **3.5k token**，
# 是普通对话的 ~10 倍——因为 system 提示词 + 工具 schema + **工具返回值**都要进上下文，
# 而返回值里的每一轮都会**再算一次**。三处一起压：
#   ① 只回未完成计划（已完成的计划对“今天要补什么”毫无用处，却要每轮付一次 token）；
#   ② 货道明细默认**只给含紧急货道的计划**（其余只给台号+汇总），需要细节时用 detail=true 再查一次；
#   ③ reason 截断（规则兜底的理由里有大段模板文本，保留结论就够模型解释“为什么补”）。
MAX_PLANS = 10
MAX_CHANNELS_PER_PLAN = 5
MAX_STOCK_CHANNELS = 20
MAX_INFLIGHT = 20
# reason 保留长度：够表达“缺货/低于预警值/常规补货”的结论，不含大段模板
MAX_REASON_CHARS = 60
# 未完成计划状态（1-建议 2-已调整 6-待指派），与 PLAN_STATUS_* / 前端筛选一致
PENDING_PLAN_STATUSES = (1, 2, 6)


def _clip(text: Any, limit: int = MAX_REASON_CHARS) -> str | None:
    """截断长文本并显式标注（**不能静默截断**：模型会拿截断后的句子当完整理由讲）。"""
    if text is None:
        return None
    value = str(text).strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…（已截断）"


def _json(payload: object) -> str:
    """工具返回值统一序列化：`ensure_ascii=False` 让模型看到可读中文（也省 token）。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _parse_date(text: str | None) -> date:
    """容错解析日期：模型可能给 '2026-10-01'、'2026/10/01' 或空串。

    为什么容错而不是抛：模型给错日期的代价应该是“助手说一句日期没看懂”，
    而不是整轮对话 500——后者会让用户以为服务坏了（正是本轮刚修过的那类误判）。
    """
    if not text:
        return date.today()
    cleaned = str(text).strip().replace("/", "-").replace(".", "-")
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        return date.today()


def _priority_label(value: Any) -> str:
    try:
        return PRIORITY_LABELS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


# ----------------------------------------------------------------------------------
# 1) 当日补货建议（回答「先看紧急的」这类问题）
# ----------------------------------------------------------------------------------


class _PlansArgs(BaseModel):
    plan_date: str | None = Field(
        default=None,
        description="计划日期，格式 YYYY-MM-DD；不填表示今天",
    )
    detail: bool = Field(
        default=False,
        description=(
            "是否返回每条建议的全部货道明细。默认 false：只给含紧急货道的计划明细，"
            "其余只给台号与汇总（控制返回体积）。需要看某台设备各货道时置 true。"
        ),
    )


def _plan_brief(plan: dict[str, Any], *, with_channels: bool) -> dict[str, Any]:
    """把一条计划压成给模型看的摘要（`with_channels=False` 时**不返回货道明细**）。

    为什么要把这个函数提到模块级：它是“token 成本”与“信息完整性”的平衡点，
    必须在单测里能直接断言（不能只靠跑真库看输出）。
    """
    items = sorted(plan.get("items", []), key=lambda i: -int(i.get("priority") or 0))
    priorities = [int(i.get("priority") or 0) for i in items]
    brief: dict[str, Any] = {
        "innerCode": plan.get("innerCode"),
        "status": status_label(int(plan.get("status") or 0)),
        "totalQuantity": plan.get("totalQuantity"),
        "channelCount": len(items),
        "maxPriority": _priority_label(max(priorities) if priorities else None),
        "assigneeName": plan.get("assigneeName") or "待指派",
    }
    if not with_channels:
        # 明确告诉模型“明细可以再查”，而不是让它以为没有明细、从而编造货道细节
        brief["channelsOmitted"] = (
            "该计划未含紧急货道，明细已省略；需要看各货道请用 detail=true 再查"
        )
        return brief
    brief["channels"] = [
        {
            "channelCode": i.get("channelCode"),
            "skuName": i.get("skuName") or "（未命名商品）",
            "current": f"{i.get('currentQuantity')}/{i.get('maxCapacity')}",
            "suggestedQuantity": i.get("suggestedQuantity"),
            "priority": _priority_label(i.get("priority")),
            "estimatedDays": i.get("estimatedDays"),
            "reason": _clip(i.get("reason")),
        }
        for i in items[:MAX_CHANNELS_PER_PLAN]
    ]
    if len(items) > MAX_CHANNELS_PER_PLAN:
        brief["channelsTruncatedFrom"] = len(items)
    return brief


async def _list_restock_suggestions(plan_date: str | None = None, detail: bool = False) -> str:
    target = _parse_date(plan_date)
    try:
        store = SqlPlanStore()
        rows = await store.list_plans(target)
        pause = await SqlPauseStore().get()
    except Exception as exc:  # noqa: BLE001 —— 工具异常要喂回模型，不能抛穿对话
        logger.warning("助手工具 list_restock_suggestions 失败：%s", type(exc).__name__)
        return _json({"error": f"查询补货建议失败（{type(exc).__name__}），请稍后重试"})

    plans = [summarize_plan(plan_from_row(row), plan_id=int(row["id"])) for row in rows]
    pending = [p for p in plans if p["status"] in PENDING_PLAN_STATUSES]
    # 含紧急货道（priority=4）的计划：这些默认带明细，因为“先看紧急的”是最常见的问法
    urgent_ids = {
        p["id"]
        for p in pending
        if any(int(i.get("priority") or 0) >= 4 for i in p.get("items", []))
    }
    returned = pending[:MAX_PLANS]

    payload: dict[str, Any] = {
        "planDate": target.isoformat(),
        # 口径必须一起给出：否则模型会把“待处理”说成“全部建议”
        "stats": {
            "pendingCount": len(pending),
            "urgentCount": len(urgent_ids),
            "pendingTotalQuantity": sum(int(p.get("totalQuantity") or 0) for p in pending),
            "allPlansCount": len(plans),
            "caliber": "pendingCount/urgentCount 只统计未完成计划（建议/已调整/待指派）；"
            "urgent 指计划内存在 priority=4（紧急）的货道",
        },
        "autoAnalysisPaused": bool(pause.paused),
        # 默认只回未完成计划（token 优化）；已完成/已取消用计数告知，不装作“当天只有这些”
        "plans": [
            _plan_brief(p, with_channels=bool(detail) or p["id"] in urgent_ids) for p in returned
        ],
        "plansScope": "仅未完成计划（建议/已调整/待指派）",
        "completedOrOtherPlans": len(plans) - len(pending),
    }
    if len(pending) > MAX_PLANS:
        payload["plansTruncatedFrom"] = len(pending)
    if not plans:
        payload["hint"] = (
            "当日没有计划行。常见原因：① 自动分析被暂停（见 autoAnalysisPaused）；"
            "② 当日 06:00 定时任务未成功执行。不要据此说‘都不需要补货’。"
        )
    elif not pending:
        payload["hint"] = "当日计划都已处理完（无未完成计划）。不要据此说‘今天不用补货’。"
    return _json(payload)


# ----------------------------------------------------------------------------------
# 2) 设备库存与档案
# ----------------------------------------------------------------------------------


class _MachineArgs(BaseModel):
    inner_code: str = Field(description="售货机编码，例如 A1000001")


async def _get_machine_inventory(inner_code: str) -> str:
    code = (inner_code or "").strip()
    if not code:
        return _json({"error": "缺少设备编码（inner_code）"})
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            profile = await read_tools.get_machine_profile(session, code)
            if profile is None:
                return _json({"error": f"未找到设备 {code}（编码是否正确？）"})
            stock = await read_tools.list_channel_stock(session, inner_codes=[code])
    except ToolError as exc:
        logger.warning("助手工具 get_machine_inventory 被拒/超时：%s", exc)
        return _json({"error": f"查询被拒或超时：{exc}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 get_machine_inventory 失败：%s", type(exc).__name__)
        return _json({"error": f"查询设备库存失败（{type(exc).__name__}）"})

    channels = [
        {
            "channelCode": item.channel_code,
            # 本投影刻意**不带商品名**：SKU 需另外查档案，而这里两边 sku_id 实测不一致
            # （见 ChannelStockItem docstring），带一个可能错的商品名比不带更糟。
            "current": item.current_stock,
            "minStock": item.min_stock,
            "isAlert": item.is_alert,
            "maxCapacity": item.capacity,
            # 如实暴露两侧档案是否一致（FIX-5：本机实测 3/3 不一致），
            # 而不是静默选边——模型也要知道“这个数有两个来源”
            "dataConsistent": item.data_consistent,
        }
        for item in stock[:MAX_STOCK_CHANNELS]
    ]
    payload: dict[str, Any] = {
        "innerCode": profile.inner_code,
        "addr": profile.addr,
        "nodeName": profile.node_name,
        "regionName": profile.region_name,
        "vmStatus": profile.vm_status,
        "channelCount": len(stock),
        "channels": channels,
        "caliber": "current 为当前库存件数；maxCapacity 为货道容量（优先取货道档案）。"
        "缺口 = maxCapacity - current，仅供参考，是否补货由补货建议决定。"
        "dataConsistent=false 表示库存表与货道档案的 SKU/容量不一致（已知数据质量问题，"
        "需业务确认权威源），此时容量可能不准，要如实告知用户而不是选一个数报出去。",
    }
    if len(stock) > MAX_STOCK_CHANNELS:
        payload["channelsTruncatedFrom"] = len(stock)
    return _json(payload)


# ----------------------------------------------------------------------------------
# 3) 在途工单
# ----------------------------------------------------------------------------------


class _InflightArgs(BaseModel):
    inner_code: str = Field(description="售货机编码，例如 A1000001")


async def _list_inflight_work_orders(inner_code: str) -> str:
    code = (inner_code or "").strip()
    if not code:
        return _json({"error": "缺少设备编码（inner_code）"})
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            tasks = await read_tools.list_inflight_tasks(session, inner_codes=[code])
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 list_inflight_work_orders 失败：%s", type(exc).__name__)
        return _json({"error": f"查询在途工单失败（{type(exc).__name__}）"})

    payload: dict[str, Any] = {
        "innerCode": code,
        "inflightCount": len(tasks),
        "orders": [
            {
                "taskId": t.task_id,
                "taskCode": t.task_code,
                "taskStatus": t.task_status,
                "channels": list(t.channels or []),
                "createTime": t.create_time,
            }
            for t in tasks[:MAX_INFLIGHT]
        ],
        "caliber": "在途 = 工单状态 1-待接单 / 2-进行中。同一设备同类型存在在途工单时，"
        "新建工单会被 Java 侧防重校验拒绝——所以这个数是‘还能不能建单’的关键依据。",
    }
    if len(tasks) > MAX_INFLIGHT:
        payload["ordersTruncatedFrom"] = len(tasks)
    return _json(payload)


# ----------------------------------------------------------------------------------
# 4) 近期销量（口径与报表/工作台一致）
# ----------------------------------------------------------------------------------


class _SalesArgs(BaseModel):
    inner_code: str = Field(description="售货机编码，例如 A1000001")
    days: int = Field(default=7, description="统计天数（1-30），默认 7 天")


async def _get_sales_summary(inner_code: str, days: int = 7) -> str:
    code = (inner_code or "").strip()
    if not code:
        return _json({"error": "缺少设备编码（inner_code）"})
    try:
        window_days = max(1, min(int(days), 30))
    except (TypeError, ValueError):
        window_days = 7

    start, end = default_sales_window(days=window_days)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            rows = await read_tools.aggregate_channel_sales(
                session, inner_codes=[code], start=start, end=end
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 get_sales_summary 失败：%s", type(exc).__name__)
        return _json({"error": f"查询销量失败（{type(exc).__name__}）"})

    total_qty = sum(int(r.qty or 0) for r in rows)
    total_amount = sum(int(r.amount or 0) for r in rows)
    payload: dict[str, Any] = {
        "innerCode": code,
        "window": {"days": window_days, "start": start, "end": end},
        "totalOrders": sum(int(r.order_count or 0) for r in rows),
        "totalQuantity": total_qty,
        "totalAmount": total_amount,
        "dailyAverage": round(total_qty / window_days, 2),
        "byChannel": [
            {
                "channelCode": r.channel_code,
                "skuName": None,  # 本工具的投影不带商品名，避免与档案口径混用
                "quantity": int(r.qty or 0),
                "amount": int(r.amount or 0),
            }
            for r in rows[:MAX_STOCK_CHANNELS]
        ],
        "caliber": "只统计订单状态=2（出货成功）的订单；金额与订单数为该口径下的实测值。"
        "channel_code 为空的订单不可归因到货道，因此不计入 byChannel，但计入总数。",
    }
    return _json(payload)


# ----------------------------------------------------------------------------------
# 诊断场景的三个只读工具（2-1 / 2-2 / 2-3 的入口）
# ----------------------------------------------------------------------------------
#
# 为什么单独一组、而不是把全部工具都给模型：**场景决定能力面**。
# 补货场景不需要知道“怎么查故障案例”，诊断场景也不需要“补货建议清单”——
# 多挂一个工具就多一份 schema（每轮都进 prompt），而且会把模型注意力引到无关问题上。
# 模型在每个场景只能看到该场景的工具（见 `tools_for_scene`），**提示词也按场景列工具**
# （两边不一致就是“提示与能力脱节”——那个 bug 已经踩过两次）。

# 异常/历史查询的默认窗口（天）。为什么两者不同：
#   · 异常检测看“最近怎么了”——90 天足够且不会拉全表；
#   · 历史案例很稀疏（本机 5 条全在 2023-03），窗口太短会得出“没有历史”的假结论。
DEFAULT_ANOMALY_WINDOW_DAYS = 90
# 历史检索默认窗口 2000 天（≈5.5 年）：比 read_tools 的程序化默认（1095）大，
# 因为自然语言的“历史/以前”比程序调用含糊，窗口给小了会得到“没有历史”的假结论
# （本机维修单全在 2023-03）。真实成本护栏仍是 per-device 过滤 + 超时熔断。
# P1-14：常量收敛到 app/constants.py（两值刻意不同，理由见 docs/CONTRACTS/status-codes.md 末节）。
DEFAULT_CASE_WINDOW_DAYS = CASE_WINDOW_DAYS_ASSISTANT
# 各工具自己的窗口上限（下面是唯一的定义处，别再散落魔法数字）：
#   异常 3650（10 年）——异常数据天生稀疏，窗口太短会得出“设备没问题”的假阴性；
#   历史 2000（≈5.5 年）——与上面的默认值一致。
MAX_ANOMALY_WINDOW_DAYS = 3650
MAX_CASE_WINDOW_DAYS = 2000

MAX_ANOMALY_ITEMS = 5


def _clip_reason(text: Any, limit: int = 90) -> str | None:
    """异常理由可能很长（带日志 id/前后库存），截断后仍保留可复核的关键数字。"""
    if text is None:
        return None
    value = str(text).strip()
    return value if len(value) <= limit else value[:limit] + "…（已截断）"


class _FindDevicesArgs(BaseModel):
    query: str = Field(
        description="用户口述的设备线索：编号 / 地址 / 点位名，例如“五道口”或“A1000001”"
    )


async def _find_devices(query: str) -> str:
    """按用户口述定位设备（实体消歧，2-3）。**绝不允许模型自己编一个编号。**"""
    text_query = (query or "").strip()
    if not text_query:
        return _json({"error": "请给出设备线索（编号 / 地址 / 点位名）"})
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            matches = await read_tools.find_devices(session, query=text_query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 find_devices 失败：%s", type(exc).__name__)
        return _json({"error": f"查询设备失败（{type(exc).__name__}）"})

    payload: dict[str, Any] = {
        "query": text_query,
        "matchCount": len(matches),
        "devices": [
            {
                "innerCode": m.inner_code,
                "addr": m.addr,
                "nodeName": m.node_name,
                "regionName": m.region_name,
                "vmTypeName": m.vm_type_name,
                "vmStatus": m.vm_status,
                "matchReason": m.match_reason,
            }
            for m in matches[:10]
        ],
        "caliber": "按编号精确 → 编号前缀 → 点位名 → 地址 的顺序**真查**设备档案（不是猜编号）。",
    }
    if not matches:
        payload["hint"] = (
            "没有匹配到设备。**不要自己编一个设备编号**；请让用户换一种说法"
            "（例如报编号前缀，或说出市区/点位名称）。"
        )
    elif len(matches) > 1:
        payload["hint"] = (
            f"有 {len(matches)} 台设备匹配，**必须让用户确认是哪一台**（报 innerCode）；"
            "未确认前不要继续查异常/历史，否则会把诊断做在错的设备上。"
        )
    return _json(payload)


class _AnomalyArgs(BaseModel):
    inner_code: str = Field(description="售货机编码（如 A1000001）；未确认时先用 find_devices 定位")
    days: int = Field(
        default=DEFAULT_ANOMALY_WINDOW_DAYS,
        description="回看天数（1-3650），默认 90；用户问“近几年/历史上有没有异常”时给大一些",
    )


async def _list_device_anomalies(inner_code: str, days: int = DEFAULT_ANOMALY_WINDOW_DAYS) -> str:
    """设备的订单/库存异常（2-1）——“这台机器到底出了什么问题”。"""
    code = (inner_code or "").strip()
    if not code:
        return _json({"error": "缺少设备编码（inner_code）"})
    requested_days = days if isinstance(days, int) else None
    try:
        # 上限 10 年：**不是**为了让模型随便拉大窗口，而是因为维修/异常数据天生稀疏——
        # 本机全部订单都在 2023 年，365 天的上限会让“查历史异常”永远返回空，
        # 用户会据此以为设备没问题（假阴性）。真正的成本护栏是 per-device 过滤 +
        # 时间范围下推到索引 + `read_query_timeout_s` 熔断（AGENTS §10），不是窗口长度。
        window_days = max(1, min(int(days), MAX_ANOMALY_WINDOW_DAYS))
    except (TypeError, ValueError):
        window_days = DEFAULT_ANOMALY_WINDOW_DAYS
    # FIX-28：**用户原话里的窗口优先**于模型请求的窗口（实测模型跟不住“近五年”）。
    # 两个值都写进返回值（`windowSource` / `windowOverriddenFromDays`），不留“谁改了什么”的暗账。
    window = time_hint.resolve_window_days(window_days, maximum=MAX_ANOMALY_WINDOW_DAYS)
    window_days = window.days

    start, end = read_tools.default_sales_window(days=window_days)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            orders = await read_tools.list_order_anomalies(
                session, inner_codes=[code], start=start, end=end
            )
            inventory = await read_tools.list_inventory_anomalies(
                session, inner_codes=[code], start=start, end=end
            )
    except read_tools.ToolError as exc:
        logger.warning("助手工具 list_device_anomalies 被拒/超时：%s", exc)
        return _json({"error": f"异常检测被拒或超时：{exc}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 list_device_anomalies 失败：%s", type(exc).__name__)
        return _json({"error": f"异常检测失败（{type(exc).__name__}）"})

    by_kind: dict[str, int] = {}
    for item in [*orders, *inventory]:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1

    payload: dict[str, Any] = {
        "innerCode": code,
        "window": {"days": window_days, "start": start, "end": end},
        "orderAnomalyCount": len(orders),
        "inventoryAnomalyCount": len(inventory),
        "byKind": by_kind,
        "orderAnomalies": [
            {
                "kind": a.kind,
                "orderNo": a.order_no,
                "channelCode": a.channel_code,
                "status": a.status,
                "payStatus": a.pay_status,
                "ageMinutes": a.age_minutes,
                "reason": _clip_reason(a.reason),
            }
            for a in orders[:MAX_ANOMALY_ITEMS]
        ],
        "inventoryAnomalies": [
            {
                "kind": a.kind,
                "changeType": a.change_type,
                "changeAmount": a.change_amount,
                "orderId": a.order_id,
                "reason": _clip_reason(a.reason),
            }
            for a in inventory[:MAX_ANOMALY_ITEMS]
        ],
        "caliber": "订单异常 = status=1 超过阀值仍未出货（卡单）或 status=3 且未退款（出货失败）；"
        "库存异常 = 无单扣减 / 记录自相矛盾 / 无说明扣减（均来自 tb_inventory_log）。",
        "dataWarning": "本机 tb_inventory_log 为空表（0 行）：库存异常恒为 0 **不代表货道没问题**，"
        "只是该类日志尚未被采集。",
    }
    payload.update(window.as_payload_fields())
    if (
        window.source == time_hint.SOURCE_MODEL
        and requested_days is not None
        and requested_days != window_days
    ):
        # 静默夹取会让模型把“实际只查了 N 天”说成“查了 M 天”（口径失真）
        payload["windowClampedFromDays"] = requested_days
    if len(orders) > MAX_ANOMALY_ITEMS:
        payload["orderAnomaliesTruncatedFrom"] = len(orders)
    if len(inventory) > MAX_ANOMALY_ITEMS:
        payload["inventoryAnomaliesTruncatedFrom"] = len(inventory)
    if not orders and not inventory:
        payload["hint"] = (
            "窗口内没有检测到异常。这不等于设备没问题（本平台的异常检测只看订单状态与库存日志）；"
            "请结合用户描述的现象判断，并**明确告知用户“本次检测未发现问题”**。"
        )
    return _json(payload)


class _HistoryArgs(BaseModel):
    inner_code: str = Field(description="售货机编码；未确认时先用 find_devices 定位")
    days: int = Field(default=DEFAULT_CASE_WINDOW_DAYS, description="回看天数（1-2000），默认 2000")


async def _search_device_history(inner_code: str, days: int = DEFAULT_CASE_WINDOW_DAYS) -> str:
    """历史维修单检索（2-2）——“同类故障以前怎么处理的”。

    ⚠️ 本工具的重点不是“能检索到”，而是**如实说出检索结果能不能当证据**：
    本平台维修工单普遍没有结构化的现象/原因/处置（FIX-22），所以 `usableCaseCount` 常为 0，
    此时**必须**走保守话术。把 `111` 这种占位文本当“同类现象”讲出来就是编造证据。
    """
    code = (inner_code or "").strip()
    if not code:
        return _json({"error": "缺少设备编码（inner_code）"})
    requested_days = days if isinstance(days, int) else None
    try:
        window_days = max(1, min(int(days), MAX_CASE_WINDOW_DAYS))
    except (TypeError, ValueError):
        window_days = DEFAULT_CASE_WINDOW_DAYS
    # FIX-28：同 `list_device_anomalies`，用户说的窗口优先
    window = time_hint.resolve_window_days(window_days, maximum=MAX_CASE_WINDOW_DAYS)
    window_days = window.days
    start, end = read_tools.default_case_window(days=window_days)

    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            result = await read_tools.search_history_cases(
                session, inner_code=code, start=start, end=end, with_anomalies=False
            )
    except read_tools.ToolError as exc:
        logger.warning("助手工具 search_device_history 被拒/超时：%s", exc)
        return _json({"error": f"历史检索被拒或超时：{exc}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 search_device_history 失败：%s", type(exc).__name__)
        return _json({"error": f"历史检索失败（{type(exc).__name__}）"})

    payload: dict[str, Any] = {
        "innerCode": code,
        "window": {"days": window_days, "start": start, "end": end},
        "totalCases": result.total_cases,
        "usableCaseCount": result.usable_case_count,
        "matchedByCounts": result.matched_by_counts,
        "cases": [
            {
                "taskCode": c.task_code,
                "device": c.inner_code,
                "vmTypeName": c.vm_type_name,
                "matchedBy": c.matched_by,
                "confidence": c.confidence,
                "confidenceBasis": c.confidence_basis,
                "phenomenon": c.phenomenon,
                "phenomenonUsable": c.phenomenon_usable,
                "taskStatus": c.task_status_label,
                # 为什么必须给时间（2026-10-02 实测补上，F-6）：
                # ① 运营问的是“以前修过吗”——不带时间就只能回答“修过”，答不了“什么时候”；
                # ② 少了它，模型只能从 `taskCode` 前缀（`202303240005`）推年份，写成“2023 年 3 月”，
                #    而载荷里并没有这个数 → `grounding.check_reply_numbers` 当场判成
                #    “没有出处的数字”并弹提示条（实测踩到）。该模块的底线是
                #    「**宁可漏报，不可误报**」——误报会让提示条变成噪声、最终没人看。
                #    根因在载荷缺字段，不是模型编造。
                # 命名与 `list_inflight_work_orders` 的 `createTime` 保持一致（同为原始 datetime，
                # 由 `_json` 的 `default=str` 序列化）。
                "createTime": c.created_time,
            }
            for c in result.cases[:MAX_ANOMALY_ITEMS]
        ],
        "caliber": result.caliber,
    }
    payload.update(window.as_payload_fields())
    if (
        window.source == time_hint.SOURCE_MODEL
        and requested_days is not None
        and requested_days != window_days
    ):
        payload["windowClampedFromDays"] = requested_days
    if len(result.cases) > MAX_ANOMALY_ITEMS:
        payload["casesTruncatedFrom"] = result.total_cases
    if result.no_evidence_hint:
        # 这条提示必须原样进 prompt：它是“不编造历史案例”的唯一护栏
        payload["noEvidenceHint"] = result.no_evidence_hint
        payload["mustSay"] = (
            "回答时**必须**说明“没有可用的历史案例/现象”这一点，不要让用户以为有历史依据；"
            "`phenomenonUsable=false` 的描述只能当“有过维修记录”用，不得当作故障现象引用。"
        )
    return _json(payload)


class _DiagnosisArgs(BaseModel):
    inner_code: str = Field(description="售货机编码；未确认时先用 find_devices 定位")
    days: int = Field(
        default=DEFAULT_ANOMALY_WINDOW_DAYS, description="异常回看天数（1-3650），默认 90"
    )
    case_days: int = Field(
        default=DEFAULT_CASE_WINDOW_DAYS, description="历史维修单回看天数（1-2000），默认 2000"
    )


async def _build_device_diagnosis(
    inner_code: str,
    days: int = DEFAULT_ANOMALY_WINDOW_DAYS,
    case_days: int = DEFAULT_CASE_WINDOW_DAYS,
) -> str:
    """结构化诊断结论（2-4）：**证据 + 未知项 + 平台动作**，不含病因推断。

    为什么要有这个工具（而不是让模型自由发挥）：自由发挥的“病因 + 置信度”无法复核、
    无法回归，而且本平台没有结构化的故障知识库（FIX-22），任何病因都是编的。
    这个工具把**能说清的**（证据/未知/动作）结构化，`confidence` 由证据强度按固定规则算出，
    模型只需如实转述——它因此没有“顺手补一个病因”的空间。
    """
    code = (inner_code or "").strip()
    if not code:
        return _json({"error": "缺少设备编码（inner_code）"})
    try:
        window_days = max(1, min(int(days), MAX_ANOMALY_WINDOW_DAYS))
        case_window_days = max(1, min(int(case_days), MAX_CASE_WINDOW_DAYS))
    except (TypeError, ValueError):
        window_days = DEFAULT_ANOMALY_WINDOW_DAYS
        case_window_days = DEFAULT_CASE_WINDOW_DAYS
    # FIX-28：两个窗口都跟着用户原话走（“近五年”时异常与历史都按 1825 天算）
    anomaly_window = time_hint.resolve_window_days(window_days, maximum=MAX_ANOMALY_WINDOW_DAYS)
    case_window = time_hint.resolve_window_days(case_window_days, maximum=MAX_CASE_WINDOW_DAYS)
    window_days = anomaly_window.days
    case_window_days = case_window.days

    start, end = read_tools.default_sales_window(days=window_days)
    case_start, case_end = read_tools.default_case_window(days=case_window_days)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            profile = await read_tools.get_machine_profile(session, code)
            if profile is None:
                return _json({"error": f"未找到设备 {code}（编码是否正确？）"})
            orders = await read_tools.list_order_anomalies(
                session, inner_codes=[code], start=start, end=end
            )
            inventory = await read_tools.list_inventory_anomalies(
                session, inner_codes=[code], start=start, end=end
            )
            history = await read_tools.search_history_cases(
                session, inner_code=code, start=case_start, end=case_end, with_anomalies=False
            )
            inflight = await read_tools.list_inflight_tasks(session, inner_codes=[code])
            stock = await read_tools.list_channel_stock(session, inner_codes=[code])
    except read_tools.ToolError as exc:
        logger.warning("助手工具 build_device_diagnosis 被拒/超时：%s", exc)
        return _json({"error": f"诊断取数被拒或超时：{exc}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("助手工具 build_device_diagnosis 失败：%s", type(exc).__name__)
        return _json({"error": f"诊断取数失败（{type(exc).__name__}）"})

    conclusion = compose_diagnosis(
        profile=profile,
        order_anomalies=orders,
        inventory_anomalies=inventory,
        history=history,
        inflight=inflight,
        stock=stock,
        window={
            "anomalyDays": window_days,
            "anomalyStart": start.isoformat(),
            "anomalyEnd": end.isoformat(),
            "caseDays": case_window_days,
        },
    )
    payload: dict[str, Any] = conclusion.model_dump(mode="json", by_alias=True)
    payload.update(anomaly_window.as_payload_fields())
    payload.update(case_window.as_payload_fields(prefix="case"))
    payload["mustSay"] = (
        "**本结论不包含病因推断**（平台没有结构化的故障知识库，FIX-22）："
        "只能转述 evidence / unknowns / recommendedActions 与 confidenceBasis，"
        "**不得自行补一个“可能原因”或“处置步骤”**；"
        "`unknowns` 必须一并告知用户（否则“没检出异常”会被理解成“设备没问题”）。"
    )
    return _json(payload)


def build_diagnosis_tools() -> list[StructuredTool]:
    """诊断场景（scene=3）的只读工具集（2-1/2-2/2-3）。

    ⚠️ 同样**只放只读工具**：建维修单仍然要在页面上人工确认（AGENTS §7.9）。
    助手能做的最后一公里是“把现象、异常、历史记录说清楚，并告诉你去哪里动手”。
    """
    return [
        StructuredTool.from_function(
            coroutine=_find_devices,
            name="find_devices",
            description=(
                "按用户口述定位设备：设备编号 / 地址 / 点位名（如“五道口”“A1000001”）。"
                "用户没给编号时必须先用它，**不要自己编设备编号**；返回多台时要让用户确认是哪台。"
            ),
            args_schema=_FindDevicesArgs,
        ),
        StructuredTool.from_function(
            coroutine=_list_device_anomalies,
            name="list_device_anomalies",
            description=(
                "检测某台设备近 N 天（默认 90）的异常：支付成功未出货（卡单）、出货失败未退款、"
                "库存无单扣减/账实不符。问“这台机器出了什么问题/为什么不出货/有没有异常”时用它。"
            ),
            args_schema=_AnomalyArgs,
        ),
        StructuredTool.from_function(
            coroutine=_build_device_diagnosis,
            name="build_device_diagnosis",
            description=(
                "生成某台设备的结构化诊断结论：证据清单（订单/库存异常、在途工单、历史维修单）、"
                "**未知项**（平台观测不到的部分）、可执行的平台动作、"
                "以及由证据强度算出的 confidence。"
                "需要“这台机器到底怎么回事/给我一个结论”时用它——它**不含病因推断**（平台无故障知识库），"
                "不要在此基础上自行编造原因或处置步骤。"
            ),
            args_schema=_DiagnosisArgs,
        ),
        StructuredTool.from_function(
            coroutine=_search_device_history,
            name="search_device_history",
            description=(
                "检索某台设备的历史维修工单（含同型号/同区域降级），返回工单号与描述原文，"
                "并明确标注描述**能不能当故障现象用**。问“以前有没有遇到过/历史怎么处理的”时用它。"
            ),
            args_schema=_HistoryArgs,
        ),
    ]


# ----------------------------------------------------------------------------------
# 工具注册
# ----------------------------------------------------------------------------------


def build_assistant_tools() -> list[StructuredTool]:
    """构造**补货场景**（scene=2）的只读工具集。

    ⚠️ **只放只读工具**。加任何写工具（建单/确认/调整）之前先回答两个问题：
    ① 写操作的 human-in-the-loop 放在哪里（AGENTS §7.9 要求首期必须有人工确认）；
    ② 谁承担“模型理解错了”的后果。当前答案是：助手不写，人去工作台点。
    """
    return [
        StructuredTool.from_function(
            coroutine=_list_restock_suggestions,
            name="list_restock_suggestions",
            description=(
                "查询某日（默认今天）的补货建议：待处理与紧急条数、各设备建议与理由、接单人、"
                "自动分析是否被暂停。默认只返回未完成计划，且只给含紧急货道的计划带明细"
                "（需某台设备各样货道时传 detail=true 再查）。"
                "问“有哪些要补/先看紧急/今天多少条”用它。它只读，不能建单或修改建议。"
            ),
            args_schema=_PlansArgs,
        ),
        StructuredTool.from_function(
            coroutine=_get_machine_inventory,
            name="get_machine_inventory",
            description=(
                "按设备编码查询设备档案（点位/区域/运行状态）与各货道当前库存、货道容量。"
                "当用户问某台机器“库存怎么样/还剩多少/哪个货道快空了”时用它。"
            ),
            args_schema=_MachineArgs,
        ),
        StructuredTool.from_function(
            coroutine=_list_inflight_work_orders,
            name="list_inflight_work_orders",
            description=(
                "查询某台设备的在途工单（状态 1-待接单 / 2-进行中）。"
                "当用户问“这台是不是已经有单了/为什么建不了单”时用它。"
            ),
            args_schema=_InflightArgs,
        ),
        StructuredTool.from_function(
            coroutine=_get_sales_summary,
            name="get_sales_summary",
            description=(
                "查询某台设备近 N 天（默认 7 天，上限 30 天）的出货成功销量与金额，按货道汇总。"
                "当用户问“这台最近卖得怎么样/为什么建议补这么多”时用它。"
            ),
            args_schema=_SalesArgs,
        ),
    ]


# ----------------------------------------------------------------------------------
# 场景 → 工具集（唯一的装配入口）
# ----------------------------------------------------------------------------------
#
# 名单本身在 `app/prompts/assistant_chat.py`（哪里放“场景能力”的定义，就要能同时约束
# 提示词与绑定，否则两边一定会漂移）。这里只负责把名单换成真工具对象。
#
# 为什么所有工具都注册进 ToolNode、但只 bind 该场景的子集：
#   · 图只有一个 `tools` 节点，它必须认识所有工具；
#   · 模型只能调用"bind 给它的"工具（schema 也才不被多余工具占掉 token）。
# 代价（已知且接受）：理论上模型可以"猜"一个本场景未绑定的工具名而仍被执行——
# 但这些工具全部只读且鉴权在同一层，不构成越权；真正需要严格能力面的写工具
# 不应该走这条路径（且当前根本没有写工具）。


def tools_for_scene(scene: int) -> list[StructuredTool]:
    """该场景应绑定的工具列表（顺序与名单一致）。

    名单里出现了但没实现的名字 → 直接报错（而不是静默少一个工具）：
    静默少工具会让提示词声称能力、实际却没有——那正是我们踩过两次的 bug。
    """
    registry = {tool.name: tool for tool in [*build_assistant_tools(), *build_diagnosis_tools()]}
    names = tool_names_for_scene(scene)
    missing = [name for name in names if name not in registry]
    if missing:
        raise RuntimeError(
            f"场景 {scene} 声明的工具未实现：{missing}；"
            "请先在 app/tools/assistant_tools.py 实现，或从 prompts 的 TOOL_NAMES_BY_SCENE 里删掉"
        )
    return [registry[name] for name in names]


def all_assistant_tools() -> list[StructuredTool]:
    """全部已实现的助手工具（供启动自检与 ToolNode 注册）。"""
    return [*build_assistant_tools(), *build_diagnosis_tools()]


__all__ = [
    "PRIORITY_LABELS",
    "all_assistant_tools",
    "build_assistant_tools",
    "build_diagnosis_tools",
    "tools_for_scene",
]
