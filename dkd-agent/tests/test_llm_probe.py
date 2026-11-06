"""`POST /agents/llm/ping` 的契约与安全边界（排期 3-5 / Batch B2）。

## 为什么这些断言值得写

模型档案页的「连通性探测」有三类“看起来能用、其实在泄漏或误导”的坏法，本文件逐条钉死：

1. **把密钥值当参数收下** → 密钥会进 HTTP 报文/日志/前端网络面板；
   本文件断言 `apiKeyEnv` 只接受**环境变量名**，密钥值形态一律 400；
2. **失败时抛 5xx** → Java 会把“密钥配错/上游 401”当成“Python 服务挂了”去重试与告警，
   处置方向完全错；本文件断言失败一律 200 + `data.ok=false` + 可分类的 `errorType`；
3. **异常文本原样回显** → 上游 SDK/网关的异常可能带 `Authorization`/密钥片段；
   本文件断言响应与日志都不含密钥、日志不含 prompt 与响应正文。

全部打桩：`llm_probe._build_chat_model` 被替换为假客户端，**不发起任何网络调用**（AGENTS §8）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.api.llm_probe as llm_probe

SECRET = {"X-Agent-Secret": "unit-test-secret"}
ENV_NAME = "DKD_UNIT_TEST_LLM_KEY"
KEY_VALUE = "sk-unit-test-secret-value"
URL = "https://api.deepseek.com"


def _body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "baseUrl": URL,
        "model": "deepseek-chat",
        "apiKeyEnv": ENV_NAME,
    }
    payload.update(overrides)
    return payload


def _post(client: TestClient, payload: dict[str, Any] | None = None) -> Any:
    return client.post("/agents/llm/ping", json=payload or _body(), headers=SECRET)


def _reply(
    content: str = "连通",
    *,
    model: str = "deepseek-flash",
    tokens: tuple[int, int] = (9, 2),
) -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "total_tokens": sum(tokens),
        },
        response_metadata={"model_name": model},
    )


class _FakeClient:
    """假 LLM 客户端：只实现探测用到的 `ainvoke`。"""

    def __init__(
        self, *, response: AIMessage | None = None, error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def fake_builder(monkeypatch: pytest.MonkeyPatch):
    """把 `_build_chat_model` 换成假客户端，并记录构造参数（= 传给 SDK 的配置）。"""
    captured: dict[str, Any] = {}

    def _install(
        *, response: AIMessage | None = None, error: Exception | None = None
    ) -> tuple[_FakeClient, dict[str, Any]]:
        fake = _FakeClient(response=response, error=error)

        def _factory(**kwargs: Any) -> _FakeClient:
            captured.clear()
            captured.update(kwargs)
            return fake

        monkeypatch.setattr(llm_probe, "_build_chat_model", _factory)
        return fake, captured

    return _install


# --------------------------------------------------------------------------------------
# 鉴权：/agents/** 必须服务间密钥（与其它 ops 接口同口径）
# --------------------------------------------------------------------------------------


def test_ping_requires_service_secret(client_with_secret: TestClient) -> None:
    assert client_with_secret.post("/agents/llm/ping", json=_body()).status_code == 401
    forged = client_with_secret.post(
        "/agents/llm/ping", json=_body(), headers={"X-Agent-Secret": "forged"}
    )
    assert forged.status_code == 401, "伪造密钥必须 401"


def test_ping_503_when_secret_unconfigured(client: TestClient) -> None:
    """未配置服务间密钥时宁可拒绝服务，也不要默认放行。"""
    assert client.post("/agents/llm/ping", json=_body()).status_code == 503


# --------------------------------------------------------------------------------------
# 成功路径：结构化字段 + 客户端参数（= 页面配置真的传给了 SDK）
# --------------------------------------------------------------------------------------


def test_ping_success_returns_structured_fields(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    fake, captured = fake_builder(response=_reply())

    resp = _post(client_with_secret)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "ok"
    data = body["data"]
    assert data["ok"] is True
    assert data["errorType"] is None and data["errorMsg"] is None
    assert data["modelServed"] == "deepseek-flash", "必须回服务端实际模型，而不是请求里的 model"
    assert (data["inputTokens"], data["outputTokens"]) == (9, 2)
    assert data["contentLen"] == 2, "只回长度不回正文（响应体不承载模型输出）"
    assert data["httpStatus"] == 200
    assert isinstance(data["latencyMs"], int) and data["latencyMs"] >= 0

    # 页面配置 → SDK 构造参数：任何一项没传下去，探测结果都不代表真实调用
    assert captured["model"] == "deepseek-chat"
    assert captured["base_url"] == URL
    assert captured["api_key"] == KEY_VALUE
    assert captured["timeout_s"] == 60.0 and captured["max_retries"] == 2
    # 默认最小请求，且密钥绝不出现在响应里
    assert fake.calls and fake.calls[0][0].content == llm_probe.DEFAULT_PROMPT
    assert KEY_VALUE not in resp.text


def test_ping_honours_prompt_and_client_overrides(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    fake, captured = fake_builder(response=_reply())

    resp = _post(
        client_with_secret,
        _body(prompt="只回复：ok", timeoutS=5, maxRetries=0),
    )

    assert resp.status_code == 200
    assert fake.calls[0][0].content == "只回复：ok"
    assert captured["timeout_s"] == 5.0
    assert captured["max_retries"] == 0


# --------------------------------------------------------------------------------------
# MissingApiKey：配置问题 → 200 + ok=false（不抛 500、不构造客户端）
# --------------------------------------------------------------------------------------


def test_missing_env_key_returns_ok_false_without_calling_llm(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_NAME, raising=False)
    _fake, captured = fake_builder()

    resp = _post(client_with_secret)

    assert resp.status_code == 200, "环境变量缺失是配置问题，不是服务端故障"
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["errorType"] == "MissingApiKey"
    assert ENV_NAME in data["errorMsg"], "提示要说清哪个环境变量没配（便于运维直接处理）"
    assert data["latencyMs"] == 0
    assert captured == {}, "密钥缺失时不得构造 LLM 客户端（更不得发出请求）"


def test_blank_env_value_is_treated_as_missing(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空串/纯空白（部署时常见的手滑）不能当“有密钥”用。"""
    monkeypatch.setenv(ENV_NAME, "   ")
    _fake, _captured = fake_builder()

    data = _post(client_with_secret).json()["data"]

    assert data["ok"] is False and data["errorType"] == "MissingApiKey"


