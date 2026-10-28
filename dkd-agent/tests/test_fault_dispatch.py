"""故障场景 M3/F-08：派单官提案（纯函数 + 图路由）单测。

验收口径（活动计划 F-08）：**缺依据拒绝生成**、**引用越界拒绝**。
另覆盖三条业务拒绝路径：
- 不可下结论 → 不产提案；
- 没有可用接单人 → 提案不可执行（不静默选错人）；
- 图层面：有提案时 run 停在 `waiting_confirm`，不可下结论时不起派单步骤。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.config import Settings
from app.graphs import fault_graph as fg
from app.tools.read_tools import Assignee, InflightTask, MachineProfile, OrderAnomaly


class FakeStore:
    """记录调用的假 store（满足 RunStore 协议，不连库）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def insert_run(self, **kwargs: Any) -> None:
        self.calls.append(("insert_run", kwargs))

    async def update_run(self, **kwargs: Any) -> None:
        self.calls.append(("update_run", kwargs))

    async def insert_step(self, **kwargs: Any) -> None:
        self.calls.append(("insert_step", kwargs))

    async def insert_tool_call(self, **kwargs: Any) -> None:
        self.calls.append(("insert_tool_call", kwargs))

    async def interrupt_stale_runs(self, **kwargs: Any) -> int:
        return 0

    def ops(self) -> list[str]:
        return [name for name, _ in self.calls]

    def first(self, name: str) -> dict[str, Any]:
        for op, kwargs in self.calls:
            if op == name:
                return kwargs
        raise AssertionError(f"没有调用 {name}")

    def all(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for op, kwargs in self.calls if op == name]


def make_settings() -> Settings:
    return Settings.model_construct(obs_enabled=True, run_stale_minutes=30)


def diagnosis(
    *,
    cannot: bool = False,
    evidence: list[dict[str, Any]] | None = None,
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "innerCode": "A1000001",
        "cannotConclude": cannot,
        "confidence": confidence,
        "evidence": (
            evidence
            if evidence is not None
            else [
                {
                    "evidenceId": "e1",
                    "kind": "order_anomaly:paid_not_vended",
                    "severity": "high",
                }
            ]
        ),
    }


def assignee(emp_id: int = 7, name: str = "张三", region_id: int = 3) -> Assignee:
    return Assignee(emp_id=emp_id, user_name=name, region_id=region_id)


def test_proposal_carries_evidence_and_assignee_basis() -> None:
    proposal = fg.build_dispatch_proposal(
        diagnosis(),
        assignees=[assignee()],
        inflight=[InflightTask(task_id=31, inner_code="A1000001", task_status=1)],
    )

    assert proposal.dispatchable is True
    assert proposal.assignee_id == 7
    assert proposal.assignee_name == "张三"
    assert proposal.based_on_evidence == ["e1"]
    assert proposal.assignee_basis == {
        "regionId": 3,
        "candidateCount": 1,
        "deviceInflightCount": 1,
    }
    # 高严重度证据 → 优先级 3
    assert proposal.priority == 3
    # 描述必须带设备号与证据条数，且提示在途工单（运营决策依据）
    assert "A1000001" in proposal.desc
    assert "在途工单" in proposal.desc


def test_cannot_conclude_blocks_proposal() -> None:
    proposal = fg.build_dispatch_proposal(
        diagnosis(cannot=True),
        assignees=[assignee()],
        inflight=[],
    )

    assert proposal.dispatchable is False
    assert proposal.reason == "cannot_conclude"
    assert proposal.assignee_id is None


def test_proposal_without_evidence_is_rejected() -> None:
    proposal = fg.build_dispatch_proposal(
        diagnosis(evidence=[]),
        assignees=[assignee()],
        inflight=[],
    )

    assert proposal.dispatchable is False
    assert proposal.reason == "diagnosis_without_evidence"


def test_no_assignee_is_not_dispatchable() -> None:
    proposal = fg.build_dispatch_proposal(diagnosis(), assignees=[], inflight=[])

    assert proposal.dispatchable is False
    assert proposal.reason == "no_assignee"
    assert proposal.assignee_basis["candidateCount"] == 0
    # 不静默选错人：宁可待指派，也不给一个假的 assigneeId
    assert proposal.assignee_id is None


def test_validate_rejects_out_of_range_evidence() -> None:
    proposal = fg.build_dispatch_proposal(diagnosis(), assignees=[assignee()], inflight=[])
    proposal.based_on_evidence = ["e1", "e999"]  # e999 不在本轮诊断证据里

    assert fg.validate_proposal_refs(proposal, diagnosis()) == "evidence_ref_out_of_range"


