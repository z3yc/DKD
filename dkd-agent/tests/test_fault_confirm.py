"""F-09 单测：派单确认的幂等 / 驳回 / 业务拒绝回退 / 并发 claim。

不连数据库、不调 Java：`ConfirmStore` 与建单回调都注入假实现（AGENTS §8）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import Settings
from app.graphs.fault_confirm import (
    ConfirmResult,
    confirm_fault_dispatch,
)
from app.graphs.fault_graph import FaultDeps
from app.tools.task_tools import (
    CallbackBusinessError,
    CallbackUnavailableError,
    CreatedTask,
)


class FakeStore:
    """记录 recorder 调用的假 store。"""

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

    def all(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for op, kwargs in self.calls if op == name]


class FakeConfirmStore:
    """内存版确认存储（模拟 agent_run / agent_step 的两条关键读与 CAS）。"""

    def __init__(
        self,
        *,
        status: str = "waiting_confirm",
        proposal: dict[str, Any] | None = None,
        confirmed: dict[str, Any] | None = None,
        claim_result: bool = True,
    ) -> None:
        self.run: dict[str, Any] = {
            "run_id": "r1",
            "status": status,
            "subject_id": "A1000001",
            "stop_reason": None,
        }
        self.proposal = (
            proposal
            if proposal is not None
            else {
                "assigneeId": 7,
                "desc": "智能诊断建议：A1000001 检出 1 条异常证据",
                "priority": 2,
            }
        )
        self.confirmed = confirmed
        self.claim_result = claim_result
        self.claims = 0
        self.released: list[str] = []

    async def load_run(self, run_id: str) -> dict[str, Any] | None:
        return self.run if run_id == "r1" else None

    async def load_proposal(self, run_id: str) -> dict[str, Any] | None:
        return self.proposal

    async def find_confirmed_task(self, run_id: str) -> dict[str, Any] | None:
        return self.confirmed

    async def claim(self, run_id: str) -> bool:
        self.claims += 1
        if not self.claim_result:
            return False
        self.run["status"] = "running"
        return True

    async def release(self, run_id: str, reason: str) -> None:
        self.released.append(reason)
        self.run["status"] = "waiting_confirm"


class FakeCallback:
    """假建单回调（记录入参；可指定返回或抛错）。"""

    def __init__(
        self,
        *,
        result: CreatedTask | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or CreatedTask(task_id=101, task_code="T20261028001")
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        inner_code: str,
        assignee_id: int,
        desc: str,
        request_id: str | None = None,
    ) -> CreatedTask:
        self.calls.append(
            {
                "inner_code": inner_code,
                "assignee_id": assignee_id,
                "desc": desc,
                "request_id": request_id,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


def make_deps() -> tuple[FaultDeps, FakeStore]:
    store = FakeStore()
    deps = FaultDeps(
        settings=Settings.model_construct(obs_enabled=True, audit_enabled=False),
        store=store,
    )
    return deps, store


@pytest.mark.asyncio
async def test_confirm_creates_task_and_finishes() -> None:
    deps, recorder = make_deps()
    confirm_store = FakeConfirmStore()
    callback = FakeCallback()

    result = await confirm_fault_dispatch(
        deps,
        run_id="r1",
        store=confirm_store,
        callback=callback,
    )

    assert isinstance(result, ConfirmResult)
    assert result.status == "confirmed"
    assert result.task_id == 101
    assert result.task_code == "T20261028001"
    # 回调参数：设备号 + 提案接单人 + 提案描述
    assert callback.calls[0]["inner_code"] == "A1000001"
    assert callback.calls[0]["assignee_id"] == 7
    # step3 落库（证据里带 taskId，供幂等回查）
    step = recorder.all("insert_step")[0]
    assert step["step_no"] == 3
    assert step["action"] == "dispatch_confirm"
    assert json.loads(step["evidence_json"])["task"]["taskId"] == 101
    assert recorder.all("update_run")[0]["status"] == "done"


@pytest.mark.asyncio
async def test_duplicate_confirm_returns_same_task_without_callback() -> None:
    deps, _ = make_deps()
    confirm_store = FakeConfirmStore(confirmed={"taskId": 101, "taskCode": "T1"})
    callback = FakeCallback()

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "duplicate"
    assert result.task_id == 101
    assert callback.calls == []


@pytest.mark.asyncio
async def test_running_run_returns_in_progress() -> None:
    deps, _ = make_deps()
    confirm_store = FakeConfirmStore(status="running")
    callback = FakeCallback()

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "in_progress"
    assert callback.calls == []


@pytest.mark.asyncio
async def test_reject_requires_reason_and_does_not_create() -> None:
    deps, recorder = make_deps()
    confirm_store = FakeConfirmStore()
    callback = FakeCallback()

    no_reason = await confirm_fault_dispatch(
        deps, run_id="r1", action="reject", store=confirm_store, callback=callback
    )
    assert no_reason.status == "invalid"

    rejected = await confirm_fault_dispatch(
        deps,
        run_id="r1",
        action="reject",
        remark="证据不足，先观察",
        store=confirm_store,
        callback=callback,
    )

    assert rejected.status == "rejected"
    assert callback.calls == []
    step = recorder.all("insert_step")[0]
    assert step["status"] == "skipped"
    assert recorder.all("update_run")[0]["status"] == "done"
    assert "人工驳回" in recorder.all("update_run")[0]["stop_reason"]


@pytest.mark.asyncio
async def test_business_rejection_releases_for_retry() -> None:
    deps, recorder = make_deps()
    confirm_store = FakeConfirmStore()
    callback = FakeCallback(error=CallbackBusinessError("设备有未完成工单，请勿重复创建工单"))

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "rejected_by_business"
    assert "设备有未完成工单" in result.message
    # 回退到 waiting_confirm（可改派/处理后重试），且记录失败步骤
    assert confirm_store.run["status"] == "waiting_confirm"
    assert confirm_store.released
    step = recorder.all("insert_step")[0]
    assert step["status"] == "error"
    assert "设备有未完成工单" in step["error_msg"]
    # 不能在业务拒绝时把运行写成 done
    assert recorder.all("update_run") == []


@pytest.mark.asyncio
async def test_unavailable_releases_for_retry() -> None:
    deps, _ = make_deps()
    confirm_store = FakeConfirmStore()
    callback = FakeCallback(error=CallbackUnavailableError("Java 服务不可达，建单未完成"))

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "unavailable"
    assert confirm_store.run["status"] == "waiting_confirm"
    assert confirm_store.released


@pytest.mark.asyncio
async def test_claim_conflict_returns_in_progress() -> None:
    deps, recorder = make_deps()
    confirm_store = FakeConfirmStore(claim_result=False)
    callback = FakeCallback()

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "in_progress"
    assert callback.calls == []
    assert recorder.all("insert_step") == []


@pytest.mark.asyncio
async def test_no_assignee_is_not_confirmed() -> None:
    deps, _ = make_deps()
    confirm_store = FakeConfirmStore(
        proposal={"assigneeId": None, "desc": "诊断建议", "priority": 1}
    )
    callback = FakeCallback()

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "rejected_by_business"
    assert "无接单人" in result.message
    assert callback.calls == []
    # 未 claim：不应改变 run 状态
    assert confirm_store.claims == 0


@pytest.mark.asyncio
async def test_not_waiting_status_is_rejected() -> None:
    deps, _ = make_deps()
    confirm_store = FakeConfirmStore(status="done")
    callback = FakeCallback()

    result = await confirm_fault_dispatch(
        deps, run_id="r1", store=confirm_store, callback=callback
    )

    assert result.status == "not_waiting"
    assert callback.calls == []


@pytest.mark.asyncio
async def test_confirm_override_assignee_and_remark() -> None:
    deps, _ = make_deps()
    confirm_store = FakeConfirmStore()
    callback = FakeCallback()

    await confirm_fault_dispatch(
        deps,
        run_id="r1",
        assignee_id=9,
        remark="已电话确认",
        store=confirm_store,
        callback=callback,
    )

    assert callback.calls[0]["assignee_id"] == 9
    assert "已电话确认" in callback.calls[0]["desc"]
