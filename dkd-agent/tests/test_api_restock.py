"""补货工作台接口与服务层单测（排期任务 1-7）。

分两层测，各测各的：
- **服务层**：用假 store/deps + 真实 `RestockService`，覆盖业务规则（原因必填、重算口径、
  当日恢复、幂等确认、暂停留痕）——这些是验收标准里的硬项；
- **API 层**：用 TestClient + 假服务，覆盖 HTTP 语义（401 无身份 / 400 日期 / 404 计划不存在 /
  409 状态机拒绝 / 统一信封），**不连 MySQL**（AGENTS §8）。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audit import (
    RESULT_FAIL,
    RESULT_OK,
    RESULT_REJECTED,
    TRIGGER_MANUAL,
    Base,
    DecisionLog,
    record_decision,
)
from app.config import Settings
from app.graphs.restock_plan_store import OrderClaimConflictError, PauseState
from app.services.restock_service import (
    AdjustmentRequest,
    RestockService,
    RestockServiceError,
)
from app.tools.read_tools import ChannelDailySales, ChannelStockItem
from app.tools.task_tools import CallbackUnavailableError, CreatedTask

PLAN_DATE = date(2026, 9, 21)
INNER = "A1000001"
VM_ID = 80


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "DKD_AGENT_RESTOCK_CALIBRATION_ENABLED": "0",
        # conftest 会在全局把留痕写库关掉（防止单测误连 MySQL）；本文件的预留痕用例
        # 都注入了假写入器，所以这里显式打开开关，开关的拒绝路径由专门的用例覆盖。
        "DKD_AGENT_AUDIT_ENABLED": "1",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _row(
    *,
    plan_id: int = 5,
    status: int = 1,
    qty: int = 8,
    current: int = 0,
    capacity: int = 10,
    task_id: int | None = None,
) -> dict[str, Any]:
    return {
        "id": plan_id,
        "plan_date": PLAN_DATE,
        "vm_id": VM_ID,
        "inner_code": INNER,
        "region_id": 3,
        "node_id": 1,
        "status": status,
        "assignee_id": 10,
        "assignee_name": "孙权",
        "task_id": task_id,
        "task_code": None,
        "adjust_reason": None,
        "adjusted_by": None,
        "adjusted_time": None,
        "items": [
            {
                "skuId": 1,
                "skuName": "可口可乐 330ml",
                "channelId": 4732,
                "channelCode": "1-1",
                "currentQuantity": current,
                "maxCapacity": capacity,
                "suggestedQuantity": qty,
                "afterRestockQuantity": current + qty,
                "estimatedDays": 2,
                "priority": 3,
                "reason": "基线依据",
            }
        ],
    }


class FakeStore:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else [_row()]
        self.decisions: list[tuple[Any, dict[str, Any]]] = []

    async def list_plans(self, plan_date: date, *, limit: int = 200) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["plan_date"] == plan_date]

    async def save_decision(self, plan: Any, **kwargs: Any) -> None:
        self.decisions.append((plan, kwargs))


class FakeDeps:
    def __init__(
        self,
        *,
        stock: list[ChannelStockItem] | None = None,
        daily: list[ChannelDailySales] | None = None,
        task_error: Exception | None = None,
    ) -> None:
        self.stock = stock if stock is not None else [_stock()]
        self.daily = daily if daily is not None else _daily()
        self.task_error = task_error
        self.load_calls: list[dict[str, Any]] = []
        self.created: list[Any] = []

    async def load(
        self, *, plan_date: date, limit: int, inner_codes: list[str] | None = None
    ) -> tuple[list[ChannelStockItem], list[ChannelDailySales]]:
        self.load_calls.append({"plan_date": plan_date, "inner_codes": inner_codes})
        return self.stock, self.daily

    async def create_task(self, plan: Any, *, request_id: str) -> CreatedTask:
        if self.task_error:
            raise self.task_error
        self.created.append(plan)
        return CreatedTask(task_id=999, task_code="202609210001")

    async def resolve_assignee(self, plan: Any) -> tuple[int | None, str | None]:
        return 10, "孙权"


class FakePauseStore:
    def __init__(self) -> None:
        self.state = PauseState()
        self.calls: list[dict[str, Any]] = []

    async def get(self, *, scope: str = "global") -> PauseState:
        return self.state

    async def set_paused(self, *, paused: bool, by: str, reason: str, scope: str = "global"):
        self.calls.append({"paused": paused, "by": by, "reason": reason})
        self.state = PauseState(
            paused=paused,
            reason=reason,
            paused_by=by if paused else None,
            paused_time=datetime(2026, 9, 21, 7, 0) if paused else None,
            resumed_by=None if paused else by,
            resumed_time=None if paused else datetime(2026, 9, 21, 8, 0),
        )
        return self.state


class FakeAuditWriter:
    """记录留痕调用的假写入器（真实写库的用例见「决策留痕」小节的 SQLite 用例）。

    默认注入到 `_service()`：不加它，服务层就会真的去连 MySQL —— 单测连外部依赖
    是本项目明令禁止的（AGENTS §8），而留痕又是业务路径上的必经调用。
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    async def __call__(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)


