"""FastAPI 入口（任务 0-9）。

启动：uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
说明：本服务只监听内网/回环，对外一律经 Java 网关（AGENTS §7.5 鉴权不裸奔）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import chat, ops
from app.checkpoint import get_checkpoint_store, open_checkpoint_store
from app.config import get_settings
from app.db import dispose_engine
from app.graphs.chat_graph import build_chat_graph
from app.logging_conf import RequestContextMiddleware, setup_logging

logger = logging.getLogger("dkd.agent")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201 —— FastAPI lifespan 签名由框架约定
    """启动/关闭钩子：校验关键配置 + 打开 checkpointer + 编译对话图。

    为什么在启动时就编译图：编译失败（版本不兼容/依赖缺失）应当**启动即暴露**，
    而不是等第一个用户请求才炸（AGENTS §6.4 不允许静默失败）。
    """
    s = get_settings()
    if not s.llm_api_key:
        logger.warning("DKD_DEEPSEEK_API_KEY 未配置 —— LLM 场景不可用（echo 模式应仍可用）")
    if s.use_llm and not s.llm_api_key:
        raise RuntimeError("DKD_AGENT_USE_LLM=1 但未配置 LLM 密钥，拒绝以错误配置启动")
    if not s.service_secret:
        logger.warning("DKD_AGENT_SERVICE_SECRET 未配置 —— 回调/运维接口将返回 503")

    store = await open_checkpoint_store(s.sqlite_path)
    app.state.checkpoint_store = store
    app.state.chat_graph = build_chat_graph(checkpointer=store.saver, use_llm=s.use_llm)
    logger.info("dkd-agent started (env=%s, use_llm=%s)", s.app_env, s.use_llm)
    try:
        yield
    finally:
        await get_checkpoint_store().close()
        await dispose_engine()
        logger.info("dkd-agent stopped")


def create_app() -> FastAPI:
    setup_logging()
    s = get_settings()
    app = FastAPI(
        title="DKD Agent Service",
        version="0.1.0",
        description="帝可得智能体服务 —— 读走 MySQL 只读账号，写走 Java REST 回调",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(chat.router)
    app.include_router(ops.router)

    @app.get("/health", tags=["ops"], summary="健康检查（含依赖就绪状态）")
    async def health() -> dict[str, object]:
        """供 Java 网关与 dkd-quartz 探活；不暴露任何凭据。"""
        return {
            "status": "ok",
            "env": s.app_env,
            "use_llm": s.use_llm,
            "llm_configured": bool(s.llm_api_key),
            "service_secret_configured": bool(s.service_secret),
            "db_configured": bool(s.db_password),
            "sqlite_path": str(s.sqlite_path),
            "whitelist_tables": len(s.whitelist_tables),
        }

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """统一错误信封，与 Java 侧约定一致（AGENTS §6.3：不抛穿编排层）。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "msg": str(exc.detail), "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"code": 422, "msg": "参数校验失败", "data": exc.errors()},
        )

    return app


app = create_app()
