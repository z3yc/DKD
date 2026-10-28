"""应用入口：装配路由与中间件，**不写业务逻辑**。

启动：`uv run python -m app`（需补 `app/__main__.py`）或 `uvicorn app.main:app`。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from devkit.ids import REQUEST_ID_VAR, bind_request_id
from devkit.logging_conf import setup_logging

from .api.health import router as health_router
from .config import Settings
from .errors import install_exception_handlers

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """每个请求绑定/透传 request_id，并在响应头回写。

    优先信任上游传入的 `X-Request-Id`（跨服务串联），没有才新生成。
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        incoming = request.headers.get("X-Request-Id")
        request_id = bind_request_id(incoming)
        token = REQUEST_ID_VAR.set(request_id)
        try:
            response: Response = await call_next(request)
        finally:
            # 必须 reset：否则长生命周期进程会串号到下一个请求，且极难发现
            REQUEST_ID_VAR.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    # 日志必须在任何业务逻辑之前初始化，否则启动期日志会丢失
    setup_logging(
        service=settings.service_name,
        env=settings.env,
        level=settings.log_level,
        fmt="console" if settings.env == "local" else "json",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("服务启动", extra={"env": settings.env})
        yield
        logger.info("服务停止")

    app = FastAPI(title=settings.service_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    install_exception_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