def _stock(*, current: int = 0, capacity: int = 10) -> ChannelStockItem:
    return ChannelStockItem(
        vm_id=VM_ID,
        inner_code=INNER,
        channel_id=4732,
        channel_code="1-1",
        inventory_sku_id=1,
        channel_sku_id=1,
        current_stock=current,
        channel_max_capacity=capacity,
        inventory_max_stock=capacity,
    )


def _daily() -> list[ChannelDailySales]:
    return [
        ChannelDailySales(
            inner_code=INNER,
            channel_code="1-1",
            sale_date=PLAN_DATE.replace(day=21 - offset),
            qty=3,
        )
        for offset in range(1, 8)
    ]


def _service(
    *,
    rows: list[dict[str, Any]] | None = None,
    deps: FakeDeps | None = None,
    audit_writer: Any | None = None,
    settings: Settings | None = None,
) -> tuple[RestockService, FakeStore, FakeDeps, FakePauseStore]:
    store = FakeStore(rows)
    real_deps = deps or FakeDeps()
    pause = FakePauseStore()
    service = RestockService(
        store=store,
        deps=real_deps,
        settings=settings or _settings(),
        pause_store=pause,
        # 默认注入假留痕写入器：单测不得连 MySQL（AGENTS §8）
        audit_writer=audit_writer if audit_writer is not None else FakeAuditWriter(),
    )
    return service, store, real_deps, pause


# --------------------------------------------------------------------------------------
# 服务层：读清单
# --------------------------------------------------------------------------------------


async def test_list_plans_returns_stats_and_prototype_fields():
    service, _, _, _ = _service(rows=[_row(), _row(plan_id=6, status=4)])
    data = await service.list_plans(PLAN_DATE)

    assert data["planDate"] == "2026-09-21"
    assert data["stats"]["pending"] == 1, "已建单的不计入待处理"
    assert data["stats"]["totalQuantity"] == 8
    plan = data["plans"][0]
    assert plan["innerCode"] == INNER
    assert plan["items"][0]["reason"] == "基线依据", "原型「依据」折叠面板的数据源"
    assert "adjustReason" in plan and "taskId" in plan


# --------------------------------------------------------------------------------------
# 服务层：调整（数量 / 重算）
# --------------------------------------------------------------------------------------


async def test_adjust_with_quantity_updates_plan_and_audit_fields():
    service, store, _, _ = _service()
    result = await service.adjust(
        5,
        plan_date=PLAN_DATE,
        user_id=7,
        payload=AdjustmentRequest(reason="现场空间受限", quantity=3),
    )

    assert result["status"] == 2, "调整后进入 2-已调整"
    assert result["plan"]["items"][0]["suggestedQuantity"] == 3
    plan, kwargs = store.decisions[-1]
    assert kwargs["adjusted_by"] == 7 and kwargs["adjust_reason"] == "现场空间受限"


async def test_adjust_requires_reason_and_quantity_or_params():
    service, _, _, _ = _service()
    with pytest.raises(RestockServiceError, match="必须填写原因"):
        await service.adjust(
            5, plan_date=PLAN_DATE, user_id=7, payload=AdjustmentRequest(reason=" ")
        )
    with pytest.raises(RestockServiceError, match="必须给出补货数量"):
        await service.adjust(
            5, plan_date=PLAN_DATE, user_id=7, payload=AdjustmentRequest(reason="x")
        )
    with pytest.raises(RestockServiceError, match="只能二选一"):
        await service.adjust(
            5,
            plan_date=PLAN_DATE,
            user_id=7,
            payload=AdjustmentRequest(reason="x", quantity=3, window_days=7),
        )


