"""测试夹具（conftest）。

原则（AGENTS §8）：
- 测试**不得**连接外部依赖：SQLite checkpoint 用 tmp_path 隔离，
  MySQL 留痕写入默认关闭（需要真 SQL 的用例在 test_audit.py 里用内存/文件 SQLite 自行建表）；
- 配置缓存必须显式清理，否则用例之间会串（Settings 用了 lru_cache）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


def _clear_caches() -> None:
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """带完整 lifespan 的测试客户端（checkpointer 落在 tmp_path，禁用 MySQL 留痕）。"""
    monkeypatch.setenv("DKD_AGENT_SQLITE_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("DKD_AGENT_AUDIT_ENABLED", "0")
    monkeypatch.setenv("DKD_AGENT_USE_LLM", "0")
    # 用空串覆盖仓库 .env 中的真实密钥：pydantic-settings 中已设置的环境变量优先于 .env
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", "")
    _clear_caches()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    _clear_caches()


@pytest.fixture
def client_with_secret(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """启用了服务间密钥的客户端（用于运维接口鉴权用例）。"""
    monkeypatch.setenv("DKD_AGENT_SQLITE_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("DKD_AGENT_AUDIT_ENABLED", "0")
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", "unit-test-secret")
    _clear_caches()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    _clear_caches()


@pytest.fixture(autouse=True)
def _isolate_env() -> Iterator[None]:
    """防止本机 .env 里的真实配置影响断言（如 USE_LLM/SERVICE_SECRET）。"""
    saved = {k: os.environ.get(k) for k in list(os.environ) if k.startswith("DKD_")}
    yield
    for k in [k for k in os.environ if k.startswith("DKD_") and k not in saved]:
        os.environ.pop(k, None)
    _clear_caches()
