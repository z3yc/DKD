"""补货工作台的业务服务层（Phase 1 / 任务 1-7）。

职责：把「HTTP 契约」与「状态机/存储/基线重算」隔开，让两条都能被单独测试：

    api/restock.py（鉴权、参数校验、HTTP 状态码）
        └─ services/restock_service.py（本模块：读计划、应用决策、重算、开关）
              ├─ graphs/restock_decisions.py（状态机，纯函数）
              ├─ graphs/restock_plan_store.py（agent_restock_plan / agent_restock_pause）
              └─ graphs/restock_baseline.py（按人工指定的窗口/服务水平重算）

## 三个业务规则（原型 V2 交互背面真正要做对的事）

1. **调整可以先重算再落库**：运营在「高级」里改预测窗口/服务水平，我们要按新参数**重算**建议量，
   而不是让他在浏览器里口算。重算用的数据与 06:00 分析完全同源（同样的只读工具、同样的窗口锚点），
   否则“同一台设备，两个入口算出两个数”会立刻失去信任。
2. **恢复只对“今天跳过”的计划开放**：`agent_restock_plan` 按 `(vm_id, plan_date)` 唯一，
   昨天的跳过记录属于历史复盘口径，不能事后改（会把 3-6 的复盘数字改掉）。跨日恢复应当由
   次日的分析生成新计划。
3. **暂停/恢复必须留痕且原因必填**：这是“让系统闭嘴”的操作，没有原因的话复盘时没人知道
   那天为什么没有建议（AGENTS §6.3 每次写操作决策必须留痕）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.audit import RESULT_FAIL, RESULT_OK, RESULT_REJECTED, TRIGGER_MANUAL, record_decision
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.graphs.restock_baseline import compute_device_baseline
from app.graphs.restock_decisions import (
    ACTION_ADJUST,
    ACTION_CONFIRM,
    ACTION_RESTORE,
    ACTION_SKIP,
    RESULT_ALREADY_ORDERED,
    RESULT_PENDING_ORDER,
    DecisionRejectedError,
    ItemOverride,
    PlanDecision,
    apply_decision,
    summarize_plan,
)
from app.graphs.restock_plan_store import (
    PAUSE_SCOPE_GLOBAL,
    OrderClaimConflictError,
    PauseState,
    SqlPauseStore,
    plan_from_row,
)
from app.graphs.restock_state import PLAN_STATUS_SUGGESTED, SCENE_RESTOCK, RestockPlan
from app.logging_conf import current_request_id
from app.tools.read_tools import default_sales_window
from app.tools.task_tools import CallbackError

logger = logging.getLogger("dkd.agent.services.restock")

# 留痕写入器签名（依赖注入点：单测传假写入器即可，不必连 MySQL）
AuditWriter = Callable[..., Awaitable[None]]

# 无网关注入 request_id 时的哨兵值（`logging_conf` 的 contextvar 缺省值）
_NO_REQUEST_ID = "-"


async def _default_audit_writer(**kwargs: Any) -> None:
    """默认留痕写入器：自建会话写 `agent_decision_log`（1-7b）。

    与 `app/api/chat.py::_record_turn` 同一套口径（scene/trigger_type/脱敏），
    差别只在于本模块是**人工干预**入口，没有人机对话的 LLM 输出。
    """
    async with get_sessionmaker()() as session:
        await record_decision(session, **kwargs)


# 人工可指定的预测窗口上限/下限（与配置的窗口集合取交集后再校验，见 _validate_overrides）
MAX_REASON = 500


class RestockServiceError(RuntimeError):
    """业务错误（HTTP 层据此映射 400/404/409）。消息是给运营看的中文人话。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdjustmentRequest(BaseModel):
    """调整入参（原型 V2「调整补货建议」弹窗的字段）。

    `quantity` 与「预测参数」二选一或同时给：
      - 只给 quantity → 直接按人工给定的数量（可能是“补满货道”或“减半”）；
      - 给 windowDays/serviceLevel → **按新参数重算**；两者同时给**拒绝**（分不清人工意图）。
    """

    reason: str = Field(max_length=MAX_REASON)
    quantity: int | None = Field(default=None, ge=0)
    channel_code: str | None = Field(default=None, max_length=32)
    window_days: int | None = Field(default=None, alias="windowDays")
    service_level: float | None = Field(default=None, alias="serviceLevel")
    coverage_days: float | None = Field(default=None, alias="coverageDays")

    model_config = {"populate_by_name": True}


