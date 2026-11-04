"""知识库管理接口（排期 3-6，服务间路由 `/agents/kb/**`）。

## 为什么挂在 `/agents/**` 而不是 `/agent/**`

`/agent/**` 是**用户经 Java 网关**的通道（身份来自 `X-Agent-User`，可伪造）；
知识库的入库/检索测试是**管理台经 Java 代理**触发的内部动作，身份来自服务间密钥
（`X-Agent-Secret`，`verify_service_secret`）。两类鉴权体系放在不同前缀下，
边界在 URL 上就可见——与 `/agents/config/reload`、`/agents/llm/ping` 同一口径（AGENTS §7.5）。

## 为什么 Java 只代理、写入只经 Python

文档/切片/向量/检索留痕属智能体自有数据（AGENTS §7.3），切片与向量化唯一事实源在
`app/services/kb_store.py`。本模块只做 HTTP 契约：参数校验、错误信封、DB 异常兜底，
不写任何业务算法（避免“页面能入库、工具检索到的却是另一套切法”的漂移）。

## 错误信封

统一 `{code, msg, data}`：
- 参数/业务校验失败 → **HTTP 400**；
- 文档不存在 → **HTTP 404**；
- 数据库/未知异常 → **HTTP 500**，`msg` 固定为通用文案（**不回传 SQL/堆栈**，AGENTS §6.1）；
  内部日志 `logger.exception` 保留排障信息。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.security import verify_service_secret
from app.services.kb_store import (
    CATEGORIES,
    KbNotFoundError,
    KbValidationError,
    ingest_document,
    list_documents,
    search,
    set_enabled,
    stats,
)

router = APIRouter(
    prefix="/agents/kb",
    tags=["kb"],
    dependencies=[Depends(verify_service_secret)],
)
logger = logging.getLogger("dkd.agent.api.kb")

# 响应类型：成功用 dict；失败直接返回带 HTTP 状态码的 JSONResponse
KbResponse = dict[str, Any] | JSONResponse


async def get_session() -> AsyncIterator[AsyncSession]:
    """请求级数据库会话（只读/自有表写都由本会话完成；测试用 dependency_overrides 注入假会话）。"""
    async with get_sessionmaker()() as session:
        yield session


# 为什么用 Annotated 而不是 `session=Depends(get_session)` 默认值：前者是 FastAPI 官方推荐写法，
# 也避免 ruff B008（“不要在参数默认值里调用函数”）；默认值写法仍可用，但会让本模块 lint 需要 noqa。
SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ------------------------------------------------------------------------------------
# 请求体（camelCase 对齐 Java/Vue；`populate_by_name` 允许测试用 snake_case 构造）
# ------------------------------------------------------------------------------------
class DocumentCreateRequest(BaseModel):
    """文档入库入参（正文先由上传端转纯文本；首版只支持粘贴/纯文本）。"""

    doc_code: str = Field(alias="docCode", description="文档编码")
    title: str = Field(description="标题 1~200 字")
    category: str = Field(default="manual", description=f"分类：{sorted(CATEGORIES)}")
    source_type: str = Field(default="text", alias="sourceType", description="text/file/url")
    source_ref: str | None = Field(default=None, alias="sourceRef", description="文件名/URL")
    content: str = Field(description="提取后的纯文本（≤1MB）")
    operator: str | None = Field(default=None, description="操作人")

    model_config = {"populate_by_name": True}


class DocumentEnabledRequest(BaseModel):
    """启停入参（前端/Java 传 1/0，Pydantic 一并接受布尔）。"""

    enabled: int | bool = Field(description="1-启用 0-停用")


class SearchRequest(BaseModel):
    """检索入参（管理台“检索测试”；每次调用都会写检索留痕）。"""

    query: str = Field(description="检索语句")
    top_k: int | None = Field(default=None, alias="topK", description="召回数 1~20")
    scene: int | None = Field(default=None, description="场景码（可空）")
    request_id: str | None = Field(default=None, alias="requestId")
    conversation_id: str | None = Field(default=None, alias="conversationId")

    model_config = {"populate_by_name": True}


# ------------------------------------------------------------------------------------
# 错误信封（为什么集中在一处：三条错误映射必须在所有接口完全一致）
# ------------------------------------------------------------------------------------
def _error(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg, "data": None})


def _handle_failure(exc: Exception, action: str) -> JSONResponse:
    """把异常映射为错误信封；**只在日志里保留细节**，响应不泄露 SQL/堆栈。"""
    if isinstance(exc, KbValidationError):
        return _error(400, str(exc))
    if isinstance(exc, KbNotFoundError):
        return _error(404, str(exc))
    logger.exception("知识库接口失败：action=%s err=%s", action, type(exc).__name__)
    return _error(500, "知识库操作失败，请稍后重试或查看服务日志")


def register_error_handlers(app: Any) -> None:
    """把 FastAPI 默认的 422 请求体校验错误改为 **400 信封**（契约要求）。

    为什么需要单独注册：`RequestValidationError` 在进入端点函数**之前**由 FastAPI 抛出，
    端点内的 try/except 捕获不到；而本任务要求“参数错误 → 400 信封”。
    main.py 挂载 `kb.router` 后调用一次本函数即可（测试中同样调用，验证行为一致）。

    @param app FastAPI 应用实例（挂载 kb.router 的同一个 app）
    """

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ",".join(str(error.get("loc")[-1]) for error in exc.errors() if error.get("loc"))
        # 只记字段名与路径，不记请求体（知识库正文可能很长/含敏感内容，AGENTS §6.1）
        logger.warning("知识库接口参数校验失败：fields=%s path=%s", fields, request.url.path)
        return _error(400, f"参数校验失败：{fields or '请求体'}")


# ------------------------------------------------------------------------------------
# 接口
# ------------------------------------------------------------------------------------
@router.get(
    "/documents",
    response_model=None,
    summary="知识库文档列表（分页，Java 管理台代理）",
)
async def documents(
    session: SessionDep,
    category: str | None = Query(default=None, description="分类过滤"),
    enabled: int | None = Query(default=None, ge=0, le=1, description="1-启用 0-停用"),
    keyword: str | None = Query(default=None, max_length=100, description="标题/编码关键词"),
    page_num: int = Query(default=1, ge=1, alias="pageNum", description="页码（从 1 起）"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize", description="每页条数"),
) -> KbResponse:
    """文档分页列表 `data={total, rows}`（rows 为 camelCase，前端直接展示）。"""
    try:
        data = await list_documents(
            session,
            category=category,
            enabled=enabled,
            keyword=keyword,
            page=page_num,
            page_size=page_size,
        )
    except Exception as exc:  # noqa: BLE001 —— 统一映射信封；细节只进日志
        return _handle_failure(exc, "documents")
    return {"code": 200, "msg": "ok", "data": data}


@router.post(
    "/documents",
    response_model=None,
    summary="文档入库（切片 + 本地向量化，内容相同则跳过）",
)
async def ingest(payload: DocumentCreateRequest, session: SessionDep) -> KbResponse:
    """入库并重建切片；`data.skipped=true` 表示内容未变化（前端要展示“跳过了多少切片”）。"""
    try:
        data = await ingest_document(
            session,
            doc_code=payload.doc_code,
            title=payload.title,
            category=payload.category,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            content=payload.content,
            operator=payload.operator,
        )
    except Exception as exc:  # noqa: BLE001
        return _handle_failure(exc, "ingest")
    return {"code": 200, "msg": "ok", "data": data}


@router.put(
    "/documents/{docCode}/enabled",
    response_model=None,
    summary="启用/停用文档（停用后不参与检索）",
)
async def update_enabled(
    session: SessionDep,
    payload: DocumentEnabledRequest,
    doc_code: str = Path(alias="docCode", description="文档编码"),
) -> KbResponse:
    """只改 enabled 标记：不删切片、不回滚历史检索记录（启用即恢复可检索）。"""
    try:
        data = await set_enabled(session, doc_code, payload.enabled)
    except Exception as exc:  # noqa: BLE001
        return _handle_failure(exc, "update_enabled")
    return {"code": 200, "msg": "ok", "data": data}


@router.post(
    "/search",
    response_model=None,
    summary="知识库检索测试（写检索留痕，返回回退原因）",
)
async def search_documents(payload: SearchRequest, session: SessionDep) -> KbResponse:
    """`data={hits, topK, minScore, retrievalId, fallbackReason}`（前端按回退原因展示治理信息）。"""
    try:
        data = await search(
            session,
            query=payload.query,
            top_k=payload.top_k,
            scene=payload.scene,
            request_id=payload.request_id,
            conversation_id=payload.conversation_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _handle_failure(exc, "search")
    return {"code": 200, "msg": "ok", "data": data}


@router.get(
    "/stats",
    response_model=None,
    summary="知识库概览（文档/切片/检索次数 + 回退率指标，5-3）",
)
async def kb_stats(session: SessionDep) -> KbResponse:
    """原样透传 `kb_store.stats()`：旧字段不变，新增 5-3 的回退率三个字段。

    不在 API 层重算比例：口径唯一一处（`kb_store.stats`），页面/报表/告警读到的
    回退率必须完全一致；API 只负责信封与错误映射。
    """
    try:
        data = await stats(session)
    except Exception as exc:  # noqa: BLE001
        return _handle_failure(exc, "stats")
    return {"code": 200, "msg": "ok", "data": data}


__all__ = [
    "DocumentCreateRequest",
    "DocumentEnabledRequest",
    "SearchRequest",
    "get_session",
    "register_error_handlers",
    "router",
]
