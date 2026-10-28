"""冒烟测试：验证应用能起、统一响应包装生效、request_id 贯通。

DEPS: fastapi, httpx

这是模板自带的**最小可用测试**；删掉它之前，请先建立等价的冒烟测试 ——
否则"能不能起来"只能靠人手工点，这是最贵的验证方式。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_unified_envelope() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    # 统一包装的四个字段必须齐全（契约 CONTRACTS/api-envelope.md）
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["data"] == {"status": "ok"}


def test_request_id_is_propagated() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"X-Request-Id": "rid-test-001"})

    # 上游传入的 request_id 必须被信任并回写，否则跨服务串不起来
    assert response.headers["X-Request-Id"] == "rid-test-001"
    assert response.json()["request_id"] == "rid-test-001"
