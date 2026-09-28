"""06:00 定时补货分析的触发服务（Phase 1 / 任务 1-9 + FIX-10）。

本模块回答一个很小但必须做对的问题：**现在能不能跑今天的补货分析？跑完是什么结果？**

## 四个设计取舍

1. **暂停开关在本层消费，不放图里。**
   `agent_restock_pause` 的语义是「暂停**自动**分析」（原型 V2 的横幅）。把它塞进图里，
   1-7 的人工干预路径（重算/确认）也会被拦住——那是另一个语义。所以开关判断留在这里，
   图保持纯粹；FIX-10 登记时说的正是「开关目前只是状态位、尚无消费方」，本模块是它的消费方。

2. **图只跑到人工确认处中断（interrupt），定时任务不等确认。**
   计划在 `analyze` 节点就已幂等落库（1-6），因此 06:00 之后工作台立刻有数据可看；
   定时任务没有任何理由保持一个挂着的中断去等运营上班。

3. **同一日期重复触发是安全的（1-9 的「任务重入安全」验收项）。**
   `thread_id` 固定为 `restock-<plan_date>`（与 1-6 验收脚本同一约定），重复触发等于重跑一次分析：
   `agent_restock_plan` 按 `(vm_id, plan_date)` 幂等 upsert，且已建单/已复盘的行不会被覆盖
   （1-6 的 `ON DUPLICATE KEY UPDATE` 条件）。**不做「已分析过就拒绝」**：数据修好后需要能重跑，
   把重跑变成“必须人工改库”的动作只会制造事故。

4. **成败都写 `agent_decision_log`**（scene=2 补货、trigger_type=2 定时任务）。
   定时分析同样是「写操作决策」（它写了计划行），AGENTS §6.3 要求留痕；
   只记成功等于把 06:00 的失败藏起来——那正是 §6.4「不允许半夜悄悄失败」要防的事。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.audit import RESULT_FAIL, RESULT_OK, TRIGGER_SCHEDULE
from app.config import Settings, get_settings
from app.graphs.restock_graph import new_restock_state
from app.graphs.restock_plan_store import PAUSE_SCOPE_GLOBAL
from app.graphs.restock_state import SCENE_RESTOCK
from app.services.decision_audit import AuditWriter, write_decision

logger = logging.getLogger("dkd.agent.services.restock_analyzer")

# 运行结果状态（Java 侧按它区分「跑过了」与「被暂停跳过」，两者都是 200）
STATUS_ANALYZED = "analyzed"
STATUS_SKIPPED = "skipped"

# 留痕目标：定时分析不是「某一份计划」而是「一轮计划」（target_id 用计划日期）
TARGET_TYPE_RUN = "restock_run"

# error_msg 列 VARCHAR(500)：超长会被截断入库/报错，这里主动截断并保留异常类型
_ERROR_MSG_MAX = 500


class RestockAnalyzeError(RuntimeError):
    """分析运行失败（HTTP 层映射 502：Python 自身故障，Java 侧应重试并告警）。"""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class AnalyzeOutcome(BaseModel):
    """一次触发的结果（对外契约，Java 定时任务按 `status` 区分成功/跳过）。

    JSON 键为 camelCase（Java/前端消费），Python 侧保持 snake_case 并用 alias 映射——
    与 `AdjustmentRequest` 同一做法，避免 `N815`（混写命名）与三端各写一份转换。
    """

    status: str
    plan_date: str = Field(alias="planDate")
    thread_id: str = Field(alias="threadId")
    interrupted: bool = False
    device_count: int = Field(default=0, alias="deviceCount")
    channel_count: int = Field(default=0, alias="channelCount")
    total_quantity: int = Field(default=0, alias="totalQuantity")
    reason: str | None = None
    paused_by: str | None = Field(default=None, alias="pausedBy")

    model_config = {"populate_by_name": True}


class RestockAnalyzer:
    """触发执行器（依赖注入 graph_factory / pause_store，便于单测替换）。"""

    def __init__(
        self,
        *,
        graph_factory: Callable[[], Any],
        pause_store: Any,
        settings: Settings | None = None,
        audit_writer: AuditWriter | None = None,
        today_provider: Callable[[], date] | None = None,
    ) -> None:
        self._graph_factory = graph_factory
        self._pause = pause_store
        self._settings = settings or get_settings()
        self._audit_writer = audit_writer
        self._today = today_provider or date.today

    async def analyze(
        self, *, plan_date: date | None = None, request_id: str | None = None
    ) -> AnalyzeOutcome:
        """跑一轮分析；被暂停时**不创建图、不读业务库**，直接返回 skipped。

        @param plan_date 目标日期（缺省今天）；显式传入便于 Java 侧与 Python 侧日期口径一致
        @param request_id 触发方（Java 定时任务）带来的全链路 ID；缺省时留痕写 NULL
        @raises RestockAnalyzeError 图执行失败（含取数超时/落库失败）
        """
        day = plan_date or self._today()
        date_text = day.isoformat()
        thread_id = f"restock-{date_text}"

        state = await self._pause.get()
        if state.paused:
            logger.info(
                "自动分析已暂停，跳过本轮 plan_date=%s by=%s reason=%s",
                date_text,
                state.paused_by,
                state.reason,
            )
            await self._audit(
                action="restock.analyze",
                target_type="restock_pause",
                target_id=PAUSE_SCOPE_GLOBAL,
                result=RESULT_OK,
                input_context={
                    "planDate": date_text,
                    "threadId": thread_id,
                    "effect": "skipped_paused",
                    "pausedBy": state.paused_by,
                    "pauseReason": state.reason,
                },
            )
            return AnalyzeOutcome(
                status=STATUS_SKIPPED,
                planDate=date_text,
                threadId=thread_id,
                reason=state.reason,
                pausedBy=state.paused_by,
            )

        # 每轮新建图与 deps：deps 内部有「本轮设备/员工/负载」缓存，
        # 跨轮复用会让长驻进程拿上一轮的员工名单派今天的单（1-6 的 load() 会重置，
        # 但并发两轮时会互相踩缓存——新建的代价只是编译一张 5 节点小图）
        graph = self._graph_factory()
        try:
            result = await graph.ainvoke(
                new_restock_state(
                    plan_date=date_text, request_id=request_id or "", trigger_type=TRIGGER_SCHEDULE
                ),
                {"configurable": {"thread_id": thread_id}},
            )
        except Exception as exc:  # noqa: BLE001 —— 编排异常一律收敛成结果，不抛穿到调用方之外（AGENTS §6.3）
            logger.error(
                "补货分析失败 plan_date=%s thread=%s err=%s",
                date_text,
                thread_id,
                type(exc).__name__,
                exc_info=True,
            )
            await self._audit(
                action="restock.analyze",
                target_type=TARGET_TYPE_RUN,
                target_id=date_text,
                result=RESULT_FAIL,
                input_context={
                    "planDate": date_text,
                    "threadId": thread_id,
                    "effect": "failed",
                    "errorType": type(exc).__name__,
                },
                error_msg=f"{type(exc).__name__}: {exc}"[:_ERROR_MSG_MAX],
            )
            raise RestockAnalyzeError(f"补货分析运行失败：{type(exc).__name__}: {exc}") from exc

        plans = list(result.get("plans") or [])
        device_count = len(plans)
        channel_count = sum(len(plan.get("items") or []) for plan in plans)
        total_quantity = sum(int(plan.get("total_quantity") or 0) for plan in plans)
        interrupted = bool(result.get("__interrupt__"))
        logger.info(
            "补货分析完成 plan_date=%s 设备=%s 货道=%s 建议总数=%s 中断=%s",
            date_text,
            device_count,
            channel_count,
            total_quantity,
            interrupted,
        )
        await self._audit(
            action="restock.analyze",
            target_type=TARGET_TYPE_RUN,
            target_id=date_text,
            result=RESULT_OK,
            input_context={
                "planDate": date_text,
                "threadId": thread_id,
                "effect": "analyzed",
                "deviceCount": device_count,
                "channelCount": channel_count,
                "totalQuantity": total_quantity,
                "interrupted": interrupted,
                "calibrated": bool(result.get("calibration_notes")),
            },
        )
        return AnalyzeOutcome(
            status=STATUS_ANALYZED,
            planDate=date_text,
            threadId=thread_id,
            interrupted=interrupted,
            deviceCount=device_count,
            channelCount=channel_count,
            totalQuantity=total_quantity,
        )

    async def _audit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        result: int,
        input_context: dict[str, Any],
        error_msg: str | None = None,
    ) -> None:
        """写一条定时分析留痕（纪律与人工干预完全一致，见 `decision_audit` 模块 docstring）。"""
        await write_decision(
            settings=self._settings,
            writer=self._audit_writer,
            scene=SCENE_RESTOCK,
            action=action,
            target_type=target_type,
            target_id=target_id,
            user_id=None,  # 定时任务无操作人：`create_by` 落 `agent`，不伪造一个用户
            trigger_type=TRIGGER_SCHEDULE,
            result=result,
            input_context=input_context,
            error_msg=error_msg,
        )


def build_default_analyzer(
    *, checkpointer: Any, settings: Settings | None = None
) -> RestockAnalyzer:
    """生产装配（`app.main` 的 lifespan 调用）：真实 checkpointer + 真实只读依赖 + 真实开关表。"""
    from app.graphs.restock_graph import SqlRestockDeps, build_restock_graph
    from app.graphs.restock_plan_store import SqlPauseStore

    effective = settings or get_settings()

    def _graph_factory() -> Any:
        return build_restock_graph(
            deps=SqlRestockDeps(settings=effective),
            checkpointer=checkpointer,
            settings=effective,
        )

    return RestockAnalyzer(
        graph_factory=_graph_factory,
        pause_store=SqlPauseStore(),
        settings=effective,
    )