def test_validate_rejects_missing_evidence() -> None:
    proposal = fg.build_dispatch_proposal(diagnosis(), assignees=[assignee()], inflight=[])
    proposal.based_on_evidence = []

    assert fg.validate_proposal_refs(proposal, diagnosis()) == "evidence_ref_missing"


def test_priority_follows_evidence_strength() -> None:
    medium = diagnosis(
        confidence="high",
        evidence=[
            {
                "evidenceId": "e1",
                "kind": "order_anomaly:paid_not_vended",
                "severity": "medium",
            }
        ],
    )
    low = diagnosis(
        confidence="medium",
        evidence=[
            {
                "evidenceId": "e1",
                "kind": "order_anomaly:paid_not_vended",
                "severity": "info",
            }
        ],
    )

    assert fg.build_dispatch_proposal(medium, assignees=[assignee()], inflight=[]).priority == 2
    assert fg.build_dispatch_proposal(low, assignees=[assignee()], inflight=[]).priority == 1


@pytest.mark.asyncio
async def test_graph_creates_proposal_and_waits_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = {
        "profile": MachineProfile(vm_id=1, inner_code="A1000001", vm_status=1, region_id=3),
        "orders": [
            OrderAnomaly(kind="paid_not_vended", order_id=11, status=1, reason="支付后未出货")
        ],
        "inventory": [],
        "inflight": [],
        "history": None,
        "stock": [],
        "window": {"hours": 24},
    }
    calls = [
        {
            "tool_code": "list_order_anomalies",
            "step_no": 1,
            "call_seq": 1,
            "row_count": 1,
        }
    ]

    async def fake_collect(
        deps: fg.FaultDeps,
        *,
        run_id: str,
        inner_code: str,
        window_hours: int,
        step_no: int = 1,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        return facts, calls, None

    async def fake_dispatch_facts(
        deps: fg.FaultDeps,
        *,
        run_id: str,
        inner_code: str,
        region_id: int | None,
        step_no: int = 2,
    ) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
        return [assignee()], [], []

    monkeypatch.setattr(fg, "collect_facts", fake_collect)
    monkeypatch.setattr(fg, "_fetch_dispatch_facts", fake_dispatch_facts)
    store = FakeStore()
    deps = fg.FaultDeps(
        settings=make_settings(), store=store, now=lambda: datetime(2026, 10, 28, 9, 0, 0)
    )

    state = await fg.run_fault_diagnosis(deps, inner_code="A1000001")

    proposal = state["dispatch_proposal"]
    assert proposal is not None
    assert proposal["dispatchable"] is True
    assert proposal["assigneeId"] == 7
    assert proposal["basedOnEvidence"] == ["e1"]
    # step 1=诊断、step 2=派单提案
    steps = store.all("insert_step")
    assert [item["step_no"] for item in steps] == [1, 2]
    assert steps[1]["role_code"] == "dispatcher"
    # 有可执行提案 → 等人工确认（非终态）
    assert store.first("update_run")["status"] == "waiting_confirm"
    assert store.first("update_run")["finished"] is False


@pytest.mark.asyncio
async def test_graph_skips_dispatch_when_cannot_conclude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = {
        "profile": MachineProfile(vm_id=1, inner_code="A1000001", vm_status=1, region_id=3),
        "orders": [],
        "inventory": [],
        "inflight": [],
        "history": None,
        "stock": [],
        "window": {"hours": 24},
    }

    async def fake_collect(
        deps: fg.FaultDeps,
        *,
        run_id: str,
        inner_code: str,
        window_hours: int,
        step_no: int = 1,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        return facts, [], None

    async def fail_dispatch_facts(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("不可下结论时不应进入派单官")

    monkeypatch.setattr(fg, "collect_facts", fake_collect)
    monkeypatch.setattr(fg, "_fetch_dispatch_facts", fail_dispatch_facts)
    store = FakeStore()
    deps = fg.FaultDeps(settings=make_settings(), store=store)

    state = await fg.run_fault_diagnosis(deps, inner_code="A1000001")

    assert state.get("dispatch_proposal") is None
    assert state["dispatch_allowed"] is False
    # 只写了诊断 step，没有派单 step
    steps = store.all("insert_step")
    assert [item["step_no"] for item in steps] == [1]
    assert store.first("update_run")["status"] == "done"
