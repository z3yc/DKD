"""FastAPI 入口（任务 0-9）。

启动：uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
说明：本服务只监听内网/回环，对外一律经 Java 网关（AGENTS §7.5 鉴权不裸奔）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import chat, fault, ops, restock
from app.checkpoint import (
    ensure_windows_event_loop_policy,
    get_checkpoint_store,
    open_checkpoint_store,
)
from app.config import get_settings
from app.db import dispose_engine
from app.graphs.chat_graph import build_chat_graph, resolve_scene_tools
from app.llm import build_chat_model
from app.logging_conf import RequestContextMiddleware, setup_logging
from app.obs.log_handler import db_log_stats, install_db_log_handler, shutdown_db_log_handler
from app.obs.run_recorder import sweep_stale_runs
from app.services import carrier_config
from app.services.restock_analyzer import build_default_analyzer
from app.tools.assistant_tools import all_assistant_tools, tools_for_scene

logger = logging.getLogger("dkd.agent")


async def _chat_tools_provider(scene: int) -> list[Any]:
    """按场景解析**本轮**工具集（排期 2-7 / 契约 §5.2）。

    为什么放在装配层而不是图里：图不该知道“工具注册表与配置从哪来”，它只承诺
    “每次 respond 前调用一次 provider”——这样配置改后**下一次对话**即生效（不必重启）。
    解析规则见 `chat_graph.resolve_scene_tools`：
      1/4 → 无工具；2/3 → `tools_for_scene(scene)`（**行为与改造前完全一致**）；
      ≥101 → 按配置 `tools` 从注册表过滤（配置写坏 → 空工具 + WARN，不抛穿）。
    """
    return await resolve_scene_tools(
        scene,
        builtin_tools=tools_for_scene,
        registry=all_assistant_tools,
        config_loader=carrier_config.load_chat_config,
    )


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

    # 运行观测收尾（F-05，2026-10-26）：把上一个进程遗留的 running 改成 interrupted。
    # 为什么必须在启动时做：进程被杀不会执行任何清理，页面会永远显示"运行中"，运营以为还在跑；
    # 为什么失败只 WARN：观测是旁路（同决策留痕纪律），不能因为表没建/库抖动就拒绝启动。
    swept = await sweep_stale_runs(s, None)
    if swept:
        logger.warning("启动扫描：%d 条遗留运行已标记为 interrupted（页面显示可重新发起）", swept)

    # 工具能力启动探测（2026-10-01）：配了工具但模型不支持函数调用时，**启动即拒绝**。
    # 为什么不等第一条消息再报：那时用户已经看到了一个 502 错误帧，
    # 而运维要在一堆正常启动日志里找到“模型不支持工具”这条线索。
    # 探测不发网络请求（bind_tools 只做本地包装），代价可忽略。
    if s.use_llm:
        # 探测**全部**可能被绑定的工具（不只补货那份）：任何一个工具的 schema 不被支持都要启动即拒，
        # 否则就会变成“诊断场景第一条消息才 502”，而那是个更难定位的失败。
        probe_tools = all_assistant_tools()
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
    # 执行日志采集（排期 2-4）：挂在 checkpointer 之后、事件循环已就绪时。
    # 为什么不在 create_app() 里挂：那时还没有运行中的事件循环（后台 flush task 建不起来）；
    # 为什么失败也不阻断启动：agent_log 是排障旁路，表未建/DB 抖动只 WARN 一次
    # （见 app/obs/log_handler.py 纪律 4），不能因此让服务起不来。
    install_db_log_handler(s)
    # 给 LLM 分支挂上**只读工具**（2026-10-01 补齐智能功能；同日升级为**按场景**挂）：
    #   场景 2（补货）= 补货清单/设备库存/在途工单/近期销量；
    #   场景 3（诊断）= 设备消歧/异常检测/历史维修单 + 库存/在途；
    #   场景 1/4 = 不挂工具（提示词也如实说“本场景查不了”，不许凭印象编数）。
    # 写操作**不在这里**：建单/确认/调整一律走页面人工确认（AGENTS §7.9），
    # 工具集里没有任何写工具，且有测试断言这一点。
    # 排期 2-7：工具集改为**每轮按场景求值**（`tools_provider`），而不是启动时把 2/3 两套
    # 写死——否则「新增/停用自定义场景」「改工具子集」必须重启服务。ToolNode 仍注册全量并集
    # （`tool_registry`）：模型只能 bind 到本轮子集，但工具节点必须认识所有可能被请求的工具。
    # `config_loader` 负责提示词覆盖与 LLM 参数（温度/上限/重试/超时/模型）每轮从配置读；
    # 配置不可读时它返回 None → 逐字回现状（契约 §5.2）。
    app.state.chat_graph = build_chat_graph(
        checkpointer=store.saver,
        use_llm=s.use_llm,
        tools_provider=_chat_tools_provider if s.use_llm else None,
        tool_registry=all_assistant_tools() if s.use_llm else None,
        config_loader=carrier_config.load_chat_config,
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
        # 先 flush 执行日志、再关连接池：顺序反了会把最后一批日志写进已 dispose 的引擎
        await shutdown_db_log_handler()
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
    app.include_router(fault.router)

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
        # 执行日志采集（2-4）的装配状态：只读计数器，**不触发 flush/不连库**。
        log_db = db_log_stats()
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
            # 2-7/2-8：配置来源开关与 TTL。**只报配置值，不在这里连库**——
            # 健康检查必须能在 DB 不可达/表未建时照常应答，而那正是它最有用的时刻。
            # `config_from_db=False` 即「一键回落纯代码默认」，是灰度回退的判据。
            "config_from_db": s.config_from_db,
            "config_ttl_s": s.config_ttl_s,
            # 2-4：执行日志采集。`log_db_dropped`/`log_db_write_errors` 是排障关键信号——
            # 它们长期 >0 说明“日志在丢/写不进库”，而此时业务可能完全正常（静默观测缺失）。
            "log_db_enabled": s.log_db_enabled,
            "log_db_retention_days": s.log_retention_days,
            "log_db_installed": log_db["installed"],
            "log_db_queue_size": log_db["queue_size"],
            "log_db_dropped": log_db["dropped"],
            "log_db_write_errors": log_db["write_errors"],
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
