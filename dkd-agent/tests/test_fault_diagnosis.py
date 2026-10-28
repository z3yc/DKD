"""故障诊断 M2 单测：证据回溯规则（F-07）+ 图运行生命周期（F-06）。

覆盖 AGENTS §8 的"拒绝路径优先"：
- 证据指向不存在的行 → 丢弃；
- 工具没被调用 → 丢弃（不能拿"没查过"当证据）；
- 工具记录的 row_count 与事实条数不一致 → 丢弃（防事实被改写）；
- 证据全丢 → cannotConclude=true、置信度 none、**不得派单**；
- 设备不存在 → 运行收尾为 failed，页面不会永远转圈。

不连数据库、不调 LLM：`collect_facts` 被替换为假实现，`store` 用假实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.config import Settings
from app.graphs import fault_graph as fg
from app.services.diagnosis import (
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    DiagnosisConclusion,
    DiagnosisEvidence,
)
from app.tools.read_tools import (
    HistoryCaseSearch,
    InflightTask,
    InventoryAnomaly,
    MachineProfile,
    OrderAnomaly,
)


class FakeStore:
    """记录调用顺序的假 store（同时满足 RunStore 协议）。"""

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


def make_settings() -> Settings:
    return Settings.model_construct(obs_enabled=True, run_stale_minutes=30)


def make_profile() -> MachineProfile:
    return MachineProfile(vm_id=1, inner_code="A1000001", vm_status=1, addr="测试地址")


def make_order(order_id: int = 11) -> OrderAnomaly:
    return OrderAnomaly(
        kind="paid_not_vended",
        order_id=order_id,
        order_no=f"NO{order_id}",
        status=1,
        reason="支付完成超过阀值未出货",
    )


def make_inventory(log_id: int = 21) -> InventoryAnomaly:
    return InventoryAnomaly(
        kind="deduct_without_order",
        log_id=log_id,
        vm_id=1,
        reason="库存扣减未命中订单",
    )


def make_inflight(task_id: int = 31) -> InflightTask:
    return InflightTask(task_id=task_id, inner_code="A1000001", task_status=1)


def make_facts(
    *,
    orders: list[OrderAnomaly] | None = None,
    inventory: list[InventoryAnomaly] | None = None,
    inflight: list[InflightTask] | None = None,
    history: HistoryCaseSearch | None = None,
) -> dict[str, Any]:
    return {
        "profile": make_profile(),
        "orders": orders or [],
        "inventory": inventory or [],
        "inflight": inflight or [],
        "history": history,
        "stock": [],
        "window": {"hours": 24},
    }


def make_calls(
    *, orders_n: int = 0, inv_n: int = 0, inflight_n: int = 0, history_n: int = 0
) -> list[dict[str, Any]]:
    return [
        {"tool_code": "list_order_anomalies", "step_no": 1, "call_seq": 1, "row_count": orders_n},
        {"tool_code": "list_inventory_anomalies", "step_no": 1, "call_seq": 2, "row_count": inv_n},
        {"tool_code": "list_inflight_tasks", "step_no": 1, "call_seq": 3, "row_count": inflight_n},
        {"tool_code": "search_history_cases", "step_no": 1, "call_seq": 4, "row_count": history_n},
    ]


def make_conclusion(
    evidence: list[DiagnosisEvidence], *, cannot: bool = False
) -> DiagnosisConclusion:
    return DiagnosisConclusion(
        inner_code="A1000001",
        evidence=evidence,
        confidence=CONFIDENCE_MEDIUM,
        confidence_basis="1 条订单异常",
        cannot_conclude=cannot,
        recommended_actions=[
            "处理异常",
            "如需现场处置：在「设备管理 → 工单管理」新建维修工单（助手不能代建）",
        ],
    )


def order_evidence(order_id: int = 11) -> DiagnosisEvidence:
    return DiagnosisEvidence(
        kind="order_anomaly:paid_not_vended",
        severity="medium",
        summary="卡单",
        source="tb_order",
        detail={"orderId": order_id},
    )


def test_traceable_evidence_is_kept() -> None:
    conclusion = make_conclusion([order_evidence(11)])
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(orders=[make_order(11)]),
        tool_calls=make_calls(orders_n=1),
    )

    assert len(trace.kept) == 1
    assert trace.kept[0].evidence_id == "e1"
    assert trace.kept[0].tool_call_id == "s1c1"
    assert trace.dropped == []
    assert trace.dispatch_allowed is True


def test_evidence_pointing_to_missing_row_is_dropped() -> None:
    conclusion = make_conclusion([order_evidence(999)])
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(orders=[make_order(11)]),
        tool_calls=make_calls(orders_n=1),
    )

    assert trace.kept == []
    assert trace.dropped == [{"kind": "order_anomaly:paid_not_vended", "reason": "row_not_found"}]


def test_evidence_without_tool_call_is_dropped() -> None:
    conclusion = make_conclusion([order_evidence(11)])
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(orders=[make_order(11)]),
        tool_calls=[],  # 本次运行没查订单
    )

    assert trace.kept == []
    assert trace.dropped[0]["reason"] == "tool_call_missing"


def test_row_count_mismatch_is_dropped() -> None:
    conclusion = make_conclusion([order_evidence(11)])
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(orders=[make_order(11), make_order(12)]),
        # 工具记录只返回 1 行，但事实里有 2 条 → 事实被改写，不可信
        tool_calls=make_calls(orders_n=1),
    )

    assert trace.kept == []
    assert trace.dropped[0]["reason"] == "row_count_mismatch"


def test_all_dropped_blocks_dispatch() -> None:
    conclusion = make_conclusion([order_evidence(999)])
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(orders=[make_order(11)]),
        tool_calls=make_calls(orders_n=1),
    )

    assert trace.cannot_conclude is True
    assert trace.dispatch_allowed is False
    assert conclusion.cannot_conclude is True
    assert conclusion.confidence == CONFIDENCE_NONE
    assert all("新建维修工单" not in action for action in conclusion.recommended_actions)


def test_inflight_task_set_mismatch_is_dropped() -> None:
    evidence = DiagnosisEvidence(
        kind="inflight_work_orders",
        severity="info",
        summary="有在途工单",
        source="tb_task",
        detail={"taskIds": [31, 32]},
    )
    conclusion = make_conclusion([evidence], cannot=True)
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(inflight=[make_inflight(31)]),
        tool_calls=make_calls(inflight_n=1),
    )

    assert trace.kept == []
    assert trace.dropped[0]["reason"] == "row_not_found"


def test_history_total_mismatch_is_dropped() -> None:
    evidence = DiagnosisEvidence(
        kind="history_cases",
        severity="info",
        summary="历史 2 条",
        source="tb_task",
        detail={"totalCases": 2},
    )
    conclusion = make_conclusion([evidence], cannot=True)
    trace = fg.trace_evidence(
        conclusion,
        facts=make_facts(history=HistoryCaseSearch(total_cases=3)),
        tool_calls=make_calls(history_n=0),
    )

    assert trace.kept == []
    assert trace.dropped[0]["reason"] == "row_not_found"


@pytest.mark.asyncio
async def test_graph_run_persists_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = make_facts(orders=[make_order(11)])
    calls = make_calls(orders_n=1)

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
        # M3 起诊断后会进入派单官；本用例只验诊断证据落库，故桩成"无可用接单人"
        return [], [], []

    monkeypatch.setattr(fg, "collect_facts", fake_collect)
    monkeypatch.setattr(fg, "_fetch_dispatch_facts", fake_dispatch_facts)
    store = FakeStore()
    deps = fg.FaultDeps(
        settings=make_settings(), store=store, now=lambda: datetime(2026, 10, 27, 9, 0, 0)
    )

    state = await fg.run_fault_diagnosis(deps, inner_code="A1000001")

    assert state["diagnosis"] is not None
    assert state["evidence_count"] == 1
    assert state["dispatch_allowed"] is True
    # 无可派接单人：提案不可执行，运行按 done 收尾并写明原因（不假装已派单）
    assert state["dispatch_proposal"]["dispatchable"] is False
    assert "no_assignee" in state["stop_reason"]
    # 收尾：run 建了、step 写了、run 以 done 结束
    assert store.ops()[0] == "insert_run"
    assert "insert_step" in store.ops()
    assert store.first("update_run")["status"] == "done"
    # step 里带证据回溯结果（前端据此把证据跳到 tool_call）
    step = store.first("insert_step")
    assert step["role_code"] == "diagnostician"
    assert "e1" in step["evidence_json"]
    assert "s1c1" in step["evidence_json"]


@pytest.mark.asyncio
async def test_device_not_found_marks_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_collect(
        deps: fg.FaultDeps,
        *,
        run_id: str,
        inner_code: str,
        window_hours: int,
        step_no: int = 1,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        return {"profile": None}, [], "device_not_found"

    monkeypatch.setattr(fg, "collect_facts", fake_collect)
    store = FakeStore()
    deps = fg.FaultDeps(settings=make_settings(), store=store)

    state = await fg.run_fault_diagnosis(deps, inner_code="NOT-EXIST")

    assert state["error"] == "device_not_found"
    assert store.first("update_run")["status"] == "failed"
    step = store.first("insert_step")
    assert step["status"] == "error"
    assert "NOT-EXIST" in step["error_msg"]
