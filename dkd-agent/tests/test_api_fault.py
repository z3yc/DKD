"""故障诊断 HTTP 接口单测（F-09b）。

只验证**契约与映射**：图执行、确认流程都打桩（不连库、不调 Java），
真正的链路在 `test_fault_confirm.py`（流程）与本轮真库 e2e（已单独执行）里覆盖。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.fault as fault_api
from app.graphs.fault_confirm import ConfirmResult


async def fake_run(deps: Any, *, inner_code: str, window_hours: int) -> dict[str, Any]:
    """假诊断：返回一个已生成提案的 state。"""
    return {
        "run_id": "fault-test0001",
        "inner_code": inner_code,
        "window_hours": window_hours,
        "evidence_count": 3,
        "dropped_count": 1,
        "dispatch_allowed": True,
        "dispatch_proposal": {"dispatchable": True, "assigneeId": 7},
        "stop_reason": None,
    }


async def fake_confirm(deps: Any, **kwargs: Any) -> ConfirmResult:
    """假确认：按预设结局返回。"""
    status = getattr(fake_confirm, "status", "confirmed")
    return ConfirmResult(
        status,
        str(kwargs.get("run_id")),
        task_id=579 if status == "confirmed" else None,
        task_code="202610280001" if status == "confirmed" else None,
        message="ok",
    )


def test_start_run_returns_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def spy(deps: Any, *, inner_code: str, window_hours: int) -> dict[str, Any]:
        calls.append({"inner_code": inner_code, "window_hours": window_hours})
        return await fake_run(deps=deps, inner_code=inner_code, window_hours=window_hours)

    monkeypatch.setattr(fault_api, "run_fault_diagnosis", spy)

    response = client.post("/agent/fault/runs", json={"innerCode": "A1000001"})

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["runId"] == "fault-test0001"
    assert data["status"] == "waiting_confirm"
    assert data["evidenceCount"] == 3
    assert data["proposal"]["dispatchable"] is True
    assert calls == [{"inner_code": "A1000001", "window_hours": 24}]


def test_start_run_failure_maps_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(fault_api, "run_fault_diagnosis", boom)

    response = client.post("/agent/fault/runs", json={"innerCode": "A1000001"})

    assert response.status_code == 502
    assert "诊断执行失败" in response.json()["msg"]


def test_confirm_requires_user_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fault_api, "confirm_fault_dispatch", fake_confirm)

    response = client.post("/agent/fault/runs/fault-test0001/confirm", json={})

    assert response.status_code == 401
    assert "X-Agent-User" in response.json()["msg"]


def test_confirm_passes_user_and_overrides(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def spy(deps: Any, **kwargs: Any) -> ConfirmResult:
        seen.update(kwargs)
        return ConfirmResult("confirmed", kwargs["run_id"], task_id=579, task_code="T1")

    monkeypatch.setattr(fault_api, "confirm_fault_dispatch", spy)

    response = client.post(
        "/agent/fault/runs/fault-test0001/confirm",
        json={"assigneeId": 9, "priority": 3, "remark": "电话确认"},
        headers={"X-Agent-User": "42"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["taskId"] == 579
    assert seen["run_id"] == "fault-test0001"
    assert seen["assignee_id"] == 9
    assert seen["priority"] == 3
    assert seen["remark"] == "电话确认"
    assert seen["user_id"] == 42
    assert seen["operator"] == "user:42"


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [("not_found", 404), ("in_progress", 409), ("not_waiting", 409), ("invalid", 400)],
)
def test_confirm_maps_request_level_failures(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_code: int,
) -> None:
    async def fake(deps: Any, **kwargs: Any) -> ConfirmResult:
        return ConfirmResult(status, str(kwargs.get("run_id")), message=f"{status}-message")

    monkeypatch.setattr(fault_api, "confirm_fault_dispatch", fake)

    response = client.post(
        "/agent/fault/runs/fault-test0001/confirm",
        json={"action": "confirm"},
        headers={"X-Agent-User": "42"},
    )

    assert response.status_code == expected_code
    assert response.json()["msg"] == f"{status}-message"


def test_confirm_reject_passes_action(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake(deps: Any, **kwargs: Any) -> ConfirmResult:
        seen.update(kwargs)
        return ConfirmResult("rejected", str(kwargs.get("run_id")), message="已驳回")

    monkeypatch.setattr(fault_api, "confirm_fault_dispatch", fake)

    response = client.post(
        "/agent/fault/runs/fault-test0001/confirm",
        json={"action": "reject", "remark": "证据不足"},
        headers={"X-Agent-User": "42"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"
    assert seen["action"] == "reject"
    assert seen["remark"] == "证据不足"


def test_confirm_rejects_unknown_action(client: TestClient) -> None:
    response = client.post(
        "/agent/fault/runs/fault-test0001/confirm",
        json={"action": "delete"},
        headers={"X-Agent-User": "42"},
    )

    assert response.status_code == 422
