"""骨架冒烟测试：健康检查（任务 0-9 验收标准）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok_and_no_secret_leak():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 负向：健康检查不得回显任何凭据
    dumped = resp.text
    assert "sk-" not in dumped
    assert "password" not in dumped.lower()


def test_response_carries_request_id():
    resp = client.get("/health", headers={"X-Request-Id": "rid-test-001"})
    assert resp.headers["X-Request-Id"] == "rid-test-001"
