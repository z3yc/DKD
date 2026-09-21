"""运维接口鉴权与响应契约（任务 0-12）。

成本数据属运营内部信息，必须走服务间密钥，不能用用户 JWT（AGENTS §7.5）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_usage_summary_requires_service_secret(client_with_secret: TestClient):
    # 无密钥 → 401
    assert client_with_secret.get("/agents/usage/summary").status_code == 401
    # 伪造密钥 → 401
    assert (
        client_with_secret.get(
            "/agents/usage/summary", headers={"X-Agent-Secret": "forged"}
        ).status_code
        == 401
    )


def test_usage_summary_returns_503_when_secret_unconfigured(client: TestClient):
    """未配置密钥时宁可拒绝服务，也不要默认放行。"""
    assert client.get("/agents/usage/summary").status_code == 503


def test_usage_summary_validates_params(client_with_secret: TestClient):
    headers = {"X-Agent-Secret": "unit-test-secret"}
    assert (
        client_with_secret.get(
            "/agents/usage/summary", params={"days": 0}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client_with_secret.get(
            "/agents/usage/summary", params={"days": 999}, headers=headers
        ).status_code
        == 422
    )
