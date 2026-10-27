"""`/agent/**` 服务间密钥校验（P0 安全修复：Python 信任边界）。

## 要防的是什么

审计发现：Python 服务在容器里监听 0.0.0.0 时，同一 docker 网络内任何容器都能直连
`/agent/**`，只需伪造一个 `X-Agent-User` 头就能冒充任意后台账号确认补货单、暂停全局分析。
本组用例锁定修复后的三条边界：

1. **回环来源放行**（本机 Java 网关 / 本地开发 / 测试客户端），保证既有部署与 pytest 不被误伤；
2. **非回环来源必须带正确 `X-Agent-Secret`**（常量时间比较），缺失或错误一律 401；
3. **密钥未配置时非回环一律 503**（服务端配置缺失 → fail-closed，绝不“没配就放行”）。

拒绝路径的断言重点不是状态码本身，而是“**没带密钥的请求到不了业务逻辑**”：
因此 401/503 用例都不注入假服务——如果安全依赖被误删，请求会落到真实服务层并因
“补货服务未初始化”返回 503，与“密钥未配置 503”容易混淆；所以断言里同时检查 msg 关键字。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


class _FakeRestockService:
    """最小假服务：只用于验证“回环/合法密钥”请求确实穿过了安全层。"""

    async def list_plans(self, plan_date: object) -> list[object]:
        return []


@contextmanager
def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str,
    client_host: str,
) -> Iterator[TestClient]:
    """按指定来源地址与服务端密钥配置构建测试客户端。

    为什么不用 conftest 的 `client` 夹具：那三个夹具固定为回环来源（默认全放行），
    无法覆盖“外部容器直连”这一攻击路径；这里显式指定 client 元组才能测到拒绝分支。
    """
    monkeypatch.setenv("DKD_AGENT_SQLITE_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("DKD_AGENT_AUDIT_ENABLED", "0")
    monkeypatch.setenv("DKD_AGENT_USE_LLM", "0")
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", secret)
    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    with TestClient(app, client=(client_host, 50123)) as c:
        # 回环/合法密钥用例需要服务实例存在，否则会把“未初始化 503”误当成安全层放行
        c.app.state.restock_service = _FakeRestockService()
        yield c
    get_settings.cache_clear()


@pytest.mark.parametrize("client_host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_loopback_allowed_without_secret(tmp_path, monkeypatch, client_host: str) -> None:
    """回环来源不需要密钥（本机网关与本地直连的既有可用路径）。"""
    with _client(tmp_path, monkeypatch, secret="", client_host=client_host) as client:
        resp = client.get("/agent/restock/plans")

    assert resp.status_code == 200, resp.text


def test_non_loopback_without_secret_returns_503(tmp_path, monkeypatch) -> None:
    """非回环 + 密钥未配置 → 503 fail-closed（服务端配置缺失，不是凭据错误）。"""
    with _client(tmp_path, monkeypatch, secret="", client_host="10.20.30.40") as client:
        resp = client.get("/agent/restock/plans")

    assert resp.status_code == 503
    assert "DKD_AGENT_SERVICE_SECRET" in resp.json()["msg"]


def test_non_loopback_missing_secret_header_returns_401(tmp_path, monkeypatch) -> None:
    """非回环 + 已配置密钥但调用方没带 → 401（审计发现的“任意容器直连”路径）。"""
    with _client(tmp_path, monkeypatch, secret="s3cret", client_host="10.20.30.40") as client:
        resp = client.get("/agent/restock/plans")

    assert resp.status_code == 401
    assert "密钥" in resp.json()["msg"]


def test_non_loopback_wrong_secret_returns_401(tmp_path, monkeypatch) -> None:
    """非回环 + 密钥不匹配 → 401（常量时间比较，错误值不得放行）。"""
    with _client(tmp_path, monkeypatch, secret="s3cret", client_host="10.20.30.40") as client:
        resp = client.get("/agent/restock/plans", headers={"X-Agent-Secret": "s3cret-forged"})

    assert resp.status_code == 401


def test_non_loopback_correct_secret_allowed(tmp_path, monkeypatch) -> None:
    """非回环 + 正确密钥 → 放行（容器部署下 Java 网关与 Python 不同容器时的正常路径）。"""
    with _client(tmp_path, monkeypatch, secret="s3cret", client_host="10.20.30.40") as client:
        resp = client.get(
            "/agent/restock/plans",
            headers={"X-Agent-Secret": "s3cret", "X-Agent-User": "7"},
        )

    assert resp.status_code == 200, resp.text


def test_write_endpoint_also_requires_secret_from_non_loopback(tmp_path, monkeypatch) -> None:
    """写接口（暂停全局分析）同样受密钥保护：审计里最高危的就是这一类写操作。"""
    with _client(tmp_path, monkeypatch, secret="s3cret", client_host="10.20.30.40") as client:
        resp = client.post(
            "/agent/restock/pause",
            json={"reason": "未授权容器尝试暂停"},
            headers={"X-Agent-User": "7"},
        )

    assert resp.status_code == 401
    assert "密钥" in resp.json()["msg"]


def test_chat_is_rejected_before_stream_starts(tmp_path, monkeypatch) -> None:
    """/agent/chat（SSE）也走同一依赖：拒绝必须发生在流开始之前，不能先 200 再断流。

    断言 Content-Type 不是 text/event-stream：若依赖被误挂到生成器内部，
    客户端会先收到 200 SSE 响应头再被中断，Java 网关会把这种半截流当“上游故障”降级，
    真正的“未授权”原因就丢了。
    """
    with _client(tmp_path, monkeypatch, secret="s3cret", client_host="10.20.30.40") as client:
        resp = client.post("/agent/chat", json={"message": "hi", "scene": 1})

    assert resp.status_code == 401
    assert "text/event-stream" not in resp.headers.get("content-type", "")
