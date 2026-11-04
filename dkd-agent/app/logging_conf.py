"""结构化日志与 request_id 贯穿（任务 0-9；AGENTS §6.4）。

request_id 来源优先级：网关注入的 X-Request-Id > 本服务生成。
为什么用 contextvar：不必把 request_id 逐层传参即可让日志、工具层、回调复用。
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar, Token
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.security import HEADER_REQUEST_ID

# 未设置时的哨兵值：写库前统一转 None（编造一个像真的 ID 会让检索永远对不上，
# 见 `app/obs/run_recorder.py` 纪律 3）
UNSET = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=UNSET)

# 执行日志（排期 2-4）的关联上下文。为什么用 contextvar 而不是逐层传参：
# 排障要回答“这条 WARN 属于哪一次运行/会话”，而业务函数签名不该为此增加参数；
# 与 request_id 同一机制，写入方在入口 set、退出 reset，logging.Handler 只读。
# 三者都可空（单测/脚本/定时任务天然没有会话），缺失即写 NULL。
_run_id: ContextVar[str] = ContextVar("run_id", default=UNSET)
_conversation_id: ContextVar[str] = ContextVar("conversation_id", default=UNSET)
_scene: ContextVar[str] = ContextVar("scene", default=UNSET)


def current_request_id() -> str:
    """供工具层/回调层读取，写入 agent_decision_log.request_id。"""
    return _request_id.get()


def current_run_id() -> str:
    """当前运行 ID（agent_run.run_id）；未设置返回哨兵 `-`。"""
    return _run_id.get()


def current_conversation_id() -> str:
    """当前会话 ID（= LangGraph thread_id）；未设置返回哨兵 `-`。"""
    return _conversation_id.get()


def current_scene() -> str:
    """当前场景（chat/restock/fault）；未设置返回哨兵 `-`。"""
    return _scene.get()


def set_run_id(run_id: str | None) -> Token[str]:
    """设置 run_id 上下文，返回 token 供 reset（必须在 finally 里恢复）。

    为什么用 token：一次运行在同一个 asyncio task 内可能嵌套/复用在途协程，
    裸 set 会让“上一个运行”的 ID 泄漏到下一个请求的日志里。
    """
    return _run_id.set(run_id or UNSET)


def reset_run_id(token: Token[str]) -> None:
    """恢复 set_run_id 之前的上下文。"""
    _run_id.reset(token)


def set_conversation_id(conversation_id: str | None) -> Token[str]:
    """设置 conversation_id 上下文，返回 token 供 reset。"""
    return _conversation_id.set(conversation_id or UNSET)


def reset_conversation_id(token: Token[str]) -> None:
    """恢复 set_conversation_id 之前的上下文。"""
    _conversation_id.reset(token)


def set_scene(scene: str | None) -> Token[str]:
    """设置 scene 上下文，返回 token 供 reset。"""
    return _scene.set(scene or UNSET)


def reset_scene(token: Token[str]) -> None:
    """恢复 set_scene 之前的上下文。"""
    _scene.reset(token)


class _JsonFormatter(logging.Formatter):
    """单行 JSON 日志：便于后续接采集，且默认不含请求体（AGENTS §6.1 禁止输出完整 LLM 请求体）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": current_request_id(),
            "msg": record.getMessage(),
        }
        # 执行日志的关联上下文只在设置了的时候出现：默认值 `-` 打出来只会制造噪音
        for key, value in (
            ("run_id", current_run_id()),
            ("conversation_id", current_conversation_id()),
            ("scene", current_scene()),
        ):
            if value != UNSET:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("path", "method", "status", "duration_ms", "scene", "user_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """统一 logging，禁止 print（AGENTS §2.3）。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """注入/透传 request_id，并记录访问日志（含耗时、状态码）。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get(HEADER_REQUEST_ID) or uuid.uuid4().hex
        # 每个请求跑在独立的 asyncio task 中，contextvar 天然隔离，无需 reset
        _request_id.set(rid)
        logger = logging.getLogger("dkd.agent.access")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "path": request.url.path,
                    "method": request.method,
                    "status": 500,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers[HEADER_REQUEST_ID] = rid
        logger.info(
            "request",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