async def test_adjust_with_window_override_recomputes_from_same_data_source():
    """「高级：修正预测参数」要真的重算，而不是让运营在浏览器里口算。"""
    service, _, deps, _ = _service()
    result = await service.adjust(
        5,
        plan_date=PLAN_DATE,
        user_id=7,
        payload=AdjustmentRequest(reason="最近一周卖得猛", window_days=7),
    )

    assert deps.load_calls and deps.load_calls[0]["inner_codes"] == [INNER], "重算只读该设备"
    item = result["plan"]["items"][0]
    # 近 7 天每天 3 件 → 需求 3.0 → 目标 ceil(3×2×1.2)=8，现库存 0 → 建议 8
    assert item["suggestedQuantity"] == 8
    assert "人工指定参数" in item["reason"] and "预测窗口=7天" in item["reason"]


async def test_adjust_service_level_out_of_range_is_rejected_as_business_error():
    service, _, _, _ = _service()
    with pytest.raises(RestockServiceError, match="服务水平必须落在"):
        await service.adjust(
            5,
            plan_date=PLAN_DATE,
            user_id=7,
            payload=AdjustmentRequest(reason="冒风险", service_level=1.2),
        )
    with pytest.raises(RestockServiceError, match="不支持的预测窗口"):
        await service.adjust(
            5,
            plan_date=PLAN_DATE,
            user_id=7,
            payload=AdjustmentRequest(reason="试试", window_days=90),
        )


async def test_adjust_unknown_plan_is_404():
    service, _, _, _ = _service()
    with pytest.raises(RestockServiceError) as excinfo:
        await service.adjust(
            999, plan_date=PLAN_DATE, user_id=7, payload=AdjustmentRequest(reason="x", quantity=1)
        )
    assert excinfo.value.status_code == 404


# --------------------------------------------------------------------------------------
# 服务层：跳过 / 恢复 / 确认
# --------------------------------------------------------------------------------------


async def test_skip_requires_reason_and_becomes_terminal():
    service, store, _, _ = _service()
    with pytest.raises(RestockServiceError, match="必须填写原因"):
        await service.skip(5, plan_date=PLAN_DATE, user_id=7, reason="  ")

    result = await service.skip(5, plan_date=PLAN_DATE, user_id=7, reason="点位撤机")
    assert result["status"] == 3
    assert store.decisions[-1][0].status == 3


async def test_restore_only_for_today_and_only_skipped():
    service, store, _, _ = _service(rows=[_row(status=3)])
    result = await service.restore(
        5, plan_date=PLAN_DATE, user_id=7, reason="误点", today=PLAN_DATE
    )
    assert result["status"] == 1, "恢复建议 = 回到 1-建议"
    assert store.decisions[-1][0].status == 1

    with pytest.raises(RestockServiceError, match="只能恢复当天"):
        await service.restore(
            5, plan_date=PLAN_DATE, user_id=7, reason="跨日", today=date(2026, 9, 22)
        )
    with pytest.raises(RestockServiceError, match="必须填写原因"):
        await service.restore(5, plan_date=PLAN_DATE, user_id=7, reason=" ", today=PLAN_DATE)

    service2, _, _, _ = _service(rows=[_row(status=1)])
    with pytest.raises(RestockServiceError, match="已跳过"):
        await service2.restore(5, plan_date=PLAN_DATE, user_id=7, reason="x", today=PLAN_DATE)


async def test_confirm_creates_task_and_writes_task_id():
    service, store, deps, _ = _service()
    result = await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert result["status"] == 4 and result["task"]["taskId"] == 999
    assert deps.created, "确认必须真的回调建单"
    plan, kwargs = store.decisions[-1]
    assert kwargs["task_id"] == 999 and plan.status == 4


async def test_confirm_twice_is_idempotent_and_does_not_call_java():
    service, store, deps, _ = _service(rows=[_row(status=4, task_id=567)])
    result = await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert result["result"] == "already_ordered"
    assert "567" in result["note"]
    assert deps.created == [] and store.decisions == [], "重复确认不得再建单、不得再写库"


