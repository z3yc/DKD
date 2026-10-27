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


# 测试客户端的来源地址必须显式是**回环**：`/agent/**` 增加了服务间密钥校验
# （app/security.py 的 require_agent_gateway），非回环来源必须带 DKD_AGENT_SERVICE_SECRET。
# Starlette TestClient 默认把 client 固定成 ("testclient", 50000)，那不是回环地址；
# 若在安全层为测试开“非回环放行”的口子，就等于把生产 fail-closed 变成 fail-open
# （DKD_AGENT_ENV 一个拼写错误即可绕过）。所以这里改测试夹具、不改安全策略。
_TEST_CLIENT = ("127.0.0.1", 50000)


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
    with TestClient(app, client=_TEST_CLIENT) as c:
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
    with TestClient(app, client=_TEST_CLIENT) as c:
        yield c
    _clear_caches()


@pytest.fixture
def client_with_llm(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """启用了 LLM 节点的客户端（节点本身由用例打桩，**不得真实调外部 API**，AGENTS §8）。

    为什么要单独一个夹具：`mode`/`model` 字段在 M1 后要能区分 echo 与真 LLM，
    而 echo 夹具（USE_LLM=0）永远走不到 LLM 分支。
    """
    monkeypatch.setenv("DKD_AGENT_SQLITE_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("DKD_AGENT_AUDIT_ENABLED", "0")
    monkeypatch.setenv("DKD_AGENT_USE_LLM", "1")
    # 占位密钥：lifespan 在 USE_LLM=1 且无密钥时会拒绝启动；实际不会发起网络调用（节点已打桩）
    monkeypatch.setenv("DKD_DEEPSEEK_API_KEY", "sk-unit-test-placeholder")
    _clear_caches()
    from app.main import create_app

    app = create_app()
    with TestClient(app, client=_TEST_CLIENT) as c:
        yield c
    _clear_caches()


@pytest.fixture(autouse=True)
def _isolate_env() -> Iterator[None]:
    """防止本机 .env 里的真实配置影响断言（如 USE_LLM/SERVICE_SECRET）。

    这里额外**兜底关闭留痕写库**：单测一旦走到默认留痕写入器就会真的连本机 MySQL。
    本轮（1-7b）实测过这个漏子：未注入假写入器的失败用例仍然向 `agent_decision_log`
    写入了 2 行真数据。要验留痕的用例请显式注入假写入器
    （见 `tests/test_api_restock.py` 的 `FakeAuditWriter`）。
    为什么额外强制 sqlite 后端：本机 `.env` 在验证 PG 后端期间会把
    `DKD_AGENT_CHECKPOINT_BACKEND` 置为 `postgres`，而 pydantic-settings 中
    **已存在的环境变量优先于 `.env`**；不在这里钉死，`uv run pytest` 就会去连真 PG，
    直接违反 AGENTS §8「测试不得连外部依赖」（也会让单测随本机服务状态时好时坏）。
    要验 PG 路径请走 `docs/scripts/verify-checkpoint-pg.py`（显式构造 Settings）。
    """
    saved = {k: os.environ.get(k) for k in list(os.environ) if k.startswith("DKD_")}
    os.environ["DKD_AGENT_AUDIT_ENABLED"] = "0"
    # 2026-10-26（F-02）：运行观测与留痕是两条写入通道，必须一起兜底关闭，
    # 否则新的 run/step/tool_call 写入器会让"跑个单测"写进本机 agent_run 表。
    # 要验观测的用例请显式注入假 store（见 tests/test_run_recorder.py）。
    os.environ["DKD_AGENT_OBS_ENABLED"] = "0"
    os.environ["DKD_AGENT_CHECKPOINT_BACKEND"] = "sqlite"
    yield
    for k in [k for k in os.environ if k.startswith("DKD_") and k not in saved]:
        os.environ.pop(k, None)
    _clear_caches()