# --------------------------------------------------------------------------------------
# 失败映射：鉴权 / 超时 / 其它；不泄漏密钥与请求体
# --------------------------------------------------------------------------------------


class _AuthError(Exception):
    """模拟上游 401（OpenAI SDK 的 APIStatusError 也带 status_code 属性）。"""

    status_code = 401


class _ReadTimeoutError(Exception):
    """模拟 httpx.ReadTimeout（类名含 Timeout —— 上游 SDK 的超时类就是这个命名）。"""


def test_authentication_failure_maps_type_and_http_status(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    # 刻意让异常文本里带密钥（模拟网关回显 Authorization 摘要）：响应与日志都必须被抹掉
    _fake, _captured = fake_builder(
        error=_AuthError(f"401 Unauthorized: invalid api key {KEY_VALUE}")
    )

    with caplog.at_level("WARNING", logger="dkd.agent.api.llm_probe"):
        resp = _post(client_with_secret)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "Authentication" in data["errorType"], "页面据此区分“密钥/权限问题”与“网络问题”"
    assert data["httpStatus"] == 401
    assert data["errorMsg"] and len(data["errorMsg"]) <= llm_probe.ERROR_MSG_MAX
    assert KEY_VALUE not in resp.text, "密钥绝不能随异常文本回显"
    assert KEY_VALUE not in caplog.text, "日志不得含密钥（AGENTS §6.1）"


def test_timeout_maps_to_timeout(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    _fake, _captured = fake_builder(error=_ReadTimeoutError("read timed out"))

    data = _post(client_with_secret).json()["data"]

    assert data["ok"] is False
    assert data["errorType"] == "Timeout", "超时与鉴权失败的处置完全不同，不能都归成异常类型名"
    assert data["httpStatus"] is None, "超时取不到 HTTP 状态码时写 NULL，不猜"


def test_other_failure_uses_exception_type(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    _fake, _captured = fake_builder(error=RuntimeError("boom"))

    data = _post(client_with_secret).json()["data"]

    assert data["ok"] is False
    assert data["errorType"] == "RuntimeError"
    assert data["httpStatus"] is None
    assert "boom" in data["errorMsg"]


def test_error_msg_is_truncated_to_max(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    _fake, _captured = fake_builder(error=RuntimeError("x" * 5000))

    data = _post(client_with_secret).json()["data"]

    assert len(data["errorMsg"]) == llm_probe.ERROR_MSG_MAX


def test_success_logs_do_not_contain_prompt_or_reply(
    client_with_secret: TestClient,
    fake_builder: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """成功日志只记 model/延迟/token：prompt 与响应正文都不进日志。"""
    monkeypatch.setenv(ENV_NAME, KEY_VALUE)
    prompt = "只回复两个字：连通"
    fake_builder(response=_reply("连通"))

    with caplog.at_level("INFO", logger="dkd.agent.api.llm_probe"):
        _post(client_with_secret, _body(prompt=prompt))

    assert "llm probe ok" in caplog.text
    assert prompt not in caplog.text
    assert KEY_VALUE not in caplog.text


# --------------------------------------------------------------------------------------
# 参数形态校验：非法即 400（不进入网络调用）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected_hint"),
    [
        ({"baseUrl": "ftp://api.deepseek.com"}, "baseUrl"),
        ({"baseUrl": "http://"}, "baseUrl"),
        ({"apiKeyEnv": "sk-unit-test-secret-value"}, "apiKeyEnv"),
        ({"apiKeyEnv": "lower_case_name"}, "apiKeyEnv"),
        ({"apiKeyEnv": "AB"}, "apiKeyEnv"),
        ({"model": ""}, None),  # 空 model 由 Pydantic 拦（422），不在本用例断言 400
    ],
)
def test_invalid_payload_is_rejected(
    client_with_secret: TestClient,
    fake_builder: Any,
    overrides: dict[str, Any],
    expected_hint: str | None,
) -> None:
    _fake, captured = fake_builder()

    resp = _post(client_with_secret, _body(**overrides))

    if expected_hint is None:
        assert resp.status_code == 422, "空 model 属于请求体校验（与其它接口同口径）"
        return
    assert resp.status_code == 400
    assert expected_hint in resp.json()["msg"]
    assert captured == {}, "参数非法时绝不能构造客户端/发出请求"


def test_key_value_shaped_api_key_env_never_echoed(
    client_with_secret: TestClient,
) -> None:
    """把密钥值误填进 apiKeyEnv 时：400 且响应里不出现该值（不得回显用户输入的错误密钥）。"""
    resp = _post(client_with_secret, _body(apiKeyEnv=KEY_VALUE))

    assert resp.status_code == 400
    assert KEY_VALUE not in resp.text