async def test_confirm_without_assignee_goes_unassigned_not_java():
    rows = [_row()]
    rows[0]["assignee_id"] = None
    rows[0]["assignee_name"] = None
    service, store, deps, _ = _service(rows=rows)
    result = await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert result["status"] == 6
    assert deps.created == []
    assert store.decisions[-1][0].status == 6


async def test_confirm_callback_failure_is_502_and_status_unchanged():
    service, store, _, _ = _service(
        deps=FakeDeps(task_error=CallbackUnavailableError("Java 不可达"))
    )
    with pytest.raises(RestockServiceError) as excinfo:
        await service.confirm(5, plan_date=PLAN_DATE, user_id=7)
    assert excinfo.value.status_code == 502
    assert store.decisions == [], "建单失败不落库、不改状态（交 1-7 的重试建单）"


# --------------------------------------------------------------------------------------
# 并发确认（排期 1-8 验收项 ②/③）
# --------------------------------------------------------------------------------------


async def test_confirm_while_ordering_is_in_progress_is_409():
    """读到 7-建单中（别人正在建单）时必须告知运营，而不是拼一把再建一张工单。"""
    service, store, deps, _ = _service(rows=[_row(status=7)])

    with pytest.raises(RestockServiceError) as excinfo:
        await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert excinfo.value.status_code == 409
    assert "正在建单中" in str(excinfo.value)
    assert deps.created == [] and store.decisions == [], "不得建单、不得改状态"


async def test_claim_conflict_maps_to_409_not_502():
    """认领冲突是 409（这次请求不该执行），不是 502（Java 那边坏了）——两者运维反应完全不同。"""
    service, store, _, _ = _service(
        deps=FakeDeps(
            task_error=OrderClaimConflictError("该计划正在建单中或状态已被变更，请刷新后重试")
        )
    )

    with pytest.raises(RestockServiceError) as excinfo:
        await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert excinfo.value.status_code == 409
    assert "刷新" in str(excinfo.value)
    assert store.decisions == [], "冲突失败不得把计划写成已建单"


