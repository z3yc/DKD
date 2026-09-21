"""鉴权拒绝路径（AGENTS §8：该拒绝的没拒绝，比该通过的没通过严重得多）。"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import get_settings
from app.security import UserContext, parse_user_context, require_login, verify_service_secret


def _req(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


def test_parse_builds_typed_context():
    ctx = parse_user_context(
        _req(
            {
                "X-Agent-User": "12",
                "X-Agent-Username": "zyc",
                "X-Agent-Roles": "admin,ops",
                "X-Agent-Region": "5",
            }
        )
    )
    assert ctx.user_id == 12
    assert ctx.roles == ("admin", "ops")
    assert ctx.region_id == 5
    assert not ctx.is_system


def test_parse_tolerates_missing_and_garbage():
    ctx = parse_user_context(_req({"X-Agent-User": "abc"}))
    assert ctx.user_id is None  # 非法值不得抛异常，交由 require_login 决定
    assert ctx.is_system


def test_require_login_rejects_system_context():
    with pytest.raises(HTTPException) as e:
        require_login(UserContext(user_id=None, user_name=None, roles=(), region_id=None))
    assert e.value.status_code == 401


def test_service_secret_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_secret", "")
    with pytest.raises(HTTPException) as e:
        verify_service_secret("anything")
    assert e.value.status_code == 503


def test_service_secret_wrong_value_rejected(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_secret", "s3cret")
    with pytest.raises(HTTPException) as e:
        verify_service_secret("wrong")
    assert e.value.status_code == 401


def test_service_secret_correct_value_passes(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_secret", "s3cret")
    assert verify_service_secret("s3cret") is None
