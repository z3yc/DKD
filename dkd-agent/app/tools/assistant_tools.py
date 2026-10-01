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

from app.db import get_sessionmaker
from app.graphs.restock_decisions import summarize_plan
from app.graphs.restock_plan_store import SqlPauseStore, SqlPlanStore, plan_from_row
from app.graphs.restock_state import status_label
from app.tools import read_tools
from app.tools.read_tools import ToolError, default_sales_window

logger = logging.getLogger("dkd.agent.tools.assistant")

# 优先级中文名：必须与前端（views/manage/restock/index.vue 的 priorityLabel）
# 和 DDL 注释（agent_restock_plan.items 的 priority 1-4）保持一致
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "紧急"}

# 单次返回的规模上限（token 成本与“误导性截断”的平衡点）
MAX_PLANS = 20
MAX_CHANNELS_PER_PLAN = 5
MAX_STOCK_CHANNELS = 20
MAX_INFLIGHT = 20


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


async def _list_restock_suggestions(plan_date: str | None = None) -> str:
    target = _parse_date(plan_date)
    try:
        store = SqlPlanStore()
        rows = await store.list_plans(target)
        pause = await SqlPauseStore().get()
    except Exception as exc:  # noqa: BLE001 —— 工具异常要喂回模型，不能抛穿对话
        logger.warning("助手工具 list_restock_suggestions 失败：%s", type(exc).__name__)
        return _json({"error": f"查询补货建议失败（{type(exc).__name__}），请稍后重试"})

    plans = [summarize_plan(plan_from_row(row), plan_id=int(row["id"])) for row in rows]
    pending_status = (1, 2, 6)  # 1-建议 2-已调整 6-待指派
    pending = [p for p in plans if p["status"] in pending_status]
    urgent = [
        p for p in pending if any(int(i.get("priority") or 0) >= 4 for i in p.get("items", []))
    ]

    def _plan_brief(plan: dict[str, Any]) -> dict[str, Any]:
        items = sorted(plan.get("items", []), key=lambda i: -int(i.get("priority") or 0))[
            :MAX_CHANNELS_PER_PLAN
        ]
        brief: dict[str, Any] = {
            "innerCode": plan.get("innerCode"),
            "status": status_label(int(plan.get("status") or 0)),
            "totalQuantity": plan.get("totalQuantity"),
            "assigneeName": plan.get("assigneeName") or "待指派",
            "channels": [
                {
                    "channelCode": i.get("channelCode"),
                    "skuName": i.get("skuName") or "（未命名商品）",
                    "current": f"{i.get('currentQuantity')}/{i.get('maxCapacity')}",
                    "suggestedQuantity": i.get("suggestedQuantity"),
                    "priority": _priority_label(i.get("priority")),
                    "estimatedDays": i.get("estimatedDays"),
                    "reason": i.get("reason"),
                }
                for i in items
            ],
        }
        if len(plan.get("items", [])) > MAX_CHANNELS_PER_PLAN:
            brief["channelsTruncatedFrom"] = len(plan.get("items", []))
        return brief

    payload: dict[str, Any] = {
        "planDate": target.isoformat(),
        # 口径必须一起给出：否则模型会把“待处理”说成“全部建议”
        "stats": {
            "pendingCount": len(pending),
            "urgentCount": len(urgent),
            "pendingTotalQuantity": sum(int(p.get("totalQuantity") or 0) for p in pending),
            "allPlansCount": len(plans),
            "caliber": "pendingCount/urgentCount 只统计未完成计划（建议/已调整/待指派）；"
            "urgent 指计划内存在 priority=4（紧急）的货道",
        },
        "autoAnalysisPaused": bool(pause.paused),
        "plans": [_plan_brief(p) for p in plans[:MAX_PLANS]],
    }
    if len(plans) > MAX_PLANS:
        payload["plansTruncatedFrom"] = len(plans)
    if not plans:
        payload["hint"] = (
            "当日没有计划行。常见原因：① 自动分析被暂停（见 autoAnalysisPaused）；"
            "② 当日 06:00 定时任务未成功执行。不要据此说‘都不需要补货’。"
        )
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
# 工具注册
# ----------------------------------------------------------------------------------


def build_assistant_tools() -> list[StructuredTool]:
    """构造助手可用的工具列表。

    ⚠️ **只放只读工具**。加任何写工具（建单/确认/调整）之前先回答两个问题：
    ① 写操作的 human-in-the-loop 放在哪里（AGENTS §7.9 要求首期必须有人工确认）；
    ② 谁承担“模型理解错了”的后果。当前答案是：助手不写，人去工作台点。
    """
    return [
        StructuredTool.from_function(
            coroutine=_list_restock_suggestions,
            name="list_restock_suggestions",
            description=(
                "查询某日（默认今天）的补货建议清单：待处理数量、其中紧急多少条、"
                "每台设备的货道建议与补货理由、接单人、自动分析是否被暂停。"
                "当用户问“有哪些要补的/先看紧急的/今天多少条”时用它。"
                "注意：它只读，不能建单或修改建议。"
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


__all__ = ["PRIORITY_LABELS", "build_assistant_tools"]
