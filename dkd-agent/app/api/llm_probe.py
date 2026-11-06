"""模型档案页的「连通性探测」接口（排期 3-5，Batch B2）。

## 用途

Java 模型档案页保存前/后可调用本接口，用**最小请求**验证「baseUrl + model + 密钥」是否可用，
返回结构化结果（延迟、服务端实际模型、token、错误类型），页面据此给出「可用/不可用 + 原因」。
它替代的是“让人在服务器上手动跑一行 curl”的排障方式。

## 三条安全纪律（AGENTS §6.1 / §7.1）

1. **只接受环境变量名，绝不接受/回显密钥值**：`apiKeyEnv` 必须匹配
   `^[A-Z][A-Z0-9_]{2,63}$`，密钥从 Python 进程的 `os.environ` 读取。
   这样密钥永远不进 HTTP 报文、不进日志、不进留痕；也避免接口退化成
   “任意地址 + 任意密钥”的出网探测工具（密钥只能来自服务端已配置的环境变量）。
2. **日志不落 prompt / 响应正文 / 密钥**：只记 model / latency / errorType / httpStatus。
3. **失败是业务结果，不是 500**：探测本身就是“问能不能用”，网络/鉴权/超时失败一律
   HTTP 200 + `data.ok=false`（页面要能显示原因）；只有**参数形态非法**
   （baseUrl/apiKeyEnv 不合法）才 400。

## 返回字段口径

`ok / latencyMs / modelServed / errorType / errorMsg(≤300) / inputTokens / outputTokens /
contentLen / httpStatus`。`httpStatus` 只在能从异常里取到时非空（取不到 = 未知，不猜）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.security import verify_service_secret

router = APIRouter(
    prefix="/agents/llm",
    tags=["llm"],
    dependencies=[Depends(verify_service_secret)],
)
logger = logging.getLogger("dkd.agent.api.llm_probe")

#: 默认最小请求（与 `app/llm.DEFAULT_PING_PROMPT` 同语义；刻意只要求两个字，控制成本）
DEFAULT_PROMPT = "只回复两个字：连通"

#: 环境变量名白名单（**不是密钥值**）：大写字母开头 + 大写/数字/下划线，总长 3~64。
#: 这个约束同时是安全边界：形如 `sk-xxxx` 的值会被拒绝（它只能放进程环境里）。
API_KEY_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

#: baseUrl 必须是 http/https 且有主机（拒绝 `ftp://`、`file://`、裸 `http://`）
BASE_URL_PATTERN = re.compile(r"^https?://[^\s/]+", re.IGNORECASE)

#: errorMsg 上限（页面展示 + 日志可读性；更长的异常文本截断，不丢类型）
ERROR_MSG_MAX = 300


class LlmPingRequest(BaseModel):
    """探测入参（camelCase 对外，内部 snake_case，与 Java 模型档案页字段同名）。"""

    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field(alias="baseUrl", min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=100)
    # ⚠️ 这里是**环境变量名**，不是密钥值（校验见 `_validate_request`）
    api_key_env: str = Field(alias="apiKeyEnv", min_length=1, max_length=64)
    timeout_s: float = Field(default=60.0, alias="timeoutS", ge=1, le=300)
    max_retries: int = Field(default=2, alias="maxRetries", ge=0, le=5)
    prompt: str | None = Field(default=None, max_length=2000)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    """RuoYi 风格信封（与 `/agents/**` 其它接口一致，Java 侧一套解析）。"""
    return {"code": 200, "msg": "ok", "data": data}


def _payload(
    *,
    ok: bool,
    latency_ms: int,
    model_served: str | None = None,
    error_type: str | None = None,
    error_msg: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    content_len: int = 0,
    http_status: int | None = None,
) -> dict[str, Any]:
    """统一响应体（字段名是 Java/前端契约，改这里必须同步 Java 侧）。"""
    return {
        "ok": ok,
        "latencyMs": latency_ms,
        "modelServed": model_served,
        "errorType": error_type,
        "errorMsg": error_msg,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "contentLen": content_len,
        "httpStatus": http_status,
    }


def _validate_request(payload: LlmPingRequest) -> None:
    """参数形态校验：非法即 400（**不**进入网络调用）。

    为什么不在 Pydantic 层用 `field_validator` 抛 ValueError：`app.main` 的 422 处理器
    把 `exc.errors()` 原样塞进 JSON，而 pydantic 会把 ValueError 实例放进 `ctx`，
    导致“校验失败 → 500 序列化错误”（chat 场景接入时实测踩到）。所以形态校验统一放这里。
    """
    base_url = payload.base_url.strip()
    if not BASE_URL_PATTERN.match(base_url):
        raise HTTPException(status_code=400, detail="baseUrl 必须是 http/https 地址（含主机名）")
    if not API_KEY_ENV_PATTERN.match(payload.api_key_env):
        raise HTTPException(
            status_code=400,
            detail=(
                "apiKeyEnv 必须是环境变量名（^[A-Z][A-Z0-9_]{2,63}$）；"
                "本接口只接受环境变量名，不接受密钥值"
            ),
        )


def _resolve_api_key(env_name: str) -> str | None:
    """按环境变量名取密钥；缺失/空串 → None（调用方映射 MissingApiKey，不抛 500）。

    为什么还要回落到 Settings：本机/容器常用 `.env` 作为唯一凭据来源，
    pydantic-settings 会把 `.env` 读进 Settings，但**不会写回 `os.environ`**。
    若这里只查 `os.environ`，同一份 `.env` 在 Agent 主链路可用、探测接口却报
    MissingApiKey（“看起来像密钥没配”的假故障）。回落只按**字段的 env 别名**精确匹配，
    不接受任意变量名，也不把值写回环境/日志。
    """
    value = os.environ.get(env_name)
    if value is None:
        value = _settings_value_by_env_name(env_name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _settings_value_by_env_name(env_name: str) -> str | None:
    """从 Settings 里找 `validation_alias` 等于该环境变量名的字段值（找不到 → None）。"""
    try:
        settings = get_settings()
        fields = type(settings).model_fields
    except Exception:  # noqa: BLE001 —— 配置系统异常不应把“探测”变成 500
        return None
    for field_name, field in fields.items():
        alias = getattr(field, "validation_alias", None)
        if alias == env_name:
            raw = getattr(settings, field_name, None)
            return str(raw) if raw not in (None, "") else None
    return None


def _http_status_of(exc: BaseException) -> int | None:
    """尽量从异常里取 HTTP 状态码（取不到返回 None，**不猜**）。"""
    for attr in ("status_code", "http_status", "http_code"):
        raw = getattr(exc, attr, None)
        if isinstance(raw, int) and 100 <= raw <= 599:
            return raw
    response = getattr(exc, "response", None)
    raw = getattr(response, "status_code", None)
    if isinstance(raw, int) and 100 <= raw <= 599:
        return raw
    return None


def _classify_error(exc: BaseException) -> str:
    """错误分类（前端按 errorType 给可执行提示）。

    - 鉴权 → `AuthenticationError`（页面提示“密钥无效/环境变量配错”）；
    - 超时 → `Timeout`（页面提示“网络或上游过慢”，与鉴权失败处置完全不同）；
    - 其余 → 异常类型名（保留原始信息，便于 Java/Python 日志对账）。
    """
    name = type(exc).__name__
    status = _http_status_of(exc)
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return "AuthenticationError"
    if isinstance(exc, TimeoutError) or "timeout" in name.lower():
        return "Timeout"
    return name


def _sanitize_error_message(exc: BaseException, *, secret: str) -> str:
    """异常文本 → 可回显的一句话（**去密钥/去换行/≤300 字**）。

    为什么要显式抹掉密钥：上游 SDK 的异常文本可能带上请求头/URL 片段（实测部分网关会回显
    `Authorization` 摘要），而该文本会原样进 HTTP 响应与页面。抹掉是**硬要求**，不是美观问题。
    """
    raw = str(exc)
    if secret:
        raw = raw.replace(secret, "***")
    # 换行会让前端提示框与日志排版错乱；统一压成单空格
    text = " ".join(raw.split())
    if not text:
        text = type(exc).__name__
    return text[:ERROR_MSG_MAX]


def _served_model(response: Any) -> str | None:
    """服务端**实际**返回的模型名（成本/效果归因必须按实际值，不按请求值）。"""
    meta = getattr(response, "response_metadata", None) or {}
    served = meta.get("model_name") or meta.get("model")
    return str(served) if served else None


def _token_usage(response: Any) -> tuple[int, int]:
    """从 usage_metadata 取 token（缺失按 0 计，不抛异常）。"""
    usage = getattr(response, "usage_metadata", None) or {}
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _content_lens(response: Any) -> int:
    """回复正文长度（**只回长度不回正文**：页面只需要“有没有内容”）。"""
    content = getattr(response, "content", "")
    return len(content) if isinstance(content, str) else len(str(content))


def _build_chat_model(
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout_s: float,
    max_retries: int,
) -> ChatOpenAI:
    """构造探测用 LLM 客户端（**测试注入点**：打桩本函数即可完全避免出网）。

    为什么单独成函数：`ChatOpenAI` 的构造参数就是“这个接口到底把哪些配置传给了 SDK”
    的唯一事实源，测试直接断言这些参数比断言行为更不容易漏项。
    """
    return ChatOpenAI(
        model=model,
        api_key=api_key,  # 内部包成 SecretStr，repr/日志不会泄漏
        base_url=base_url,
        timeout=timeout_s,
        max_retries=max_retries,
        streaming=False,
    )


@router.post("/ping", summary="模型连通性探测（最小请求；密钥只按环境变量名解析）")
async def ping_llm_model(payload: LlmPingRequest) -> dict[str, Any]:
    """发一次最小请求验证「baseUrl + model + 环境变量里的密钥」是否可用。

    返回 HTTP 200（信封）+ `data.ok`：鉴权失败/超时/网络错误都是**业务结果**，
    不是服务端故障——若这里抛 5xx，Java 侧会按“Python 服务不可用”重试与告警，方向就错了。
    """
    _validate_request(payload)
    env_name = payload.api_key_env
    api_key = _resolve_api_key(env_name)
    if api_key is None:
        # 200 + ok=false：环境变量没配是**配置问题**，页面应引导去部署环境加变量；
        # 绝不回显任何密钥值（这里连值都没有），也不抛 500（否则 Java 会重试无效请求）。
        return _ok(
            _payload(
                ok=False,
                latency_ms=0,
                error_type="MissingApiKey",
                error_msg=f"环境变量 {env_name} 未配置",
            )
        )

    prompt = payload.prompt or DEFAULT_PROMPT
    started = time.perf_counter()
    try:
        client = _build_chat_model(
            base_url=payload.base_url.strip(),
            model=payload.model,
            api_key=api_key,
            timeout_s=payload.timeout_s,
            max_retries=payload.max_retries,
        )
        response = await client.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:  # noqa: BLE001 —— 探测失败是业务结果，不抛穿（AGENTS §6.3）
        latency_ms = int((time.perf_counter() - started) * 1000)
        error_type = _classify_error(exc)
        http_status = _http_status_of(exc)
        # 日志纪律（AGENTS §6.1）：只记 model/延迟/错误类型/状态码，不记 prompt/响应/密钥
        logger.warning(
            "llm probe failed model=%s latency_ms=%s error_type=%s http_status=%s",
            payload.model,
            latency_ms,
            error_type,
            http_status,
        )
        return _ok(
            _payload(
                ok=False,
                latency_ms=latency_ms,
                error_type=error_type,
                error_msg=_sanitize_error_message(exc, secret=api_key),
                http_status=http_status,
            )
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    input_tokens, output_tokens = _token_usage(response)
    served = _served_model(response)
    logger.info(
        "llm probe ok model=%s served=%s latency_ms=%s tokens_in=%s tokens_out=%s",
        payload.model,
        served,
        latency_ms,
        input_tokens,
        output_tokens,
    )
    return _ok(
        _payload(
            ok=True,
            latency_ms=latency_ms,
            model_served=served,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            content_len=_content_lens(response),
            http_status=200,
        )
    )


__all__ = ["API_KEY_ENV_PATTERN", "DEFAULT_PROMPT", "LlmPingRequest", "router"]
