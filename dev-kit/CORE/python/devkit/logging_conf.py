"""结构化日志初始化。

DEPS: ids（request_id 上下文）。刻意不 import config —— 参数显式传入，
这样只想要日志模块时可以单独摘走这一个文件。

字段契约见 CONTRACTS/log-fields.md：ts/level/msg/service/env/request_id/logger 必填。
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import Token
from datetime import datetime
from logging import LogRecord
from typing import Any

from .ids import REQUEST_ID_VAR

__all__ = ["JsonFormatter", "RequestIdFilter", "setup_logging", "bind_request_id"]

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """一行一条 JSON。生产环境必须用它 —— 采集与检索都依赖结构化。"""

    def __init__(self, *, service: str, env: str) -> None:
        super().__init__()
        self.service = service
        self.env = env

    def format(self, record: LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "service": self.service,
            "env": self.env,
            "request_id": getattr(record, "request_id", "-"),
            "logger": record.name,
        }
        # extra 字段（通过 logger.info("...", extra={"user_id": 1}) 传入）
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["stack"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class RequestIdFilter(logging.Filter):
    """给每条日志注入当前上下文的 request_id。

    用 Filter 而不是在每个调用点传参：调用点传参一定会漏，漏了就串不起来。
    """

    def filter(self, record: LogRecord) -> bool:
        record.request_id = REQUEST_ID_VAR.get()
        return True


def setup_logging(
    *,
    service: str,
    env: str = "local",
    level: str = "INFO",
    fmt: str = "json",
    third_party_level: str = "WARNING",
) -> None:
    """初始化根 logger。**在应用启动的最早处调用一次**，不要在各模块里各配一遍。

    `fmt="console"` 用于本地开发（可读），字段集合与 JSON 保持一致由 RequestIdFilter 保证。
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())

    if fmt == "json":
        handler.setFormatter(JsonFormatter(service=service, env=env))
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-5s %(name)s [%(request_id)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # 第三方库默认只报 WARNING：uvicorn/httpx 的 INFO 噪声会淹没业务日志
    for noisy in ("uvicorn", "uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(third_party_level)


def bind_request_id(request_id: str | None = None) -> tuple[str, Token[str]]:
    """绑定 request_id 并返回 (值, token)，token 用于请求结束时 reset。

    为什么返回 token：长生命周期的 worker（如消费循环）必须显式 reset，
    否则会复用上一个请求的 request_id，日志串号且极难发现。
    """
    from .ids import bind_request_id as _bind  # 局部导入避免循环
    from .ids import REQUEST_ID_VAR as _var  # noqa: PLC0415

    rid = _bind(request_id)
    return rid, _var
