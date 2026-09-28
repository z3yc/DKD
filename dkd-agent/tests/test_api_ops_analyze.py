"""`POST /agents/restock/analyze` 的 HTTP 契约与鉴权（排期任务 1-9）。

为什么要单独一个文件而不是塞进 `test_ops.py`：这里测的是**定时任务入口**——
它是 `/agents/**`（服务间密钥）而不是 `/agent/restock/**`（用户经网关），
鉴权体系必须逐条钉死，混用就等于把「谁都能触发分析」当特性。

用例覆盖：密钥拒绝路径（401/503）、日期参数（400）、暂停跳过（200 + skipped 不是错误）、
失败映射（502 让 Java 侧可重试）、以及 `X-Request-Id` 是否真的传进了执行器
（全链路 trace 断在入口就再也接不回去）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.restock_analyzer import (
    STATUS_ANALYZED,
    STATUS_SKIPPED,
    AnalyzeOutcome,
    RestockAnalyzeError,
)

SECRET = {"X-Agent-Secret": "unit-test-secret"}


class FakeAnalyzer:
    """记录调用参数的假执行器（真实执行逻辑见 tests/test_restock_analyzer.py）。"""

    def __init__(
        self, *, outcome: AnalyzeOutcome | None = None, error: Exception | None = None
    ) -> None:
        self.outcome = outcome or AnalyzeOutcome(
            status=STATUS_ANALYZED,
            plan_date="2026-09-21",
            thread_id="restock-2026-09-21",
            interrupted=True,
            device_count=3,
            channel_count=5,
            total_quantity=21,
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def analyze(self, *, plan_date: date | None = None, request_id: str | None = None):
        self.calls.append({"plan_date": plan_date, "request_id": request_id})
        if self.error is not None:
            raise self.error
        return self.outcome


@pytest.fixture
def api_client(client_with_secret: TestClient):
    analyzer = FakeAnalyzer()
    client_with_secret.app.state.restock_analyzer = analyzer
    return client_with_secret, analyzer


def test_analyze_requires_service_secret(client_with_secret: TestClient):
    assert client_with_secret.post("/agents/restock/analyze").status_code == 401
    resp = client_with_secret.post("/agents/restock/analyze", headers={"X-Agent-Secret": "forged"})
    assert resp.status_code == 401, "伪造密钥必须 401"


def test_analyze_returns_503_when_secret_unconfigured(client: TestClient):
    """未配置密钥时宁可拒绝服务，也不要默认放行（与 /agents/usage/summary 同口径）。"""
    assert client.post("/agents/restock/analyze").status_code == 503


def test_analyze_returns_camel_case_outcome(api_client):
    client, analyzer = api_client
    resp = client.post("/agents/restock/analyze", json={"planDate": "2026-09-21"}, headers=SECRET)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "ok"
    data = body["data"]
    assert data["status"] == STATUS_ANALYZED and data["planDate"] == "2026-09-21"
    assert data["deviceCount"] == 3 and data["channelCount"] == 5 and data["totalQuantity"] == 21
    assert data["threadId"] == "restock-2026-09-21"
    assert analyzer.calls[-1]["plan_date"] == date(2026, 9, 21)


def test_request_id_header_is_forwarded_to_analyzer(api_client):
    """`X-Request-Id` 必须贯穿到执行器与留痕，否则 Java 侧日志按 rid 检索会断链。"""
    client, analyzer = api_client
    resp = client.post(
        "/agents/restock/analyze",
        json={"planDate": "2026-09-21"},
        headers={**SECRET, "X-Request-Id": "job-20260921-0600"},
    )
    assert resp.status_code == 200
    assert analyzer.calls[-1]["request_id"] == "job-20260921-0600"


def test_analyze_defaults_to_python_side_today(api_client):
    """不传 planDate 时由 Python 用当天（本地脚本/演示方便）；Java 定时任务总是显式传。"""
    client, analyzer = api_client
    assert client.post("/agents/restock/analyze", headers=SECRET).status_code == 200
    assert analyzer.calls[-1]["plan_date"] is None


def test_bad_plan_date_is_400(api_client):
    client, _ = api_client
    resp = client.post("/agents/restock/analyze", json={"planDate": "2026/09/21"}, headers=SECRET)
    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.json()["msg"]


def test_paused_run_is_200_with_skipped_status(client_with_secret: TestClient):
    """被暂停**不是错误**：Java 定时任务据此判定成功，不触发重试与告警。"""
    client_with_secret.app.state.restock_analyzer = FakeAnalyzer(
        outcome=AnalyzeOutcome(
            status=STATUS_SKIPPED,
            plan_date="2026-09-21",
            thread_id="restock-2026-09-21",
            reason="盘点一周",
            paused_by="7",
        )
    )
    resp = client_with_secret.post(
        "/agents/restock/analyze", json={"planDate": "2026-09-21"}, headers=SECRET
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "skipped" and data["reason"] == "盘点一周" and data["pausedBy"] == "7"


def test_analyzer_failure_maps_to_502_with_message(api_client):
    client, _ = api_client
    client.app.state.restock_analyzer = FakeAnalyzer(
        error=RestockAnalyzeError("补货分析运行失败：TimeoutError: 取数超时")
    )
    resp = client.post("/agents/restock/analyze", headers=SECRET)
    assert resp.status_code == 502
    assert "TimeoutError" in resp.json()["msg"], "失败原因要能出现在 Java 侧的作业日志里"


def test_analyzer_uninitialized_is_503(api_client):
    client, _ = api_client
    client.app.state.restock_analyzer = None
    resp = client.post("/agents/restock/analyze", headers=SECRET)
    assert resp.status_code == 503
    assert "未初始化" in resp.json()["msg"]