class CasDeps(FakeDeps):
    """带 CAS 认领的假依赖：模拟 `SqlRestockDeps.create_task` 的排他语义。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.status = 1
        self._lock = asyncio.Lock()

    async def create_task(self, plan: Any, *, request_id: str) -> CreatedTask:
        async with self._lock:
            if self.status != plan.status:
                raise OrderClaimConflictError(
                    "该计划正在建单中或状态已被变更（可能由另一位运营同时操作），请刷新后重试"
                )
            self.status = 7
        await asyncio.sleep(0.05)  # 撑开并发窗口，让第二个请求真的撞上占位
        return await super().create_task(plan, request_id=request_id)


async def test_two_concurrent_confirms_build_exactly_one_task():
    """验收项 ③：两个运营同时点「确认建单」——只建一张工单，另一个拿到 409。"""
    service, store, deps, _ = _service(deps=CasDeps())

    results = await asyncio.gather(
        service.confirm(5, plan_date=PLAN_DATE, user_id=7),
        service.confirm(5, plan_date=PLAN_DATE, user_id=8),
        return_exceptions=True,
    )

    ok = [r for r in results if isinstance(r, dict)]
    rejected = [r for r in results if isinstance(r, RestockServiceError)]
    assert len(ok) == 1, f"只能有一个成功：{results}"
    assert len(rejected) == 1 and rejected[0].status_code == 409
    assert ok[0]["status"] == 4 and ok[0]["task"]["taskId"] == 999
    assert len(deps.created) == 1, "回调 Java 的次数必须是 1（否则就是重复建单）"
    assert len(store.decisions) == 1, "只回写一次"


# --------------------------------------------------------------------------------------
# 服务层：暂停 / 恢复自动分析
# --------------------------------------------------------------------------------------


async def test_pause_and_resume_require_reason_and_record_actor():
    service, _, _, pause = _service()
    with pytest.raises(RestockServiceError, match="必须填写原因"):
        await service.set_pause(paused=True, user_id=7, reason=" ")

    paused = await service.set_pause(paused=True, user_id=7, reason="盘点一周")
    assert paused.paused is True and paused.paused_by == "7"
    resumed = await service.set_pause(paused=False, user_id=8, reason="盘点完成")
    assert resumed.paused is False and resumed.resumed_by == "8"
    assert [c["paused"] for c in pause.calls] == [True, False]


# --------------------------------------------------------------------------------------
# 服务层：决策留痕（1-7b / FIX-2）—— AGENTS §6.3「每次写操作决策必须留痕」
# 覆盖成功 / 幂等 / 状态机拒绝 / 并发冲突 / 建单失败 / 开关，以及「审计坏掉不能拖垮业务」
# --------------------------------------------------------------------------------------


async def test_skip_writes_decision_log_with_manual_trigger():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(audit_writer=recorder)
    await service.skip(5, plan_date=PLAN_DATE, user_id=7, reason="点位撤机")

    entry = recorder.calls[-1]
    assert entry["action"] == "restock.skip"
    assert entry["scene"] == 2 and entry["trigger_type"] == TRIGGER_MANUAL == 3
    assert entry["target_type"] == "plan" and entry["target_id"] == "5"
    assert entry["user_id"] == 7 and entry["result"] == RESULT_OK
    assert entry["error_msg"] is None
    assert "llm_output" not in entry, "人工干预没有 LLM 输出，不得塞成「模型建议」"
    ctx = entry["input_context"]
    assert ctx["effect"] == "skipped" and ctx["planStatusAfter"] == 3
    assert ctx["planStatusBefore"] == 1 and ctx["reason"] == "点位撤机"
    assert ctx["planDate"] == "2026-09-21" and ctx["vmId"] == VM_ID and ctx["innerCode"] == INNER


async def test_adjust_writes_human_window_into_audit_context():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(audit_writer=recorder)
    await service.adjust(
        5,
        plan_date=PLAN_DATE,
        user_id=7,
        payload=AdjustmentRequest(reason="最近一周卖得猛", window_days=7),
    )

    entry = recorder.calls[-1]
    assert entry["action"] == "restock.adjust" and entry["result"] == RESULT_OK
    ctx = entry["input_context"]
    assert ctx["effect"] == "adjusted" and ctx["reason"] == "最近一周卖得猛"
    assert ctx["windowDays"] == 7 and ctx["requestedQuantity"] is None
    assert ctx["recomputedQuantity"] == 8, "重算结果要进留痕，否则无法复盘这个数是怎么来的"


async def test_confirm_writes_assignee_and_task_into_audit_context():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(audit_writer=recorder)
    await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    entry = recorder.calls[-1]
    assert entry["action"] == "restock.confirm" and entry["result"] == RESULT_OK
    ctx = entry["input_context"]
    assert ctx["effect"] == "created" and ctx["planStatusAfter"] == 4
    assert ctx["taskId"] == 999 and ctx["taskCode"] == "202609210001"
    assert ctx["assigneeId"] == 10 and ctx["assigneeName"] == "孙权", "派给了谁必须可审计（1-8）"


async def test_state_machine_rejection_is_audited_as_rejected():
    recorder = FakeAuditWriter()
    service, store, deps, _ = _service(rows=[_row(status=5)], audit_writer=recorder)
    with pytest.raises(RestockServiceError):
        await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert store.decisions == [] and deps.created == [], "被拒绝不得产生任何写入"
    entry = recorder.calls[-1]
    assert entry["result"] == RESULT_REJECTED == 3
    assert entry["error_msg"], "拒绝原因要原样留痕（运营会问「我点了为什么没生效」）"
    assert entry["input_context"]["effect"] == "rejected"


async def test_task_create_failure_is_audited_as_failed():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(
        deps=FakeDeps(task_error=CallbackUnavailableError("Java 不可达")), audit_writer=recorder
    )
    with pytest.raises(RestockServiceError):
        await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    entry = recorder.calls[-1]
    assert entry["result"] == RESULT_FAIL == 2
    assert entry["input_context"]["effect"] == "create_failed"
    assert "Java 不可达" in entry["error_msg"]


async def test_claim_conflict_is_audited_as_rejected():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(
        deps=FakeDeps(task_error=OrderClaimConflictError("该计划正在建单中，请刷新后重试")),
        audit_writer=recorder,
    )
    with pytest.raises(RestockServiceError):
        await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    entry = recorder.calls[-1]
    assert entry["result"] == RESULT_REJECTED
    assert entry["input_context"]["effect"] == "concurrent_conflict"


async def test_idempotent_confirm_is_audited_and_does_not_write_again():
    recorder = FakeAuditWriter()
    service, store, deps, _ = _service(rows=[_row(status=4, task_id=567)], audit_writer=recorder)
    await service.confirm(5, plan_date=PLAN_DATE, user_id=7)

    assert store.decisions == [] and deps.created == []
    entry = recorder.calls[-1]
    assert entry["result"] == RESULT_OK
    assert entry["input_context"]["effect"] == "already_ordered"


async def test_pause_and_resume_are_audited_with_reason():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(audit_writer=recorder)
    await service.set_pause(paused=True, user_id=7, reason="盘点一周")
    await service.set_pause(paused=False, user_id=8, reason="盘点完成")

    assert [c["action"] for c in recorder.calls] == ["restock.pause", "restock.resume"]
    first = recorder.calls[0]
    assert first["target_type"] == "restock_pause" and first["target_id"] == "global"
    assert first["input_context"]["reason"] == "盘点一周"
    assert [c["user_id"] for c in recorder.calls] == [7, 8]


async def test_audit_failure_does_not_break_the_business_flow():
    """审计是旁路：留痕写不进去（含非 SQLAlchemy 类异常）也必须让运营的点击生效。"""
    recorder = FakeAuditWriter(error=ValueError("留痕参数处理缺陷"))
    service, store, _, _ = _service(audit_writer=recorder)

    result = await service.skip(5, plan_date=PLAN_DATE, user_id=7, reason="点位撤机")

    assert result["status"] == 3 and store.decisions[-1][0].status == 3
    assert recorder.calls == [], "失败调用不应被当成已留痕"


async def test_audit_disabled_does_not_call_writer():
    recorder = FakeAuditWriter()
    service, _, _, _ = _service(
        audit_writer=recorder, settings=_settings(DKD_AGENT_AUDIT_ENABLED="0")
    )
    await service.skip(5, plan_date=PLAN_DATE, user_id=7, reason="点位撤机")
    assert recorder.calls == []


async def test_decision_log_row_is_really_persisted(tmp_path):
    """真 SQL 落库用例：证明留痕不是「只在假写入器里出现过」。

    表结构由 SQLAlchemy 模型生成，与 `docs/ddl/agent_tables.sql::agent_decision_log`
    列一一对应（同 `tests/test_audit.py` 的做法）。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _sqlite_writer(**kwargs: Any) -> None:
        async with maker() as session:
            await record_decision(session, **kwargs)

    service, _, _, _ = _service(
        rows=[_row(plan_id=5), _row(plan_id=6, status=5)], audit_writer=_sqlite_writer
    )
    await service.skip(5, plan_date=PLAN_DATE, user_id=7, reason="点位撤机")
    with pytest.raises(RestockServiceError):
        await service.confirm(6, plan_date=PLAN_DATE, user_id=7)

    async with maker() as session:
        rows = (await session.execute(select(DecisionLog))).scalars().all()
    await engine.dispose()

    assert len(rows) == 2, "两条决策（成功 / 被拒）都要落库"
    by_action = {row.action: row for row in rows}
    ok, rejected = by_action["restock.skip"], by_action["restock.confirm"]
    assert (ok.scene, ok.trigger_type, ok.result) == (2, TRIGGER_MANUAL, RESULT_OK)
    assert ok.del_flag == "0"
    assert ok.target_id == "5" and ok.create_by == "7" and ok.request_id is None
    assert ok.llm_output is None, "人工干预没有 LLM 输出"
    assert rejected.result == RESULT_REJECTED and rejected.error_msg
    assert rejected.input_context["effect"] == "rejected"


