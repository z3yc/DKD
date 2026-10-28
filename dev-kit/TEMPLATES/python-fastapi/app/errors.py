"""框架适配层：把 devkit 的 DomainError 映射为统一响应包装。

DEPS: devkit.errors, devkit.ids, fastapi

为什么单独放一个文件：CORE/errors.py 刻意不依赖任何 Web 框架，
这样它在非 HTTP 项目（脚本、消费进程）里也能用。框架适配属于模板（L3）职责。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from devkit.errors import DomainError
from devkit.ids import get_request_id

__all__ = ["envelope", "install_exception_handlers"]

logger = logging.getLogger(__name__)

# 错误码：`<SYS><CC><NN>`，SYS 由 init-project 脚本按项目名生成（见 CONTRACTS/error-codes.md）
OK_CODE = "__APP_CODE_OK__"        # 例：DK0000
INTERNAL_CODE = "__APP_CODE_INTERNAL__"  # 例：DK5000


def envelope(data: Any = None, *, code: str = OK_CODE, message: str = "ok") -> dict[str, Any]:
    """统一响应包装（契约见 CONTRACTS/api-envelope.md）。**所有接口都过这一层**。"""
    return {"code": code, "message": message, "data": data, "request_id": get_request_id()}


def install_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理。生产环境不回传堆栈，只回 request_id 供检索。"""

    @app.exception_handler(DomainError)
    async def _handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        # WARN 而非 ERROR：业务预期内的失败不是系统故障，打 ERROR 会淹没告警
        logger.warning("业务异常", extra={"error_code": exc.code, "detail": exc.message})
        return JSONResponse(
            status_code=exc.http_status,
            content=envelope(
                exc.data or None, code=exc.code, message=exc.message
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # 记一次、抛一次，不重复记录（契约 error-codes.md §5）
        logger.exception("未处理异常", extra={"error_code": INTERNAL_CODE})
        # 生产环境绝不回传堆栈与内部细节，只给 request_id 供检索
        return JSONResponse(
            status_code=500,
            content=envelope(None, code=INTERNAL_CODE, message="服务内部错误，请稍后重试"),
        )
