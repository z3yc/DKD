"""LLM 接入单测（任务 0-3）——全部使用假实现，不调用外部 API（AGENTS §8）。"""

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.llm import LlmError, build_chat_model, ping_llm


def _settings(**overrides: object) -> Settings:
    """构造隔离配置：不读 .env、不读同名环境变量。"""
    base: dict[str, object] = {
        "DKD_DEEPSEEK_API_KEY": "sk-unit-test-fake-key",
        "DKD_AGENT_LLM_MODEL": "deepseek-chat",
        "DKD_AGENT_LLM_BASE_URL": "https://api.deepseek.com",
        "DKD_AGENT_LLM_TIMEOUT": 12.5,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class _FakeModel:
    """最小假客户端：只实现 ping_llm 用到的 ainvoke。"""

    def __init__(self, message: AIMessage) -> None:
        self._message = message
        self.calls: list[object] = []

    async def ainvoke(self, messages: object) -> AIMessage:
        self.calls.append(messages)
        return self._message


class _BoomModel:
    async def ainvoke(self, messages: object) -> AIMessage:
        raise TimeoutError("simulated upstream timeout")


def test_build_requires_api_key():
    with pytest.raises(LlmError, match="DKD_DEEPSEEK_API_KEY"):
        build_chat_model(_settings(**{"DKD_DEEPSEEK_API_KEY": ""}))


def test_build_maps_settings_and_hides_key():
    client = build_chat_model(_settings())
    assert client.model_name == "deepseek-chat"
    assert client.openai_api_base == "https://api.deepseek.com"
    assert client.request_timeout == 12.5
    assert client.max_retries == 2
    # 负向：凭据不得出现在日志/异常/repr 可见处（AGENTS §6.1）
    assert "sk-unit-test-fake-key" not in repr(client)


async def test_ping_returns_structured_result_with_token_usage():
    fake = _FakeModel(
        AIMessage(
            content="连通",
            usage_metadata={"input_tokens": 9, "output_tokens": 1, "total_tokens": 10},
            response_metadata={"model_name": "deepseek-flash"},
        )
    )
    result = await ping_llm(model=fake, settings=_settings())  # type: ignore[arg-type]
    assert result.ok is True
    assert result.content == "连通"
    assert result.input_tokens == 9
    assert result.output_tokens == 1
    assert result.latency_ms >= 0
    assert len(fake.calls) == 1


async def test_ping_reports_actual_served_model_not_configured_one():
    """实测：配置 deepseek-chat 时服务端可能返回 deepseek-flash，须按实际值计量。"""
    fake = _FakeModel(AIMessage(content="ok", response_metadata={"model_name": "deepseek-flash"}))
    result = await ping_llm(model=fake, settings=_settings())  # type: ignore[arg-type]
    assert result.model == "deepseek-flash"
    assert result.configured_model == "deepseek-chat"


async def test_ping_marks_empty_content_as_not_ok():
    fake = _FakeModel(AIMessage(content="   "))
    result = await ping_llm(model=fake, settings=_settings())  # type: ignore[arg-type]
    assert result.ok is False


async def test_ping_wraps_upstream_failure_into_llm_error():
    with pytest.raises(LlmError, match="TimeoutError"):
        await ping_llm(model=_BoomModel(), settings=_settings())  # type: ignore[arg-type]