# --------------------------------------------------------------------------------------
# API 层：HTTP 语义与鉴权（假服务，不连库）
# --------------------------------------------------------------------------------------


class FakeApiService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_plans(self, plan_date: date) -> dict[str, Any]:
        self.calls.append(("list", {"plan_date": plan_date}))
        return {"planDate": plan_date.isoformat(), "stats": {}, "plans": []}

    async def confirm(self, plan_id: int, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("confirm", {"plan_id": plan_id, **kwargs}))
        return {"planId": plan_id, "status": 4}

    async def adjust(self, plan_id: int, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("adjust", {"plan_id": plan_id, **kwargs}))
        return {"planId": plan_id, "status": 2}

    async def skip(self, plan_id: int, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("skip", {"plan_id": plan_id, **kwargs}))
        return {"planId": plan_id, "status": 3}

    async def restore(self, plan_id: int, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("restore", {"plan_id": plan_id, **kwargs}))
        return {"planId": plan_id, "status": 1}

    async def pause_state(self) -> PauseState:
        return PauseState(paused=True, reason="盘点", paused_by="7")

    async def set_pause(self, *, paused: bool, user_id: int, reason: str) -> PauseState:
        self.calls.append(("pause", {"paused": paused, "user_id": user_id, "reason": reason}))
        return PauseState(paused=paused, reason=reason, paused_by=str(user_id))


