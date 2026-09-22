"""补货工作台接口与服务层单测（排期任务 1-7）。

分两层测，各测各的：
- **服务层**：用假 store/deps + 真实 `RestockService`，覆盖业务规则（原因必填、重算口径、
  当日恢复、幂等确认、暂停留痕）——这些是验收标准里的硬项；
- **API 层**：用 TestClient + 假服务，覆盖 HTTP 语义（401 无身份 / 400 日期 / 404 计划不存在 /
  409 状态机拒绝 / 统一信封），**不连 MySQL**（AGENTS §8）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.graphs.restock_plan_store import PauseState
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
    base: dict[str, object] = {"DKD_AGENT_RESTOCK_CALIBRATION_ENABLED": "0"}
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
    *, rows: list[dict[str, Any]] | None = None, deps: FakeDeps | None = None
) -> tuple[RestockService, FakeStore, FakeDeps, FakePauseStore]:
    store = FakeStore(rows)
    real_deps = deps or FakeDeps()
    pause = FakePauseStore()
    service = RestockService(store=store, deps=real_deps, settings=_settings(), pause_store=pause)
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
