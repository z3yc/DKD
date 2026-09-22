"""建单回调封装单测（排期任务 1-3）。

用 `httpx.MockTransport` 完全替换网络层：**不依赖 Java 服务在跑**，也不产生真实写操作。
重点验证三件事：
1. **字段映射不能反**（`expectCapacity` 必须是补货数量，不是货道容量
   —— 0-13 冻结稿 §4.2 的语义陷阱）；
2. **失败分类要分清**（业务拒绝 / 鉴权失败 / 不可达，上层反应完全不同）；
3. **密钥不外泄**（不在 URL、不在请求体、不进日志）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.graphs.restock_state import PLAN_STATUS_SUGGESTED, RestockItem, RestockPlan
from app.tools.task_tools import (
    CallbackAuthError,
    CallbackBusinessError,
    CallbackUnavailableError,
    CreatedTask,
    create_restock_task,
)

SECRET = "unit-test-service-secret"


@pytest.fixture(autouse=True)
def _secret(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _plan(*, quantity: int = 8, capacity: int = 10, current: int = 2) -> RestockPlan:
    item = RestockItem(
        channel_id=4732,
        channel_code="1-1",
        sku_id=1,
        sku_name="可口可乐 330ml",
        current_quantity=current,
        max_capacity=capacity,
        suggested_quantity=quantity,
        after_restock_quantity=current + quantity,
    )
    return RestockPlan(
        plan_date="2026-09-21",
        inner_code="A1000001",
        vm_id=80,
        region_id=3,
        assignee_id=2,
        status=PLAN_STATUS_SUGGESTED,
        items=[item],
    )


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def test_payload_maps_quantity_not_capacity_and_hides_secret_from_body():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "msg": "工单创建成功",
                "data": {"taskId": 567, "taskCode": "202609210001"},
            },
        )

    async with _client(handler) as client:
        created = await create_restock_task(_plan(), client=client, request_id="rid-1")

    body = captured["body"]
    assert captured["url"].endswith("/agent/callback/task"), "必须打回调端点，不是 /manage/task"
    assert body["innerCode"] == "A1000001"
    assert body["userId"] == 2, "接单人来自 plan.assignee_id"
    assert body["productTypeId"] == 2, "补货工单类型 = 2"
    detail = body["details"][0]
    assert detail["expectCapacity"] == 8, (
        "expectCapacity 必须是**补货数量**（不是容量 10）——坑 9 的语义陷阱"
    )
    assert detail["channelCode"] == "1-1"
    # 密钥只在请求头，绝不能出现在 URL 或请求体里
    assert SECRET in captured["headers"].get("x-agent-secret", "")
    assert SECRET not in captured["url"]
    assert SECRET not in json.dumps(body)
    assert captured["headers"].get("x-request-id") == "rid-1"
    assert created == CreatedTask(task_id=567, task_code="202609210001")


async def test_directly_connected_plan_is_rejected_before_http():
    """没有接单人的计划在本地就应被拒（0-13 冻结稿：缺 assignee 不得建单）。"""
    plan = _plan()
    plan.assignee_id = None
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover —— 不应被调用
        calls["n"] += 1
        return httpx.Response(200, json={"code": 200, "data": {"taskId": 1}})

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="缺少接单人"):
            await create_restock_task(plan, client=client)
    assert calls["n"] == 0


async def test_business_rejection_keeps_java_message_verbatim():
    """业务拒绝必须原样带出 Java 的文案（这是给运营人员看的失败原因）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500, "msg": "设备有未完成工单，请勿重复创建工单"})

    async with _client(handler) as client:
        with pytest.raises(CallbackBusinessError) as excinfo:
            await create_restock_task(_plan(), client=client)

    assert str(excinfo.value) == "设备有未完成工单，请勿重复创建工单"
    assert excinfo.value.code == 500


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, CallbackAuthError),
        (403, CallbackAuthError),
        (503, CallbackUnavailableError),
        (500, CallbackUnavailableError),
    ],
)
async def test_transport_errors_are_classified(status: int, expected: type[Exception]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": status, "msg": "回调密钥未配置，拒绝服务"})

    async with _client(handler) as client:
        with pytest.raises(expected):
            await create_restock_task(_plan(), client=client)


async def test_timeout_and_network_failure_are_unavailable_not_business():
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    async with _client(timeout_handler) as client:
        with pytest.raises(CallbackUnavailableError, match="超时"):
            await create_restock_task(_plan(), client=client)

    def refuse_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _client(refuse_handler) as client:
        with pytest.raises(CallbackUnavailableError, match="不可达"):
            await create_restock_task(_plan(), client=client)


async def test_missing_secret_fails_fast_without_http(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", "")
    get_settings.cache_clear()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover —— 不应被调用
        calls["n"] += 1
        return httpx.Response(200, json={"code": 200, "data": {"taskId": 1}})

    async with _client(handler) as client:
        with pytest.raises(CallbackAuthError, match="SERVICE_SECRET 未配置"):
            await create_restock_task(_plan(), client=client)
    assert calls["n"] == 0


async def test_success_without_task_id_is_treated_as_error():
    """老版本回调不回传 taskId 时必须显式失败，避免上层静默拿到 None 却当成建成。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "msg": "工单创建成功"})

    async with _client(handler) as client:
        with pytest.raises(CallbackUnavailableError, match="未返回 taskId"):
            await create_restock_task(_plan(), client=client)


async def test_logs_never_contain_secret(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": {"taskId": 9, "taskCode": "T9"}})

    with caplog.at_level(logging.DEBUG, logger="dkd.agent.tools.task"):
        async with _client(handler) as client:
            await create_restock_task(_plan(), client=client, request_id="rid-log")

    assert SECRET not in caplog.text, "密钥绝不能进日志（AGENTS §6.1/§7.8）"
    assert "task_id=9" in caplog.text, "成功日志要带结果标识，便于对账"