class FailingService(FakeApiService):
    async def skip(self, plan_id: int, **kwargs: Any) -> dict[str, Any]:
        raise RestockServiceError("该计划为「已跳过」终态，不可再变更", status_code=409)


@pytest.fixture
def api_client(client: TestClient):
    """复用 conftest 的 TestClient（含 lifespan），把补货服务替换成假实现。"""
    service = FakeApiService()
    client.app.state.restock_service = service
    return client, service


def test_list_plans_ok_without_user_header_and_defaults_to_today(api_client):
    client, service = api_client
    resp = client.get("/agent/restock/plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["data"]["planDate"] == date.today().isoformat()
    assert service.calls[-1][1]["plan_date"] == date.today()


def test_write_without_identity_is_401(api_client):
    client, _ = api_client
    resp = client.post("/agent/restock/plans/5/skip", json={"reason": "撤机"})
    assert resp.status_code == 401
    assert "X-Agent-User" in resp.json()["msg"], "写操作必须经网关携带用户身份"


def test_identity_header_is_forwarded_to_service(api_client):
    client, service = api_client
    resp = client.post(
        "/agent/restock/plans/5/skip",
        json={"reason": "点位撤机"},
        headers={"X-Agent-User": "7", "X-Agent-Username": "zhangsan"},
    )
    assert resp.status_code == 200 and resp.json()["data"]["status"] == 3
    assert service.calls[-1][1]["user_id"] == 7


def test_adjust_accepts_camel_case_params(api_client):
    client, service = api_client
    resp = client.post(
        "/agent/restock/plans/5/adjust",
        json={"reason": "最近卖得猛", "windowDays": 7, "serviceLevel": 0.95},
        headers={"X-Agent-User": "7"},
    )
    assert resp.status_code == 200
    payload = service.calls[-1][1]["payload"]
    assert payload.window_days == 7 and payload.service_level == 0.95


def test_bad_date_is_400_and_missing_reason_is_422(api_client):
    client, _ = api_client
    assert client.get("/agent/restock/plans?plan_date=2026/09/21").status_code == 400
    # reason 为空串：Pydantic min_length 校验直接 422（前端也会拦，但后端不能只靠前端）
    resp = client.post(
        "/agent/restock/plans/5/skip", json={"reason": ""}, headers={"X-Agent-User": "7"}
    )
    assert resp.status_code == 422


def test_business_rejection_maps_to_409_with_message(api_client):
    client, _ = api_client
    client.app.state.restock_service = FailingService()
    resp = client.post(
        "/agent/restock/plans/5/skip", json={"reason": "撤机"}, headers={"X-Agent-User": "7"}
    )
    assert resp.status_code == 409
    assert "终态" in resp.json()["msg"], "拒绝原因必须原样给运营看"


def test_pause_endpoints_require_reason_and_identity(api_client):
    client, service = api_client
    resp = client.post(
        "/agent/restock/pause", json={"reason": "盘点一周"}, headers={"X-Agent-User": "7"}
    )
    assert resp.status_code == 200 and resp.json()["data"]["paused"] is True
    assert service.calls[-1][1] == {"paused": True, "user_id": 7, "reason": "盘点一周"}

    assert client.post("/agent/restock/pause", json={"reason": "x"}).status_code == 401
    state = client.get("/agent/restock/pause").json()["data"]
    assert state["paused"] is True and state["reason"] == "盘点"


def test_service_unavailable_when_not_initialized(client: TestClient):
    client.app.state.restock_service = None
    resp = client.get("/agent/restock/plans")
    assert resp.status_code == 503
