"""LLM 接入（任务 0-3）。

设计方案（方案 V1.1 §5.1）：`langchain-openai` 以 OpenAI 兼容模式指向 DeepSeek，
复用 Java 侧同一账号（`DKD_DEEPSEEK_API_KEY`），后续可零成本切通义千问。

日志纪律（AGENTS §6.1 / §6.3）：只记 model / 耗时 / token / 状态码，
**禁止记录完整请求体、响应全文与任何凭据**（本模块所有日志均不含 prompt 与 api_key）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings

logger = logging.getLogger("dkd.agent.llm")

DEFAULT_PING_PROMPT = "只回复两个字：连通"


class LlmError(RuntimeError):
    """LLM 调用失败的统一异常（API 层据此映射 502，AGENTS §6.3）。"""


@dataclass(frozen=True, slots=True)
class LlmPingResult:
    """连通性探测结果（结构化，便于单测断言与成本计量）。"""

    ok: bool
    content: str
    model: str | None
    configured_model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


def build_chat_model(
    settings: Settings | None = None,
    *,
    streaming: bool = False,
) -> ChatOpenAI:
    """构造 LLM 客户端。

    - `api_key` 不配置时**立即失败**，避免带着空 key 发出请求后拿到难排查的 401；
    - `max_retries=2`：网络抖动重试，但不做无限重试（防止成本失控）。
    """
    s = settings or get_settings()
    if not s.llm_api_key:
        raise LlmError("DKD_DEEPSEEK_API_KEY 未配置，LLM 不可用（参见 .env.example）")
    return ChatOpenAI(
        model=s.llm_model,
        api_key=s.llm_api_key,  # 内部包装为 SecretStr，repr/日志不会泄漏
        base_url=s.llm_base_url,
        timeout=s.llm_timeout_s,
        max_retries=2,
        streaming=streaming,
    )


def _extract_tokens(message: AIMessage) -> tuple[int, int]:
    """从 usage_metadata 取 token 数（缺失时按 0 计，不抛异常）。"""
    usage = getattr(message, "usage_metadata", None) or {}
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _extract_served_model(message: AIMessage) -> str | None:
    """取服务端实际返回的模型名。

    为什么重要：实测配置 `deepseek-chat` 时，服务端返回的 `model` 可能是
    `deepseek-flash`——成本计量与效果归因必须按**实际模型**记录，不能按配置假设。
    """
    meta = getattr(message, "response_metadata", None) or {}
    served = meta.get("model_name") or meta.get("model")
    return str(served) if served else None


async def ping_llm(
    *,
    model: ChatOpenAI | None = None,
    prompt: str = DEFAULT_PING_PROMPT,
    settings: Settings | None = None,
) -> LlmPingResult:
    """一次最小请求验证连通性（任务 0-3 的验收手段）。

    `model` 参数用于注入假实现（测试不得真实调用外部 API，AGENTS §8）。
    """
    s = settings or get_settings()
    client = model or build_chat_model(s)
    started = time.perf_counter()
    try:
        resp = await client.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:  # 只记类型与摘要，绝不落请求体
        logger.warning("llm ping failed: %s: %s", type(exc).__name__, str(exc)[:200], exc_info=True)
        raise LlmError(f"LLM 调用失败：{type(exc).__name__}") from exc

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    if not isinstance(resp, AIMessage):
        raise LlmError(f"LLM 返回类型异常：{type(resp).__name__}")

    tin, tout = _extract_tokens(resp)
    served = _extract_served_model(resp)
    if served and s.llm_model and served != s.llm_model:
        logger.warning(
            "llm served model differs from config: served=%s config=%s", served, s.llm_model
        )
    logger.info(
        "llm ping ok: model=%s latency_ms=%s tokens_in=%s tokens_out=%s",
        served or s.llm_model,
        latency_ms,
        tin,
        tout,
    )

    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    return LlmPingResult(
        ok=bool(content.strip()),
        content=content,
        model=served,
        configured_model=s.llm_model,
        input_tokens=tin,
        output_tokens=tout,
        latency_ms=latency_ms,
    )
