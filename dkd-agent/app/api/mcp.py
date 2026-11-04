"""MCP 工具接入接口（排期 3-7，服务间路由 `/agents/mcp/**`）。

## 边界

MCP 的 `agent_mcp_*` 表与工具同步是**智能体自有数据**（AGENTS §7.3）：Java 管理台只做
鉴权/代理/错误映射，协议交互与落库唯一事实源在 `app/services/mcp_client.py`。
本模块只负责 HTTP 契约：参数校验、错误信封、`McpError` → 状态码映射。

## 错误映射（与 task-19 一致）

- 参数/配置/只读闸问题 → **400**（含“写工具一律拒绝”）；
- server/工具不存在 → **404**；
- 上游失败 → **502**；超时 → **504**；
- 其余异常 → **500**，msg 固定通用文案（不回传 SQL/堆栈/请求头，AGENTS §6.1）。

`/call` 是**只读测试**通道：只允许调用已同步且 `read_only=1` 的工具。
写操作永远只能走 Java 回调（AGENTS §7.3/§7.9），这里不提供任何写能力。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.security import verify_service_secret
from app.services.mcp_client import (
    SERVER_CODE_PATTERN,
    McpError,
    McpServer,
    call_tool,
    list_synced_tools,
    list_tools,
    load_server_by_code,
    load_synced_tool,
    probe_and_sync,
    split_read_only_tools,
    sync_server_tools,
)

router = APIRouter(
    prefix="/agents/mcp",
    tags=["mcp"],
    dependencies=[Depends(verify_service_secret)],
)
logger = logging.getLogger("dkd.agent.api.mcp")

MpResponse = dict[str, Any] | JSONResponse

#: McpError.error_type → HTTP 状态码；未列出的（transport/http/jsonrpc/protocol）一律 502。
_ERROR_STATUS: dict[str, int] = {
    "validation": 400,
    "unsupported": 400,
    "disabled": 400,
    "write_denied": 400,
    "not_found": 404,
    "timeout": 504,
}


async def get_session() -> AsyncIterator[AsyncSession]:
    """请求级数据库会话（测试用 dependency_overrides 注入假会话，不连真库）。"""
    async with get_sessionmaker()() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CallRequest(BaseModel):
    """工具调用入参（只读测试通道；`arguments` 原样进 JSON-RPC `tools/call`）。"""

    arguments: dict[str, Any] = Field(
        default_factory=dict, description="工具入参（JSON 对象，可空）"
    )


def _error(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg, "data": None})


def _map_failure(exc: Exception, action: str) -> JSONResponse:
    """`McpError` → 状态码；未知异常 → 500 通用文案（细节只进日志）。"""
    if isinstance(exc, McpError):
        status = _ERROR_STATUS.get(exc.error_type, 502)
        return _error(status, exc.message)
    logger.exception("MCP 接口失败：action=%s err=%s", action, type(exc).__name__)
    return _error(500, "MCP 操作失败，请稍后重试或查看服务日志")


def register_error_handlers(app: Any) -> None:
    """把 FastAPI 默认的 422 请求体校验错误改为 **400 信封**（与 kb 接口同口径）。

    `RequestValidationError` 在端点函数之前抛出，端点内 try/except 捕获不到；
    main.py 挂载 `mcp.router` 后调用一次即可（测试中同样调用）。
    """

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ",".join(str(error.get("loc")[-1]) for error in exc.errors() if error.get("loc"))
        # 只记字段名与路径，不记请求体（工具参数可能含业务数据/敏感值）
        logger.warning("MCP 接口参数校验失败：fields=%s path=%s", fields, request.url.path)
        return _error(400, f"参数校验失败：{fields or '请求体'}")


def _ensure_mcp_enabled() -> None:
    if not get_settings().mcp_enabled:
        raise McpError("disabled", "MCP 功能已关闭（DKD_AGENT_MCP_ENABLED=false）", None)


def _validate_server_code(code: str) -> str:
    if not SERVER_CODE_PATTERN.match(code or ""):
        raise McpError("validation", f"MCP server 编码不合法：{code!r}", None)
    return code


async def _require_server(
    session: AsyncSession, code: str, *, include_disabled: bool = False
) -> McpServer:
    """server 不存在 → 404；默认要求启用，`include_disabled=True` 时不要求。

    探测/同步传 `include_disabled=True`：安全流程是「新增默认停用 → 探测确认只读工具 →
    人工复核后启用」，若探测也被 enabled 挡住，运营只能“先启用再探测”，闸门形同虚设。
    """
    server = await load_server_by_code(
        session, _validate_server_code(code), include_disabled=include_disabled
    )
    if server is None:
        raise McpError("not_found", f"MCP server 不存在：{code}", None)
    return server


# ------------------------------------------------------------------------------------
# 接口
# ------------------------------------------------------------------------------------
@router.post(
    "/probe/{code}",
    response_model=None,
    summary="探测 MCP server 并同步只读工具（写探测记录）",
)
async def probe(code: str, session: SessionDep) -> MpResponse:
    """握手 + 拉工具清单 + 只读过滤 + 落库；失败也写探测记录并更新 server 状态。"""
    try:
        _ensure_mcp_enabled()
        server = await _require_server(session, code, include_disabled=True)
        data = await probe_and_sync(session, server)
    except Exception as exc:  # noqa: BLE001 —— 统一映射信封
        return _map_failure(exc, "probe")
    return {"code": 200, "msg": "ok", "data": data}


@router.post(
    "/sync/{code}",
    response_model=None,
    summary="只同步 MCP 只读工具（不写探测记录）",
)
async def sync(code: str, session: SessionDep) -> MpResponse:
    """返回 `{synced, rejected, disabled}`：rejected = 因非只读被拒绝的工具数。"""
    try:
        _ensure_mcp_enabled()
        server = await _require_server(session, code, include_disabled=True)
        raw_tools = await list_tools(server)
        allowed, rejected = split_read_only_tools(raw_tools)
        counts = await sync_server_tools(session, server, allowed)
        data = {
            "synced": counts["synced"],
            "rejected": len(rejected),
            "disabled": counts["disabled"],
        }
    except Exception as exc:  # noqa: BLE001
        return _map_failure(exc, "sync")
    return {"code": 200, "msg": "ok", "data": data}


@router.get("/tools", response_model=None, summary="已同步的 MCP 工具列表")
async def tools(
    session: SessionDep,
    server_code: str | None = Query(default=None, alias="serverCode", description="按 server 过滤"),
) -> MpResponse:
    """`data` 直接是**工具数组**（Java/Vue 均按数组消费，含 code/name/readOnly/enabled）。

    为什么不做 `{total, rows}`：Java `AgentMcpClient.tools()` 的契约是“信封 data 为工具数组”，
    前端 `normalizeRows` 也只认数组；多包一层会让页面显示“没有已同步工具”。
    """
    try:
        if server_code:
            _validate_server_code(server_code)
        rows = await list_synced_tools(session, server_code)
    except Exception as exc:  # noqa: BLE001
        return _map_failure(exc, "tools")
    return {"code": 200, "msg": "ok", "data": rows}


@router.post(
    "/call/{code}/{tool}",
    response_model=None,
    summary="只读工具调用测试（写工具一律 400）",
)
async def call(code: str, tool: str, payload: CallRequest, session: SessionDep) -> MpResponse:
    """调用已同步的**只读**工具；返回 `{text, truncated, isError}`（截断会显式标注）。

    只读判定以**已同步记录**为准（`agent_mcp_tool.read_only`）：`tools/call` 时不再
    向 server 重拉清单（每次调用多一次往返没必要），但同步闸已保证该记录来自
    `readOnlyHint=true` 的工具。
    """
    try:
        _ensure_mcp_enabled()
        server = await _require_server(session, code)
        record = await load_synced_tool(session, server.id, tool)
        if record is None:
            raise McpError("not_found", f"工具未同步或不存在：{tool}", None)
        if not record["read_only"]:
            raise McpError("write_denied", f"写工具禁止直调：{tool}（写操作仍只能回调 Java）", None)
        if not record["enabled"]:
            raise McpError("disabled", f"工具已停用：{tool}", None)
        data = await call_tool(server, record["name"], payload.arguments)
    except Exception as exc:  # noqa: BLE001
        return _map_failure(exc, "call")
    return {"code": 200, "msg": "ok", "data": data}


__all__ = [
    "CallRequest",
    "get_session",
    "register_error_handlers",
    "router",
]
