"""故障场景 F-09：派单提案的人工确认与幂等建单。

## 为什么用 DB 状态机两阶段，而不是 LangGraph `interrupt()`（实现决策 D11）

计划里 F-09 写的是 `interrupt()`；本轮改为「提案落库 → 独立确认调用」两阶段，原因：

1. 确认动作来自**独立 HTTP 请求**（运营点按钮），天然跨进程/跨实例；
   DB 状态机（`waiting_confirm → running → done`）本身就是跨进程协调器，
   不需要把图实例、thread_id、checkpointer 一起持久化再恢复；
2. 中断恢复的语义在 F-09 里只需要"别重复建单"，而幂等的真正兜底是
   **Java 侧同设备同类型在途工单防重** + 本模块的 claim/回查，不是 checkpointer；
3. 少一层依赖：项目已有 PG/SQLite checkpointer 仅用于对话/补货图，
   故障图当前无多步人工介入；引入 interrupt 会多一条需要长期维护的恢复路径
   （AGENTS：不新增新旧两套并存路径）。

若将来故障场景出现"多轮人工介入"（例如审核官也要人确认），再评估把图挂 checkpointer 并用 interrupt。

## 幂等模型（状态迁移只有这一个入口）

```
waiting_confirm --claim--> running --Java 成功--> done（写 step3 + taskId）
                                |--业务拒绝--> waiting_confirm（原因写 step3.error_msg，可改人重试）
                                |--传输/鉴权失败--> waiting_confirm（可重试）
waiting_confirm --reject--> done（人工驳回，不建单）
done + 已有 dispatch_confirm step --> 重复确认直接返回同一 task（幂等，不重复回调）
running（并发确认）--> in_progress（调用方提示"正在建单中"，不要重试）
```

## 边界

- 只经 `task_tools.create_repair_task` 走 Java 回调建单；本模块**不碰**任何 `tb_*` 表；
- 不改设备状态（计划 D7）；
- 每次确认/驳回都写 `agent_step`（过程）与 `agent_decision_log`（决策留痕，AGENTS §6.3）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from app.db import get_sessionmaker
from app.graphs.fault_graph import ROLE_DISPATCHER, FaultDeps
from app.obs.run_recorder import finish_run, record_step
from app.services.decision_audit import resolve_request_id, write_decision
from app.tools.task_tools import (
    CallbackAuthError,
    CallbackBusinessError,
    CallbackUnavailableError,
    CreatedTask,
    create_repair_task,
)

logger = logging.getLogger("dkd.agent.graphs.fault_confirm")

ACTION_DISPATCH_CONFIRM = "dispatch_confirm"
ACTION_DISPATCH_PROPOSE = "dispatch_propose"
CONFIRM_STEP_NO = 3

# 确认动作（拒绝与确认共用同一入口：都改变运行状态，必须走同一条幂等路径）
ACTION_CONFIRM = "confirm"
ACTION_REJECT = "reject"

# 回调签名（单测注入假实现；生产默认 task_tools.create_repair_task）
RepairTaskCallback = Callable[..., Awaitable[CreatedTask]]


@dataclass
class ConfirmResult:
    """确认结果（调用方按 status 决定 HTTP 语义）。"""

    status: str
    run_id: str
    task_id: int | None = None
    task_code: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "runId": self.run_id,
            "taskId": self.task_id,
            "taskCode": self.task_code,
            "message": self.message,
        }


class ConfirmStore(Protocol):
    """确认流程的持久化接口（单测注入假实现，不连库）。"""

    async def load_run(self, run_id: str) -> dict[str, Any] | None:
        """读运行（status/subject_id）。"""

    async def load_proposal(self, run_id: str) -> dict[str, Any] | None:
        """读 step2 提案。"""

    async def find_confirmed_task(self, run_id: str) -> dict[str, Any] | None:
        """读已建单结果（幂等回查）。"""

    async def claim(self, run_id: str) -> bool:
        """CAS：waiting_confirm → running，返回是否抢到。"""

    async def release(self, run_id: str, reason: str) -> None:
        """回退：running → waiting_confirm（建单失败可重试）。"""


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)) and value:
        try:
            payload = json.loads(value)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


class SqlConfirmStore:
    """默认实现：直接读写 dkd 库的 agent_run / agent_step（仅 SELECT/UPDATE）。"""

    async def load_run(self, run_id: str) -> dict[str, Any] | None:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text(
                    """
                    select run_id, status, subject_id, stop_reason
                    from agent_run
                    where run_id = :run_id and del_flag = '0'
                    """
                ),
                {"run_id": run_id},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def load_proposal(self, run_id: str) -> dict[str, Any] | None:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text(
                    """
                    select evidence_json
                    from agent_step
                    where run_id = :run_id and action = :action
                    order by step_no desc, id desc
                    limit 1
                    """
                ),
                {"run_id": run_id, "action": ACTION_DISPATCH_PROPOSE},
            )
            row = result.mappings().first()
            if not row:
                return None
            proposal = _parse_json(row["evidence_json"]).get("proposal")
            return proposal if isinstance(proposal, dict) else None

    async def find_confirmed_task(self, run_id: str) -> dict[str, Any] | None:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text(
                    """
                    select evidence_json
                    from agent_step
                    where run_id = :run_id and action = :action
                    order by id desc
                    limit 1
                    """
                ),
                {"run_id": run_id, "action": ACTION_DISPATCH_CONFIRM},
            )
            row = result.mappings().first()
            if not row:
                return None
            payload = _parse_json(row["evidence_json"])
            task = payload.get("task")
            return task if isinstance(task, dict) else None

    async def claim(self, run_id: str) -> bool:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text(
                    """
                    update agent_run
                       set status = 'running', stop_reason = null, update_time = now()
                     where run_id = :run_id and status = 'waiting_confirm' and del_flag = '0'
                    """
                ),
                {"run_id": run_id},
            )
            await session.commit()
            return int(result.rowcount or 0) == 1

    async def release(self, run_id: str, reason: str) -> None:
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    """
                    update agent_run
                       set status = 'waiting_confirm', stop_reason = :reason, update_time = now()
                     where run_id = :run_id and status = 'running'
                    """
                ),
                {"run_id": run_id, "reason": reason[:200]},
            )
            await session.commit()


async def _audit(
    deps: FaultDeps,
    *,
    run_id: str,
    writer: Any,
    action: str,
    inner_code: str,
    result: int,
    assignee_id: int | None,
    priority: int | None,
    remark: str | None,
    error_msg: str | None = None,
) -> None:
    """写决策留痕（失败只 WARN，由 `write_decision` 内部兜底）。"""
    await write_decision(
        settings=deps.settings,
        writer=writer,
        scene=3,  # 与 agent_decision_log.scene 的“3-诊断”一致
        action=action,
        target_type="vm",
        target_id=inner_code,
        user_id=None,  # 运营身份由 Java 侧 oper_log 记录；此处不伪造用户
        trigger_type=3,
        result=result,
        input_context={
            "runId": run_id,
            "assigneeId": assignee_id,
            "priority": priority,
            "remark": remark,
        },
        error_msg=error_msg,
    )


async def confirm_fault_dispatch(
    deps: FaultDeps,
    *,
    run_id: str,
    action: str = ACTION_CONFIRM,
    assignee_id: int | None = None,
    priority: int | None = None,
    remark: str | None = None,
    operator: str = "operator",
    store: ConfirmStore | None = None,
    callback: RepairTaskCallback | None = None,
    audit_writer: Any = None,
) -> ConfirmResult:
    """确认/驳回一次派单提案（幂等；失败可重试，绝不重复建单）。

    @param action confirm=建单 / reject=人工驳回
    @param assignee_id 覆盖提案接单人（运营改派）；不传则用提案里的
    @param priority 覆盖优先级（仅留痕展示；Java TaskDto 无该字段）
    @param remark 运营备注（驳回必填；确认时并入工单描述）
    @param store 持久化实现（单测注入假实现）
    @param callback 建单回调（单测注入假实现）
    @param audit_writer 决策留痕写入器（单测注入假实现）
    """
    if action not in (ACTION_CONFIRM, ACTION_REJECT):
        return ConfirmResult("invalid", run_id, message=f"不支持的动作：{action}")

    target_store: ConfirmStore = store or SqlConfirmStore()
    run = await target_store.load_run(run_id)
    if run is None:
        return ConfirmResult("not_found", run_id, message="运行不存在")

    # 幂等：已有建单结果就直接返回，不重复回调（无论当前 status 是 done 还是异常回退中的脏状态）
    confirmed = await target_store.find_confirmed_task(run_id)
    if confirmed is not None:
        return ConfirmResult(
            "duplicate",
            run_id,
            task_id=confirmed.get("taskId"),
            task_code=confirmed.get("taskCode"),
            message="该运行已建单，返回原工单（未重复建单）",
        )

    if run.get("status") == "running":
        return ConfirmResult("in_progress", run_id, message="正在建单中，请勿重复提交")
    if run.get("status") != "waiting_confirm":
        return ConfirmResult(
            "not_waiting",
            run_id,
            message=f"当前状态 {run.get('status')} 不可确认（仅 waiting_confirm 允许）",
        )

    proposal = await target_store.load_proposal(run_id)
    if proposal is None:
        return ConfirmResult("not_waiting", run_id, message="派单提案缺失，无法确认")

    if action == ACTION_REJECT:
        if not remark:
            return ConfirmResult("invalid", run_id, message="驳回必须填写原因")
        if not await target_store.claim(run_id):
            return ConfirmResult("in_progress", run_id, message="运行状态已被其他请求改变，请刷新")
        await record_step(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=CONFIRM_STEP_NO,
            role_code=ROLE_DISPATCHER,
            action=ACTION_DISPATCH_CONFIRM,
            summary="人工驳回派单提案",
            evidence={"proposal": proposal, "rejectedBy": operator, "remark": remark},
            status="skipped",
        )
        await finish_run(
            deps.settings,
            deps.store,
            run_id=run_id,
            status="done",
            stop_reason=f"人工驳回：{remark}"[:200],
        )
        await _audit(
            deps,
            run_id=run_id,
            writer=audit_writer,
            action="fault.dispatch_reject",
            inner_code=str(run.get("subject_id") or ""),
            result=3,
            assignee_id=assignee_id,
            priority=priority,
            remark=remark,
        )
        return ConfirmResult("rejected", run_id, message="已驳回，未建单")

    assignee = assignee_id if assignee_id is not None else proposal.get("assigneeId")
    if assignee is None:
        # 提案里没有接单人（派单官当时没找到人）：需要先补派，不能静默建单给 None
        return ConfirmResult("rejected_by_business", run_id, message="提案无接单人，请先补派")

    if not await target_store.claim(run_id):
        # 并发：另一个请求刚抢到 —— 再查一次是否已完成（短窗口内可能已经建完）
        confirmed = await target_store.find_confirmed_task(run_id)
        if confirmed is not None:
            return ConfirmResult(
                "duplicate",
                run_id,
                task_id=confirmed.get("taskId"),
                task_code=confirmed.get("taskCode"),
                message="该运行已建单，返回原工单（未重复建单）",
            )
        return ConfirmResult("in_progress", run_id, message="正在建单中，请勿重复提交")

    inner_code = str(run.get("subject_id") or "")
    desc = str(proposal.get("desc") or "")
    if remark:
        desc = f"{desc}（运营备注：{remark}）"
    request_id = resolve_request_id()
    repair_callback = callback or create_repair_task

    try:
        created = await repair_callback(
            inner_code=inner_code,
            assignee_id=int(assignee),
            desc=desc,
            request_id=request_id,
        )
    except CallbackBusinessError as exc:
        # Java 业务校验拒绝（设备有未完成工单 / 员工区域不一致 / 售货机不存在）：
        # 回退到 waiting_confirm，运营可改派或处理在途工单后重试；文案原样透传
        await target_store.release(run_id, f"建单被业务拒绝：{exc}")
        await record_step(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=CONFIRM_STEP_NO,
            role_code=ROLE_DISPATCHER,
            action=ACTION_DISPATCH_CONFIRM,
            summary="建单被业务拒绝",
            evidence={"proposal": proposal, "assigneeId": int(assignee), "remark": remark},
            status="error",
            error_msg=f"建单被业务拒绝：{exc}",
        )
        await _audit(
            deps,
            run_id=run_id,
            writer=audit_writer,
            action="fault.dispatch_confirm",
            inner_code=inner_code,
            result=3,
            assignee_id=int(assignee),
            priority=priority,
            remark=remark,
            error_msg=str(exc),
        )
        return ConfirmResult("rejected_by_business", run_id, message=str(exc))

    except (CallbackAuthError, CallbackUnavailableError) as exc:
        # 鉴权/传输失败：不改变状态（保留 waiting_confirm 供重试），但要留下可排障的 step
        await target_store.release(run_id, f"建单失败可重试：{exc}")
        await record_step(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=CONFIRM_STEP_NO,
            role_code=ROLE_DISPATCHER,
            action=ACTION_DISPATCH_CONFIRM,
            summary="建单未完成（可重试）",
            evidence={"proposal": proposal, "assigneeId": int(assignee), "remark": remark},
            status="error",
            error_msg=str(exc),
        )
        await _audit(
            deps,
            run_id=run_id,
            writer=audit_writer,
            action="fault.dispatch_confirm",
            inner_code=inner_code,
            result=2,
            assignee_id=int(assignee),
            priority=priority,
            remark=remark,
            error_msg=str(exc),
        )
        return ConfirmResult("unavailable", run_id, message=str(exc))

    await record_step(
        deps.settings,
        deps.store,
        run_id=run_id,
        step_no=CONFIRM_STEP_NO,
        role_code=ROLE_DISPATCHER,
        action=ACTION_DISPATCH_CONFIRM,
        summary=f"已建维修工单 {created.task_code or created.task_id}",
        evidence={
            "proposal": proposal,
            "assigneeId": int(assignee),
            "remark": remark,
            "confirmedBy": operator,
            "task": {"taskId": created.task_id, "taskCode": created.task_code},
        },
    )
    await finish_run(deps.settings, deps.store, run_id=run_id, status="done", stop_reason=None)
    await _audit(
        deps,
        run_id=run_id,
        writer=audit_writer,
        action="fault.dispatch_confirm",
        inner_code=inner_code,
        result=1,
        assignee_id=int(assignee),
        priority=priority,
        remark=remark,
    )
    logger.info(
        "故障派单确认完成 run_id=%s task_id=%s task_code=%s",
        run_id,
        created.task_id,
        created.task_code,
    )
    return ConfirmResult(
        "confirmed", run_id, task_id=created.task_id, task_code=created.task_code, message="已建单"
    )


__all__ = [
    "ACTION_CONFIRM",
    "ACTION_REJECT",
    "ConfirmResult",
    "ConfirmStore",
    "SqlConfirmStore",
    "confirm_fault_dispatch",
]