class RestockService:
    """工作台服务（依赖注入 store / deps，便于测试替换）。"""

    def __init__(
        self,
        *,
        store: Any,
        deps: Any,
        settings: Settings | None = None,
        pause_store: Any | None = None,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._deps = deps
        self._settings = settings or get_settings()
        # 开关存储单独注入：测试用内存实现时不该被拖去连 MySQL
        self._pause = pause_store or SqlPauseStore(getattr(store, "_session_factory", None))
        # 留痕写入器同理：单测注入假写入器，生产走默认的 MySQL 实现
        self._audit_writer: AuditWriter = audit_writer or _default_audit_writer

    # ---------------- 读 ----------------

    async def list_plans(self, plan_date: date) -> dict[str, Any]:
        """工作台清单（含原型顶部的统计卡数据：待处理数 / 紧急数）。"""
        rows = await self._store.list_plans(plan_date)
        plans = []
        for row in rows:
            plan = plan_from_row(row)
            summary = summarize_plan(plan, plan_id=int(row["id"]))
            summary["taskId"] = row.get("task_id")
            summary["taskCode"] = row.get("task_code")
            summary["adjustReason"] = row.get("adjust_reason")
            summary["adjustedBy"] = row.get("adjusted_by")
            adjusted_time = row.get("adjusted_time")
            summary["adjustedTime"] = adjusted_time.isoformat() if adjusted_time else None
            plans.append(summary)
        pending = [p for p in plans if p["status"] in (PLAN_STATUS_SUGGESTED, 2, 6)]
        return {
            "planDate": plan_date.isoformat(),
            "stats": {
                "pending": len(pending),
                "urgent": sum(1 for p in pending for item in p["items"] if item["priority"] >= 4),
                "totalQuantity": sum(p["totalQuantity"] for p in pending),
            },
            "plans": plans,
        }

    async def get_plan_row(self, plan_id: int, plan_date: date) -> dict[str, Any]:
        rows = await self._store.list_plans(plan_date)
        for row in rows:
            if int(row["id"]) == plan_id:
                return row
        raise RestockServiceError(f"计划 {plan_id} 不存在（或不属于 {plan_date}）", status_code=404)

    # ---------------- 人工干预 ----------------

    async def confirm(self, plan_id: int, *, plan_date: date, user_id: int) -> dict[str, Any]:
        """确认建单：状态机放行 → 回调 Java（1-3）→ 回写 4-已建单 / 6-待指派。"""
        row, plan = await self._load(plan_id, plan_date)
        decision = PlanDecision(vmId=plan.vm_id, action=ACTION_CONFIRM, userId=user_id)
        return await self._execute(
            plan,
            decision,
            plan_id=plan_id,
            task_id=row.get("task_id"),
            # 派单结果（1-8）要能回答“这张工单派给了谁”，否则事后无法解释错派
            audit_extra={"assigneeId": plan.assignee_id, "assigneeName": plan.assignee_name},
        )

    async def adjust(
        self,
        plan_id: int,
        *,
        plan_date: date,
        user_id: int,
        payload: AdjustmentRequest,
    ) -> dict[str, Any]:
        """调整：可只改数量，也可按人工指定的预测窗口/服务水平**重算**后再改。"""
        _row, plan = await self._load(plan_id, plan_date)
        reason = (payload.reason or "").strip()
        if not reason:
            raise RestockServiceError("调整必须填写原因（用于复盘归因，不能留空）")

        overrides = (payload.window_days, payload.service_level, payload.coverage_days)
        if any(value is not None for value in overrides):
            if payload.quantity is not None:
                raise RestockServiceError(
                    "「按新参数重算」与「指定数量」只能二选一：两者同时给会分不清哪个是人工意图"
                )
            item = await self._recompute(plan, payload=payload, plan_date=plan_date)
            quantity, channel_code = item["suggestedQuantity"], item["channelCode"]
            # 把重算依据一并交给状态机：新参数算出来的理由要覆盖旧基线依据（否则依据自相矛盾）
            note = item["reason"]
        else:
            if payload.quantity is None:
                raise RestockServiceError("调整必须给出补货数量，或指定预测窗口/服务水平以重算")
            quantity = payload.quantity
            channel_code = payload.channel_code or plan.items[0].channel_code
            note = None

        decision = PlanDecision(
            vmId=plan.vm_id,
            action=ACTION_ADJUST,
            reason=reason,
            userId=user_id,
            items=[ItemOverride(channelCode=channel_code, suggestedQuantity=quantity, note=note)],
        )
        return await self._execute(
            plan,
            decision,
            plan_id=plan_id,
            # 人工指定的预测参数必须进留痕：没有它就无法复盘“这次为什么算出来是这个数”
            audit_extra={
                "channelCode": channel_code,
                "requestedQuantity": payload.quantity,
                "recomputedQuantity": quantity if payload.quantity is None else None,
                "windowDays": payload.window_days,
                "serviceLevel": payload.service_level,
                "coverageDays": payload.coverage_days,
            },
        )

    async def skip(
        self, plan_id: int, *, plan_date: date, user_id: int, reason: str
    ) -> dict[str, Any]:
        """跳过（原因必填）：置 3-已跳过，终态。"""
        if not (reason or "").strip():
            raise RestockServiceError("跳过必须填写原因（原型 V2 的必选项）")
        _row, plan = await self._load(plan_id, plan_date)
        decision = PlanDecision(vmId=plan.vm_id, action=ACTION_SKIP, reason=reason, userId=user_id)
        return await self._execute(plan, decision, plan_id=plan_id)

    async def restore(
        self, plan_id: int, *, plan_date: date, user_id: int, reason: str, today: date
    ) -> dict[str, Any]:
        """恢复建议（原型 V2 的「恢复建议」）：3-已跳过 → 1-建议，**仅限当天**。

        为什么必须限定当天：`agent_restock_plan` 按 `(vm_id, plan_date)` 唯一，
        改写历史日期的计划会污染 3-6 的复盘口径（“说好跳过，事后又被改回建议”）。
        跨日恢复应当由次日分析生成新计划，而不是改旧行。
        """
        if not (reason or "").strip():
            raise RestockServiceError("恢复必须填写原因")
        if plan_date != today:
            raise RestockServiceError(
                f"只能恢复当天（{today.isoformat()}）的计划；"
                f"{plan_date.isoformat()} 属历史记录，不可改写（会污染复盘口径）"
            )
        _row, plan = await self._load(plan_id, plan_date)
        decision = PlanDecision(
            vmId=plan.vm_id, action=ACTION_RESTORE, reason=reason, userId=user_id
        )
        return await self._execute(plan, decision, plan_id=plan_id)

    # ---------------- 暂停 / 恢复自动分析 ----------------

    async def pause_state(self) -> PauseState:
        return await self._pause.get()

    async def set_pause(self, *, paused: bool, user_id: int, reason: str) -> PauseState:
        """暂停/恢复自动分析（原因必填：这是“让系统闭嘴”的操作，必须能事后解释）。"""
        if not (reason or "").strip():
            raise RestockServiceError("暂停/恢复必须填写原因")
        state = await self._pause.set_paused(paused=paused, by=str(user_id), reason=reason)
        await self._audit(
            action="restock.pause" if paused else "restock.resume",
            # target_type 用 restock_pause 而不是 plan：这是“整个自动分析的开关”，
            # 不是某一份计划；混进 plan 会让审计页按计划过滤时冒出无意义条目
            target_type="restock_pause",
            target_id=PAUSE_SCOPE_GLOBAL,
            user_id=user_id,
            result=RESULT_OK,
            input_context={
                "reason": reason,
                "paused": paused,
                "pausedBy": state.paused_by,
                "resumedBy": state.resumed_by,
            },
        )
        return state

    # ---------------- 内部 ----------------

    def _request_id(self, *, plan_id: int) -> str:
        """全链路 request_id（AGENTS §6.4）优先，缺失时回退到可追查的合成值。

        为什么要回退而不是直接给 contextvar 的缺省值 `-`：本地脚本/直连调用没有网关，
        回调头里写 `-` 等于没写；`restock-confirm-<plan_id>` 至少能在 Java 日志里定位到计划。
        """
        rid = current_request_id()
        return rid if rid and rid != _NO_REQUEST_ID else f"restock-confirm-{plan_id}"

    async def _audit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        user_id: int | None,
        result: int,
        input_context: dict[str, Any],
        error_msg: str | None = None,
    ) -> None:
        """写一条人工干预决策留痕（1-7b / FIX-2，AGENTS §6.3 强制项）。

        三条纪律：
        1. **留痕失败不得中断业务**——审计是旁路，捕获后只记 WARN（同 0-12 的对话链路）；
           这里**故意用宽异常**：连接池耗尽、配置缺失、脱敏实现异常各异，
           而审计侧的任何一种都不该让运营「点了确认却什么都没发生」。
           不是静默吐掉：WARN + exc_info 会把堆栈打进日志，便于事后补录。
        2. 审计关闭（`DKD_AGENT_AUDIT_ENABLED=0`）时不写，便于无库环境跑测试。
        3. **不写 `llm_output`**：人工作业没有 LLM 输出，把人工参数塞进去会让 3-7 审计页
           把「人做的事」渲染成「模型建议的事」；结果因此全部放在 `input_context` 里。
        """
        if not self._settings.audit_enabled:
            return
        rid = current_request_id()
        try:
            await self._audit_writer(
                scene=SCENE_RESTOCK,
                action=action,
                request_id=None if rid in (None, "", _NO_REQUEST_ID) else rid,
                user_id=user_id,
                trigger_type=TRIGGER_MANUAL,
                input_context=input_context,
                target_type=target_type,
                target_id=target_id,
                result=result,
                error_msg=error_msg,
            )
        except Exception as exc:  # noqa: BLE001 —— 故意宽捕获：见 docstring 纪律 1（WARN + 堆栈，非静默）
            logger.warning(
                "决策留痕写入失败（业务不受影响，需事后补录）action=%s target=%s err=%s",
                action,
                target_id,
                type(exc).__name__,
                exc_info=True,
            )

    def _audit_context(
        self,
        plan: RestockPlan,
        decision: PlanDecision,
        *,
        plan_id: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """决策留痕的输入上下文——**这就是 3-7 审计页的正文**，字段必须自解释。"""
        context: dict[str, Any] = {
            "planId": plan_id,
            "planDate": plan.plan_date,
            "vmId": plan.vm_id,
            "innerCode": plan.inner_code,
            "planStatusBefore": plan.status,
            "reason": decision.reason,
            "items": [item.model_dump(by_alias=True, exclude_none=True) for item in decision.items],
        }
        if extra:
            context.update(extra)
        return context

    async def _audit_decision(
        self,
        decision: PlanDecision,
        *,
        plan_id: int,
        result: int,
        input_context: dict[str, Any],
        error_msg: str | None = None,
    ) -> None:
        """计划类决策的留痕（四类动作共用；动作名带 `restock.` 前缀对齐 DDL 注释示例）。"""
        await self._audit(
            action=f"restock.{decision.action}",
            target_type="plan",
            target_id=str(plan_id),
            user_id=decision.user_id,
            result=result,
            input_context=input_context,
            error_msg=error_msg,
        )

    async def _load(self, plan_id: int, plan_date: date) -> tuple[dict[str, Any], RestockPlan]:
        row = await self.get_plan_row(plan_id, plan_date)
        return row, plan_from_row(row)

    async def _recompute(
        self, plan: RestockPlan, *, payload: AdjustmentRequest, plan_date: date
    ) -> dict[str, Any]:
        """按人工指定的预测参数重算（数据来源与 06:00 分析完全同源）。"""
        channel_code = payload.channel_code or plan.items[0].channel_code
        stock, daily = await self._deps.load(
            plan_date=plan_date,
            limit=self._settings.read_row_limit,
            inner_codes=[plan.inner_code],
        )
        channel_stock = [
            item for item in stock if item.channel_code == channel_code and item.vm_id == plan.vm_id
        ]
        if not channel_stock:
            raise RestockServiceError(
                f"货道 {channel_code} 已不在设备 {plan.inner_code} 的库存档案里，无法重算",
                status_code=409,
            )
        try:
            suggestions = compute_device_baseline(
                stock_items=channel_stock,
                daily_rows=[row for row in daily if row.channel_code == channel_code],
                today=plan_date,
                settings=self._settings,
                window_days=payload.window_days,
                service_level=payload.service_level,
                coverage_days=payload.coverage_days,
                # 设备是否有样本由 1-4 从传进来的销量行推断；这里只送单货道数据，
                # 因此显式告知“该设备有样本”（计划存在本身就说明设备在运营）
                device_has_sample=True,
            )
        except ValueError as exc:  # 参数越界（窗口不在配置内 / 服务水平越界）
            raise RestockServiceError(str(exc)) from exc
        suggestion = suggestions[0]
        logger.info(
            "按人工参数重算 vm_id=%s channel=%s window=%s service=%s → 建议=%s",
            plan.vm_id,
            channel_code,
            payload.window_days,
            payload.service_level,
            suggestion.suggested_quantity,
        )
        return {
            "channelCode": channel_code,
            "suggestedQuantity": suggestion.suggested_quantity,
            "dailyDemand": suggestion.daily_demand,
            "reason": suggestion.reason,
        }

    async def _execute(
        self,
        plan: RestockPlan,
        decision: PlanDecision,
        *,
        plan_id: int,
        task_id: int | None = None,
        audit_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """状态机 → 落库 → （确认时）建单。返回给前端的结果结构对四类操作统一。

        每个**终态**都写一条 `agent_decision_log`（1-7b / FIX-2）：成功、状态机拒绝、
        并发冲突、建单失败四条路径都要留痕——审计页要能回答「谁在什么时候点了什么、
        最后怎么样」，只记成功的留痕等于把失败藏起来。
        参数校验类拒绝（400，如原因留空/服务水平越界）不在此列，故不留痕：
        它们没有触碰任何业务状态，请求本身已在访问日志里（本方法的边界如此，非疏漏）。
        """
        context = self._audit_context(plan, decision, plan_id=plan_id, extra=audit_extra)
        try:
            new_plan, result, note = apply_decision(plan, decision, task_id=task_id)
        except DecisionRejectedError as exc:
            logger.warning("决策被拒 plan_id=%s action=%s reason=%s", plan_id, decision.action, exc)
            await self._audit_decision(
                decision,
                plan_id=plan_id,
                result=RESULT_REJECTED,
                input_context={**context, "effect": "rejected"},
                error_msg=str(exc),
            )
            raise RestockServiceError(str(exc), status_code=409) from exc

        if result == RESULT_ALREADY_ORDERED:
            # 幂等：不再落库、不再建单，直接把“已建单”如实告诉运营（排期 1-8 验收项 ②）
            await self._audit_decision(
                decision,
                plan_id=plan_id,
                result=RESULT_OK,
                input_context={
                    **context,
                    "effect": "already_ordered",
                    "planStatusAfter": new_plan.status,
                },
            )
            return {
                "planId": plan_id,
                "vmId": plan.vm_id,
                "status": new_plan.status,
                "result": result,
                "note": note,
                "plan": summarize_plan(new_plan, plan_id=plan_id),
            }

        created: dict[str, Any] | None = None
        if result == RESULT_PENDING_ORDER:
            try:
                task = await self._deps.create_task(
                    new_plan, request_id=self._request_id(plan_id=plan_id)
                )
            except OrderClaimConflictError as exc:
                # 并发确认（排期 1-8）：另一个请求已经抢到该计划的建单权。
                # 409 而不是 502 —— 这不是“Java 出错了”，是“这次请求不该执行”，
                # 运营收到“正在建单中，请刷新”就对了。
                logger.info("建单认领冲突 plan_id=%s reason=%s", plan_id, exc)
                await self._audit_decision(
                    decision,
                    plan_id=plan_id,
                    result=RESULT_REJECTED,
                    input_context={**context, "effect": "concurrent_conflict"},
                    error_msg=str(exc),
                )
                raise RestockServiceError(str(exc), status_code=409) from exc
            except CallbackError as exc:
                # 建单失败：状态留在原处并如实回报（1-6 的同类取舍：回滚会让运营以为没点过）；
                # 占位释放由 deps.create_task 内部完成（1-8），所以这里不必再动库。
                logger.warning("建单失败 plan_id=%s err=%s", plan_id, exc)
                await self._audit_decision(
                    decision,
                    plan_id=plan_id,
                    result=RESULT_FAIL,
                    input_context={**context, "effect": "create_failed"},
                    error_msg=str(exc),
                )
                raise RestockServiceError(f"建单失败：{exc}", status_code=502) from exc
            new_plan = new_plan.model_copy(update={"status": 4})
            await self._store.save_decision(
                new_plan,
                by=str(decision.user_id or "agent"),
                adjusted_by=decision.user_id,
                adjust_reason=decision.reason,
                task_id=task.task_id,
                task_code=task.task_code,
            )
            created = {"taskId": task.task_id, "taskCode": task.task_code}
            await self._audit_decision(
                decision,
                plan_id=plan_id,
                result=RESULT_OK,
                input_context={
                    **context,
                    "effect": "created",
                    "planStatusAfter": new_plan.status,
                    "taskId": task.task_id,
                    "taskCode": task.task_code,
                },
            )
        else:
            await self._store.save_decision(
                new_plan,
                by=str(decision.user_id or "agent"),
                adjusted_by=decision.user_id,
                adjust_reason=decision.reason,
            )
            await self._audit_decision(
                decision,
                plan_id=plan_id,
                result=RESULT_OK,
                input_context={
                    **context,
                    # 状态机返回的动作结果（adjusted/skipped/restored/assigned/unassigned）
                    "effect": result,
                    "planStatusAfter": new_plan.status,
                },
            )

        return {
            "planId": plan_id,
            "vmId": plan.vm_id,
            "status": new_plan.status,
            "result": result,
            "note": note,
            "task": created,
            "plan": summarize_plan(new_plan, plan_id=plan_id),
        }


def default_window(plan_date: date, settings: Settings | None = None) -> tuple[Any, Any]:
    """默认分析窗口（供 API 层回显“本次用的是哪个窗口”，避免前端自己算边界算错）。"""
    s = settings or get_settings()
    return default_sales_window(days=max(s.restock_weights), today=plan_date)
