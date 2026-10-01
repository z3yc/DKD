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

from app.api import chat, ops, restock
from app.checkpoint import (
    ensure_windows_event_loop_policy,
    get_checkpoint_store,
    open_checkpoint_store,
)
from app.config import get_settings
from app.db import dispose_engine
from app.graphs.chat_graph import build_chat_graph
from app.llm import build_chat_model
from app.logging_conf import RequestContextMiddleware, setup_logging
from app.services.restock_analyzer import build_default_analyzer
from app.tools.assistant_tools import build_assistant_tools

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

    # 工具能力启动探测（2026-10-01）：配了工具但模型不支持函数调用时，**启动即拒绝**。
    # 为什么不等第一条消息再报：那时用户已经看到了一个 502 错误帧，
    # 而运维要在一堆正常启动日志里找到“模型不支持工具”这条线索。
    # 探测不发网络请求（bind_tools 只做本地包装），代价可忽略。
    if s.use_llm:
        probe_tools = build_assistant_tools()
        try:
            build_chat_model().bind_tools(probe_tools)
        except NotImplementedError as exc:
            raise RuntimeError(
                "当前 LLM 客户端不支持函数调用（bind_tools），但助手已配置只读工具 —— "
                "拒绝以不兼容的配置启动。处理方式：改用支持 function calling 的模型"
                "（如 deepseek-chat），或把 DKD_AGENT_USE_LLM 置 0。"
            ) from exc

    store = await open_checkpoint_store(s)
    app.state.checkpoint_store = store
    # 给 LLM 分支挂上**只读工具**（2026-10-01 补齐智能功能）：
    # 助手因此能真取数（补货清单/设备库存/在途工单/近期销量）并基于真实数据作答，
    # 而不再是“我看不了数据”。写操作**不在这里**：建单/确认/调整一律走补货工作台的人工确认
    # （AGENTS §7.9）。工具集里没有任何写工具，且有测试断言这一点。
    app.state.chat_graph = build_chat_graph(
        checkpointer=store.saver,
        use_llm=s.use_llm,
        tools=build_assistant_tools() if s.use_llm else None,
    )
    # 补货工作台服务（1-7）：真实 store + 真实只读依赖；测试可用 app.state.restock_service 整体替换
    app.state.restock_service = restock.build_default_service()
    # 定时分析执行器（1-9）：与工作台共用同一份 checkpointer；
    # 测试可用 app.state.restock_analyzer 整体替换
    app.state.restock_analyzer = build_default_analyzer(checkpointer=store.saver, settings=s)
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
    app.include_router(restock.router)

    @app.get("/health", tags=["ops"], summary="健康检查（含依赖就绪状态）")
    async def health() -> dict[str, object]:
        """供 Java 网关与 dkd-quartz 探活；不暴露任何凭据。

        `checkpoint_location` 是**已脱敏**的位置描述（sqlite 是文件路径，PG 是口令掩码后的
        DSN），为什么报“实际装配的后端”而不是配置值：配置成 postgres 但装配回退成 sqlite 时，
        只报配置值的 health 会告诉运维“一切正常”。

        `checkpoint_open` 单独列出来：进程关闭后模块级单例仍在，只看后端名会得到
        一个误导排障的假绿灯。
        """
        checkpoint_backend = s.checkpoint_backend
        checkpoint_location: str | None = None
        checkpoint_open = False
        try:
            checkpoint = get_checkpoint_store()
            checkpoint_backend = checkpoint.backend
            checkpoint_location = checkpoint.location
            checkpoint_open = checkpoint.is_open
        except RuntimeError:
            # lifespan 未完成/已关闭：健康检查必须仍能应答，
            # 否则排障时连“服务是否活着”都问不出来
            pass
        return {
            "status": "ok",
            "env": s.app_env,
            "use_llm": s.use_llm,
            "llm_configured": bool(s.llm_api_key),
            "service_secret_configured": bool(s.service_secret),
            "db_configured": bool(s.db_password),
            "checkpoint_backend": checkpoint_backend,
            "checkpoint_location": checkpoint_location,
            "checkpoint_open": checkpoint_open,
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


# ⚠️ 模块级副作用，但必须在这里（不能在 lifespan 里）：
# Windows + postgres 后端要求事件循环策略是 SelectorEventLoop，而策略必须在
# **事件循环被创建之前**生效；uvicorn 的 Server.__init__ 先 import 应用再建循环，
# 所以模块级是唯一够早的时机（原委见 ensure_windows_event_loop_policy docstring）。
# 条件式生效：非 Windows 或 sqlite 后端时本行为 no-op。
ensure_windows_event_loop_policy(get_settings())

app = create_app()
