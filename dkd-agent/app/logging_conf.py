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
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.security import HEADER_REQUEST_ID

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    """供工具层/回调层读取，写入 agent_decision_log.request_id。"""
    return _request_id.get()


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
