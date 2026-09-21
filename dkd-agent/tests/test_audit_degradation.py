"""审计/计量降级不得影响对话可用性（任务 0-12 的关键非功能要求）。

设计取舍：审计是旁路——**基础设施故障降级放行并告警**；
但编码错误（非 SQLAlchemyError）必须暴露，否则会被"吞"成隐性 bug（AGENTS §6.1）。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config import get_settings


def _enable_audit(monkeypatch) -> None:
    """开启审计并清除配置缓存——否则断言的是 app 启动时的旧配置（会假通过）。"""
    monkeypatch.setenv("DKD_AGENT_AUDIT_ENABLED", "1")
    get_settings.cache_clear()


def _events(text: str) -> list[str]:
    return [
        ln[7:]
        for block in text.strip().split("\n\n")
        for ln in block.splitlines()
        if ln.startswith("event: ")
    ]


def _patch_audit_broken(monkeypatch, exc: Exception) -> None:
    """让审计用的 session 工厂抛指定异常（模拟数据库不可用）。"""

    def _boom() -> None:
        raise exc

    monkeypatch.setattr("app.api.chat.get_sessionmaker", _boom)
    _enable_audit(monkeypatch)


def test_chat_survives_audit_db_outage(client: TestClient, monkeypatch):
    """计量库不可用：对话仍应完整返回（meta/delta/done），不可 500 或空响应。"""
    _patch_audit_broken(monkeypatch, OperationalError("select 1", {}, Exception("db down")))
    resp = client.post("/agent/chat", json={"message": "audit down", "conversation_id": "c-boom"})
    assert resp.status_code == 200
    events = _events(resp.text)
    assert events[0] == "meta"
    assert events[-1] == "done"
    assert "error" not in events


def test_chat_survives_audit_write_failure(client: TestClient, monkeypatch):
    """留痕写入阶段故障：同样不得影响对话（两处降级点都要覆盖）。"""

    class _BoomMaker:
        def __call__(self) -> object:
            raise OperationalError("insert", {}, Exception("table missing"))

    monkeypatch.setattr("app.api.chat.get_sessionmaker", lambda: _BoomMaker())
    _enable_audit(monkeypatch)
    resp = client.post("/agent/chat", json={"message": "x", "conversation_id": "c-boom2"})
    assert resp.status_code == 200
    assert _events(resp.text)[-1] == "done"


def test_quota_exceeded_returns_429_frame(client: TestClient, monkeypatch):
    """限额熔断是业务规则（非故障）：必须明确告诉前端，而不是静默放行。"""
    from app.audit import QuotaExceededError

    async def _fake_meter(*_args: object, **_kwargs: object) -> None:
        raise QuotaExceededError("用户当日 token 已达上限")

    monkeypatch.setattr("app.api.chat._meter", _fake_meter)
    _enable_audit(monkeypatch)

    class _Allowed:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr("app.api.chat.get_sessionmaker", lambda: lambda: _Allowed())
    resp = client.post("/agent/chat", json={"message": "hi", "conversation_id": "c-quota"})
    body = resp.text
    assert "event: error" in body
    assert '"code": 429' in body
    assert json.loads(body.split("data: ", 1)[1].split("\n")[0])["code"] == 429
