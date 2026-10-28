"""故障诊断场景图（M2：诊断官 + 证据硬校验，F-06/F-07）。

## 这一版做了什么

一次运行（`agent_run`，scene_code=fault）里跑一轮"诊断官"：
1. 取事实（6 次只读工具，全部走 `read_tools` 的白名单/分批/超时护栏）；
2. 用 `compose_diagnosis()` 组装结构化结论（**纯函数，不调 LLM**）；
3. **证据回溯（F-07）**：每条证据必须能指回本轮某次工具调用的返回行，
   指不回去的丢弃；丢光则 `cannotConclude=true`、置信度归零、**禁止派单**；
4. 写入 `agent_run` / `agent_step` / `agent_tool_call`（失败只 WARN，不阻断业务）。

## 这一版刻意不做

- 不建单、不改库存：写操作在 F-09 的派单官里，且必须过 `interrupt()` 人工确认；
- 不调 LLM：诊断结论本来就该由证据规则算出（复用 2-4 的 `compose_diagnosis`），
  LLM 在故障场景里只应参与"解释与选人"，不参与"事实判断"；
- 不挂 checkpointer：F-09 加中断时再挂，届时 state 已保证可 JSON 序列化（见 `fault_state`）。

## 为什么主管用代码路由

只有 3 个角色（诊断/派单/审核），路由条件可枚举（有没有诊断结论、有没有提案、要不要复核）；
代码路由可单测、可复现、零 token。LLM 只在角色内部解释与选择。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.db import get_sessionmaker
from app.graphs.fault_state import FaultState
from app.obs.run_recorder import finish_run, record_step, record_tool_call, start_run
from app.services.diagnosis import (
    CONFIDENCE_NONE,
    DiagnosisConclusion,
    DiagnosisEvidence,
    compose_diagnosis,
)
from app.tools import read_tools

logger = logging.getLogger("dkd.agent.graphs.fault")

ROLE_DIAGNOSTICIAN = "diagnostician"
ACTION_DIAGNOSE = "diagnose"

# M3/F-08：派单官。只产出**提案**，不建单——建单在 F-09 由人工确认后触发（AGENTS §7.9）
ROLE_DISPATCHER = "dispatcher"
ACTION_DISPATCH_PROPOSE = "dispatch_propose"

# 提案优先级（1-低 / 2-中 / 3-高 / 4-紧急），口径与补货工作台 priorityLabel 一致
PRIORITY_LOW = 1
PRIORITY_MEDIUM = 2
PRIORITY_HIGH = 3

DEFAULT_WINDOW_HOURS = 24

# 证据 kind 前缀 → 产出它的工具。回溯不到工具调用 = 证据不可复核 = 必须丢弃（F-07）
KIND_TO_TOOL: dict[str, str] = {
    "order_anomaly": "list_order_anomalies",
    "inventory_anomaly": "list_inventory_anomalies",
    "history_cases": "search_history_cases",
    "inflight_work_orders": "list_inflight_tasks",
}

# 不能下结论时**不得保留**的动作：这些动作会引导运营去建单/派单
DISPATCH_ACTION_MARKERS = ("新建维修工单", "先处理/关闭在途工单")


@dataclass
class FaultDeps:
    """图运行所需的外部依赖（测试可整体替换，不需要连库/连模型）。"""

    settings: Settings
    store: Any = None
    now: Callable[[], datetime] | None = None

    def clock(self) -> datetime:
        """当前时间（测试注入固定时钟，避免用例随系统时间漂移）。"""
        return self.now() if self.now is not None else datetime.now()


@dataclass
class EvidenceTrace:
    """证据回溯结果（F-07 的唯一判定出口）。"""

    kept: list[DiagnosisEvidence] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    cannot_conclude: bool = True
    dispatch_allowed: bool = False


@dataclass
class DispatchProposal:
    """派单提案（F-08 的唯一产物；F-09 经人工确认后才建单）。

    为什么把"能不能派"与"派的依据"放在同一个对象里：只给一个 assigneeId 的话，
    页面无法解释"为什么派给这个人、为什么现在派"，运营只能盲信；提案必须自带
    `based_on_evidence`（诊断证据）与 `assignee_basis`（区域/候选数/在途数）。
    """

    inner_code: str
    assignee_id: int | None
    assignee_name: str | None
    priority: int
    desc: str
    based_on_evidence: list[str] = field(default_factory=list)
    assignee_basis: dict[str, Any] = field(default_factory=dict)
    dispatchable: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """出口统一 camelCase（与 diagnosis 的 serialization_alias 约定一致）。"""
        return {
            "innerCode": self.inner_code,
            "assigneeId": self.assignee_id,
            "assigneeName": self.assignee_name,
            "priority": self.priority,
            "desc": self.desc,
            "basedOnEvidence": list(self.based_on_evidence),
            "assigneeBasis": dict(self.assignee_basis),
            "dispatchable": self.dispatchable,
            "reason": self.reason,
        }


def _row_count(rows: Any) -> int:
    """统一算"工具返回了几行"：list 取长度；HistoryCaseSearch 取 cases 长度。"""
    if rows is None:
        return 0
    if isinstance(rows, list):
        return len(rows)
    cases = getattr(rows, "cases", None)
    if cases is not None:
        return len(cases)
    return 1


async def collect_facts(
    deps: FaultDeps,
    *,
    run_id: str,
    inner_code: str,
    window_hours: int,
    step_no: int = 1,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    """取诊断所需事实，并把每次工具调用写进 `agent_tool_call`。

    @return `(facts, tool_calls, error)`；设备不存在时 error=`device_not_found`
    """
    end = deps.clock()
    start = end - timedelta(hours=window_hours)
    calls: list[dict[str, Any]] = []
    seq = 0

    async def run_tool(
        tool_code: str, factory: Callable[[], Any], params: dict[str, Any]
    ) -> Any:
        nonlocal seq
        seq += 1
        began = time.perf_counter()
        rows = await factory()
        duration_ms = int((time.perf_counter() - began) * 1000)
        row_count = _row_count(rows)
        await record_tool_call(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=step_no,
            call_seq=seq,
            tool_code=tool_code,
            params=params,
            row_count=row_count,
            duration_ms=duration_ms,
        )
        calls.append(
            {
                "tool_code": tool_code,
                "step_no": step_no,
                "call_seq": seq,
                "row_count": row_count,
                "duration_ms": duration_ms,
            }
        )
        return rows

    async with get_sessionmaker()() as session:
        profile = await run_tool(
            "get_machine_profile",
            lambda: read_tools.get_machine_profile(session, inner_code),
            {"innerCode": inner_code},
        )
        if profile is None:
            return {"profile": None}, calls, "device_not_found"

        orders = await run_tool(
            "list_order_anomalies",
            lambda: read_tools.list_order_anomalies(
                session, inner_codes=[inner_code], start=start, end=end
            ),
            {"innerCodes": [inner_code], "hours": window_hours},
        )
        inventory = await run_tool(
            "list_inventory_anomalies",
            lambda: read_tools.list_inventory_anomalies(
                session, inner_codes=[inner_code], start=start, end=end
            ),
            {"innerCodes": [inner_code], "hours": window_hours},
        )
        inflight = await run_tool(
            "list_inflight_tasks",
            lambda: read_tools.list_inflight_tasks(session, inner_codes=[inner_code]),
            {"innerCodes": [inner_code]},
        )
        history = await run_tool(
            "search_history_cases",
            lambda: read_tools.search_history_cases(
                session, inner_code=inner_code, start=start, end=end
            ),
            {"innerCode": inner_code, "hours": window_hours},
        )
        stock = await run_tool(
            "list_channel_stock",
            lambda: read_tools.list_channel_stock(session, inner_codes=[inner_code]),
            {"innerCodes": [inner_code]},
        )

    facts: dict[str, Any] = {
        "profile": profile,
        "orders": list(orders or []),
        "inventory": list(inventory or []),
        "inflight": list(inflight or []),
        "history": history,
        "stock": list(stock or []),
        "window": {
            "hours": window_hours,
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
        },
    }
    return facts, calls, None


def _check_row(  # noqa: C901 —— 证据类型枚举，拆函数只会让调用链更难读
    evidence: DiagnosisEvidence,
    call: dict[str, Any],
    *,
    orders: list[Any],
    inventory: list[Any],
    history: Any,
    inflight: list[Any],
) -> str | None:
    """校验一条证据能否指回本轮工具返回的**具体一行**。

    @return None 表示可回溯；否则返回丢弃原因（原因会写进 step.evidence_json，便于排障）
    """
    detail = evidence.detail or {}
    prefix = evidence.kind.split(":", 1)[0]

    if prefix == "order_anomaly":
        if call["row_count"] != len(orders):
            # 工具返回行数与事实条数不一致：要么事实被改写，要么记录写错，两者都不能信
            return "row_count_mismatch"
        order_id = detail.get("orderId")
        return None if any(item.order_id == order_id for item in orders) else "row_not_found"

    if prefix == "inventory_anomaly":
        if call["row_count"] != len(inventory):
            return "row_count_mismatch"
        log_id = detail.get("logId")
        return None if any(item.log_id == log_id for item in inventory) else "row_not_found"

    if prefix == "history_cases":
        if history is None or detail.get("totalCases") != history.total_cases:
            return "row_not_found"
        return None if call["row_count"] == len(history.cases) else "row_count_mismatch"

    if prefix == "inflight_work_orders":
        ids = set(detail.get("taskIds") or [])
        actual = {item.task_id for item in inflight}
        if ids != actual:
            return "row_not_found"
        return None if call["row_count"] == len(inflight) else "row_count_mismatch"

    return "unknown_kind"


def trace_evidence(
    conclusion: DiagnosisConclusion,
    *,
    facts: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> EvidenceTrace:
    """F-07：把每条证据回溯到本轮的工具调用返回行；回溯不到的丢弃。

    规则（可直接单测）：
    1. 证据 kind 必须先映射到工具，且本轮**确实调用过**该工具；
    2. 证据 detail 里的业务键（orderId / logId / totalCases / taskIds）必须命中事实集合；
    3. 工具记录的 `row_count` 必须等于事实集合大小（防"事实被改写但工具没返回那么多行"）。

    丢弃后若**没有任何订单/库存异常证据**，结论判 `cannot_conclude=True`、置信度归零、
    并移除所有会引导派单的动作（`dispatch_allowed=False`）。
    """
    calls_by_tool: dict[str, dict[str, Any]] = {}
    for call in tool_calls:
        calls_by_tool.setdefault(call["tool_code"], call)

    orders = list(facts.get("orders") or [])
    inventory = list(facts.get("inventory") or [])
    inflight = list(facts.get("inflight") or [])
    history = facts.get("history")

    kept: list[DiagnosisEvidence] = []
    dropped: list[dict[str, Any]] = []
    for evidence in conclusion.evidence:
        tool_code = KIND_TO_TOOL.get(evidence.kind.split(":", 1)[0])
        call = calls_by_tool.get(tool_code) if tool_code else None
        if call is None:
            dropped.append({"kind": evidence.kind, "reason": "tool_call_missing"})
            continue
        reason = _check_row(
            evidence,
            call,
            orders=orders,
            inventory=inventory,
            history=history,
            inflight=inflight,
        )
        if reason is not None:
            dropped.append({"kind": evidence.kind, "reason": reason})
            continue
        index = len(kept) + 1
        evidence.evidence_id = f"e{index}"
        evidence.tool_call_id = f"s{call['step_no']}c{call['call_seq']}"
        kept.append(evidence)

    has_anomaly = any(
        item.kind.startswith(("order_anomaly", "inventory_anomaly")) for item in kept
    )
    if not has_anomaly:
        conclusion.cannot_conclude = True
        conclusion.confidence = CONFIDENCE_NONE
        conclusion.confidence_basis = "过滤后没有可回溯到本轮工具返回的异常证据，按规则不得下结论"
        conclusion.recommended_actions = [
            action
            for action in conclusion.recommended_actions
            if not any(marker in action for marker in DISPATCH_ACTION_MARKERS)
        ]
    conclusion.evidence = kept

    return EvidenceTrace(
        kept=kept,
        dropped=dropped,
        cannot_conclude=conclusion.cannot_conclude,
        dispatch_allowed=bool(kept) and not conclusion.cannot_conclude,
    )


def _proposal_priority(diagnosis: dict[str, Any]) -> int:
    """优先级由证据强度决定（不让模型或调用方随手给）。"""
    evidence = diagnosis.get("evidence") or []
    if any(str(item.get("severity")) == "high" for item in evidence):
        return PRIORITY_HIGH
    if str(diagnosis.get("confidence")) == "high":
        return PRIORITY_MEDIUM
    return PRIORITY_LOW


def build_dispatch_proposal(
    diagnosis: dict[str, Any],
    *,
    assignees: list[Any],
    inflight: list[Any],
) -> DispatchProposal:
    """由诊断结论 + 候选接单人生成派单提案（纯函数，可单测）。

    拒绝路径（F-08 验收）：
    - `cannotConclude=true` → `cannot_conclude`，不产提案；
    - 没有任何 `evidenceId` → `diagnosis_without_evidence`（"没依据的派单"比不派更糟）；
    - 没有可用接单人 → `no_assignee`（与补货的「待指派」同语义，不静默选错人）。
    """
    inner_code = str(diagnosis.get("innerCode") or "")
    evidence_ids = [
        str(item.get("evidenceId"))
        for item in (diagnosis.get("evidence") or [])
        if item.get("evidenceId")
    ]
    empty = DispatchProposal(inner_code, None, None, PRIORITY_LOW, "", evidence_ids, {}, False)
    if diagnosis.get("cannotConclude") is True:
        empty.reason = "cannot_conclude"
        return empty
    if not evidence_ids:
        empty.reason = "diagnosis_without_evidence"
        return empty

    assignee = assignees[0] if assignees else None
    basis: dict[str, Any] = {
        "regionId": getattr(assignee, "region_id", None) if assignee is not None else None,
        "candidateCount": len(assignees),
        "deviceInflightCount": len(inflight),
    }
    if assignee is None:
        empty.assignee_basis = basis
        empty.reason = "no_assignee"
        return empty

    kinds = sorted({str(item.get("kind")) for item in (diagnosis.get("evidence") or [])})
    desc = f"智能诊断建议：{inner_code} 检出 {len(evidence_ids)} 条异常证据（{'、'.join(kinds)}）"
    if inflight:
        desc += f"；设备已有 {len(inflight)} 张在途工单，处置前请先核对"
    return DispatchProposal(
        inner_code=inner_code,
        assignee_id=getattr(assignee, "emp_id", None),
        assignee_name=getattr(assignee, "user_name", None),
        priority=_proposal_priority(diagnosis),
        desc=desc,
        based_on_evidence=evidence_ids,
        assignee_basis=basis,
        dispatchable=True,
    )


def validate_proposal_refs(proposal: DispatchProposal, diagnosis: dict[str, Any]) -> str | None:
    """校验提案引用（F-08 的"引用越界拒绝"）。

    @return None 表示合法；否则返回拒绝原因（调用方写进 step.error_msg）
    """
    allowed = {
        str(item.get("evidenceId"))
        for item in (diagnosis.get("evidence") or [])
        if item.get("evidenceId")
    }
    if not proposal.based_on_evidence:
        return "evidence_ref_missing"
    if any(ref not in allowed for ref in proposal.based_on_evidence):
        return "evidence_ref_out_of_range"
    if proposal.assignee_id is not None and proposal.assignee_basis.get("regionId") is None:
        return "assignee_basis_missing"
    return None


def _dispatch_summary(proposal: DispatchProposal) -> str:
    """派单提案给运营看的一句话（是否可执行、依据几条，不粉饰）。"""
    if proposal.dispatchable:
        return (
            f"派给={proposal.assignee_name}（优先级 {proposal.priority}）；"
            f"依据证据 {len(proposal.based_on_evidence)} 条；待人工确认"
        )
    return f"未生成可执行派单：{proposal.reason}"


async def _fetch_dispatch_facts(
    deps: FaultDeps,
    *,
    run_id: str,
    inner_code: str,
    region_id: int | None,
    step_no: int = 2,
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    """取派单所需事实：同区域可接单人 + 本设备在途工单（都写 tool_call）。"""
    calls: list[dict[str, Any]] = []
    seq = 0

    async def run_tool(
        tool_code: str,
        factory: Callable[[], Any],
        params: dict[str, Any],
        row_count_fn: Callable[[Any], int] | None = None,
    ) -> Any:
        nonlocal seq
        seq += 1
        began = time.perf_counter()
        rows = await factory()
        duration_ms = int((time.perf_counter() - began) * 1000)
        row_count = row_count_fn(rows) if row_count_fn is not None else _row_count(rows)
        await record_tool_call(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=step_no,
            call_seq=seq,
            tool_code=tool_code,
            params=params,
            row_count=row_count,
            duration_ms=duration_ms,
        )
        calls.append(
            {
                "tool_code": tool_code,
                "step_no": step_no,
                "call_seq": seq,
                "row_count": row_count,
                "duration_ms": duration_ms,
            }
        )
        return rows

    grouped: dict[int, list[Any]] = {}
    async with get_sessionmaker()() as session:
        if region_id is not None:
            grouped = await run_tool(
                "list_region_assignees",
                lambda: read_tools.list_region_assignees(session, region_ids=[region_id]),
                {"regionId": region_id},
                row_count_fn=lambda data: len((data or {}).get(region_id, [])),
            )
        # 即使没有接单人也要查在途：它是"这批已经有人在处理"的证据，页面要展示
        inflight = await run_tool(
            "list_inflight_tasks",
            lambda: read_tools.list_inflight_tasks(session, inner_codes=[inner_code]),
            {"innerCodes": [inner_code]},
        )
    assignees = list((grouped or {}).get(region_id, [])) if region_id is not None else []
    return assignees, list(inflight or []), calls


def _make_dispatch_node(deps: FaultDeps) -> Callable[[FaultState], Any]:
    """构造派单官节点（M3/F-08）：只生成提案并落 step，不建单。"""

    async def dispatch(state: FaultState) -> FaultState:
        run_id = state["run_id"]
        inner_code = state["inner_code"]
        diagnosis = state.get("diagnosis") or {}
        region_id = state.get("region_id")
        try:
            assignees, inflight, calls = await _fetch_dispatch_facts(
                deps, run_id=run_id, inner_code=inner_code, region_id=region_id
            )
        except Exception as exc:  # noqa: BLE001 —— 取数失败落 step 并终止，不抛穿图
            logger.warning(
                "派单选人取数失败 run_id=%s err=%s", run_id, type(exc).__name__, exc_info=True
            )
            await record_step(
                deps.settings,
                deps.store,
                run_id=run_id,
                step_no=2,
                role_code=ROLE_DISPATCHER,
                action=ACTION_DISPATCH_PROPOSE,
                summary="选人取数失败",
                status="error",
                error_msg=f"选人取数失败（{type(exc).__name__}）",
            )
            return {"error": "dispatch_collect_failed", "stop_reason": "派单选人取数失败"}

        proposal = build_dispatch_proposal(diagnosis, assignees=assignees, inflight=inflight)
        ref_error = validate_proposal_refs(proposal, diagnosis) if proposal.dispatchable else None
        if ref_error is not None:
            await record_step(
                deps.settings,
                deps.store,
                run_id=run_id,
                step_no=2,
                role_code=ROLE_DISPATCHER,
                action=ACTION_DISPATCH_PROPOSE,
                summary="提案引用校验失败",
                status="error",
                error_msg=f"提案引用校验失败：{ref_error}",
            )
            return {"error": "proposal_invalid", "stop_reason": f"派单提案引用非法：{ref_error}"}

        await record_step(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=2,
            role_code=ROLE_DISPATCHER,
            action=ACTION_DISPATCH_PROPOSE,
            summary=_dispatch_summary(proposal),
            evidence={"proposal": proposal.to_dict(), "toolCalls": calls},
            status="ok" if proposal.dispatchable else "skipped",
        )
        stop_reason = None if proposal.dispatchable else f"未生成可执行派单：{proposal.reason}"
        return {
            "dispatch_proposal": proposal.to_dict(),
            "stop_reason": stop_reason,
        }

    return dispatch


def _diagnose_summary(conclusion: DiagnosisConclusion, trace: EvidenceTrace) -> str:
    """一轮诊断给运营看的一句话（不编造，数字全来自 trace）。"""
    return (
        f"置信度={conclusion.confidence}；证据 {len(trace.kept)} 条"
        f"（丢弃 {len(trace.dropped)} 条）；"
        f"{'可派单' if trace.dispatch_allowed else '证据不足，不派单'}"
    )


def _make_diagnose_node(deps: FaultDeps) -> Callable[[FaultState], Any]:
    """构造诊断官节点（闭包持有依赖，测试可注入假 deps）。"""

    async def diagnose(state: FaultState) -> FaultState:
        run_id = state["run_id"]
        inner_code = state["inner_code"]
        window_hours = state.get("window_hours", DEFAULT_WINDOW_HOURS)
        try:
            facts, calls, error = await collect_facts(
                deps,
                run_id=run_id,
                inner_code=inner_code,
                window_hours=window_hours,
            )
        except Exception as exc:  # noqa: BLE001 —— 取数失败要落 step 并终止，不能抛穿图
            logger.warning(
                "故障诊断取数失败 run_id=%s err=%s", run_id, type(exc).__name__, exc_info=True
            )
            await record_step(
                deps.settings,
                deps.store,
                run_id=run_id,
                step_no=1,
                role_code=ROLE_DIAGNOSTICIAN,
                action=ACTION_DIAGNOSE,
                summary="取数失败",
                status="error",
                error_msg=f"取数失败（{type(exc).__name__}）",
            )
            return {"error": "collect_failed", "stop_reason": "取数失败，未生成诊断"}

        if error == "device_not_found":
            await record_step(
                deps.settings,
                deps.store,
                run_id=run_id,
                step_no=1,
                role_code=ROLE_DIAGNOSTICIAN,
                action=ACTION_DIAGNOSE,
                summary="设备不存在",
                status="error",
                error_msg=f"设备不存在：{inner_code}",
            )
            return {"error": "device_not_found", "stop_reason": f"设备不存在：{inner_code}"}

        conclusion = compose_diagnosis(
            profile=facts["profile"],
            order_anomalies=facts["orders"],
            inventory_anomalies=facts["inventory"],
            history=facts["history"],
            inflight=facts["inflight"],
            stock=facts["stock"],
            window=facts["window"],
        )
        trace = trace_evidence(conclusion, facts=facts, tool_calls=calls)

        payload = {
            "diagnosis": conclusion.model_dump(by_alias=True),
            "trace": [
                {
                    "evidenceId": evidence.evidence_id,
                    "toolCallId": evidence.tool_call_id,
                    "kind": evidence.kind,
                }
                for evidence in trace.kept
            ],
            "dropped": trace.dropped,
        }
        await record_step(
            deps.settings,
            deps.store,
            run_id=run_id,
            step_no=1,
            role_code=ROLE_DIAGNOSTICIAN,
            action=ACTION_DIAGNOSE,
            summary=_diagnose_summary(conclusion, trace),
            evidence=payload,
        )
        return {
            "tool_calls": calls,
            # 区域 id 是派单官的选人键；放 state 而不是让它再查一次设备档案
            "region_id": facts["profile"].region_id,
            "diagnosis": conclusion.model_dump(by_alias=True),
            "evidence_count": len(trace.kept),
            "dropped_count": len(trace.dropped),
            "dispatch_allowed": trace.dispatch_allowed,
            "stop_reason": None
            if trace.dispatch_allowed
            else "证据不足或不可回溯，未生成派单建议",
        }

    return diagnose


def supervisor(state: FaultState) -> str:
    """主管路由（纯函数）：出错→结束；无诊断→诊断官；可派单且无提案→派单官；否则结束。"""
    if state.get("error"):
        return END
    if state.get("diagnosis") is None:
        return ROLE_DIAGNOSTICIAN
    if state.get("dispatch_allowed") and state.get("dispatch_proposal") is None:
        return ROLE_DISPATCHER
    return END


def _pass_through(state: FaultState) -> FaultState:  # noqa: ARG001 —— 节点签名由框架约定
    """空节点：只为让 `supervisor` 能以「条件边」的形式挂在图入口。"""
    return {}


def build_fault_graph(deps: FaultDeps) -> Any:
    """编译故障诊断图（M3：supervisor + 诊断官 + 派单官；M4 再加审核官）。"""
    builder = StateGraph(FaultState)
    builder.add_node("supervisor", _pass_through)
    builder.add_node(ROLE_DIAGNOSTICIAN, _make_diagnose_node(deps))
    builder.add_node(ROLE_DISPATCHER, _make_dispatch_node(deps))
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        supervisor,
        {
            ROLE_DIAGNOSTICIAN: ROLE_DIAGNOSTICIAN,
            ROLE_DISPATCHER: ROLE_DISPATCHER,
            END: END,
        },
    )
    builder.add_edge(ROLE_DIAGNOSTICIAN, "supervisor")
    builder.add_edge(ROLE_DISPATCHER, "supervisor")
    return builder.compile()


async def run_fault_diagnosis(
    deps: FaultDeps,
    *,
    inner_code: str,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    trigger_type: int = 3,
) -> FaultState:
    """跑一次故障诊断（含派单提案）：建 run → 跑图 → 收尾（失败也收尾，页面不会永远转圈）。

    @param trigger_type 1-用户 2-定时任务 3-人工干预（首期由页面按钮触发，固定 3）
    @return 最终 state（含 diagnosis / dispatch_proposal / dispatch_allowed）
    """
    run_id = f"fault-{uuid.uuid4().hex[:12]}"
    await start_run(
        deps.settings,
        deps.store,
        run_id=run_id,
        scene_code="fault",
        subject_type="vm",
        subject_id=inner_code,
        trigger_type=trigger_type,
        create_by="agent",
    )
    graph = build_fault_graph(deps)
    initial: FaultState = {
        "run_id": run_id,
        "inner_code": inner_code,
        "window_hours": window_hours,
        "step_no": 1,
        "diagnosis": None,
        "dispatch_allowed": False,
        "dispatch_proposal": None,
    }
    try:
        result = await graph.ainvoke(initial)
    except Exception:
        await finish_run(
            deps.settings,
            deps.store,
            run_id=run_id,
            status="failed",
            stop_reason="诊断执行异常",
        )
        raise
    proposal = result.get("dispatch_proposal") or {}
    if result.get("error"):
        status = "failed"
    elif proposal.get("dispatchable") and result.get("stop_reason") is None:
        # 提案已生成、等人确认：非终态，finished_at 不写（recorder 语义）
        status = "waiting_confirm"
    else:
        status = "done"
    await finish_run(
        deps.settings,
        deps.store,
        run_id=run_id,
        status=status,
        stop_reason=result.get("stop_reason"),
    )
    return result


__all__ = [
    "DEFAULT_WINDOW_HOURS",
    "DispatchProposal",
    "EvidenceTrace",
    "FaultDeps",
    "build_dispatch_proposal",
    "build_fault_graph",
    "collect_facts",
    "run_fault_diagnosis",
    "supervisor",
    "trace_evidence",
    "validate_proposal_refs",
]
