"""对话接口（任务 0-9 / 0-10 / 0-12）。

本模块承载三件事：
1. **流式透传**（0-9）：SSE 逐帧输出，必须可取消（AGENTS §2.2）；
2. **会话持久化与断线重连**（0-10）：图带 SQLite checkpointer，`thread_id = conversation_id`；
   客户端带 `Last-Event-ID` 重连时**不重跑图**，从 checkpoint 读回本轮回复并从中断位置续传；
3. **留痕与计量**（0-12）：每轮写 agent_decision_log / agent_message，并在请求前做 token 限额熔断。

频率常量说明：`_CHUNK` 只影响分帧粒度，事件 id 即帧序号，前端按 `Last-Event-ID` 续传，
不依赖该常量。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.audit import (
    RESULT_OK,
    QuotaExceededError,
    enforce_daily_quota,
    record_decision,
    record_message,
)
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.llm import LlmError
from app.logging_conf import current_request_id
from app.security import HEADER_REQUEST_ID, parse_user_context

router = APIRouter(prefix="/agent", tags=["chat"])
logger = logging.getLogger("dkd.agent.chat")

_CHUNK = 24
_INTERVAL_S = 0.03
HEADER_LAST_EVENT_ID = "Last-Event-ID"


class ChatRequest(BaseModel):
    """对话入参（对外契约，Pydantic 约束，AGENTS §2.3）。"""

    message: str = Field(min_length=1, max_length=4000, description="用户输入")
    conversation_id: str | None = Field(
        default=None, max_length=64, description="会话ID（= LangGraph thread_id），缺省则新建"
    )
    scene: int = Field(
        default=1, ge=1, le=4, description="场景：1-通用问答 2-补货 3-诊断 4-运营分析"
    )


def _sse(event: str, data: dict[str, object], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str) -> list[str]:
    return [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)] or [""]


async def _load_turn_from_checkpoint(graph: object, config: dict) -> tuple[str, int]:
    """断线重连：从 checkpoint 读回**上一轮**的回复与历史长度（不重跑图，避免重复追加上下文）。"""
    snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
    values = getattr(snapshot, "values", None) or {}
    return str(values.get("reply", "")), len(values.get("messages", []))


async def _meter(session: object, *, settings: Settings, scene: int, user_id: int | None) -> None:
    """请求前限额熔断（LLM 一旦发出就产生费用，事后告警已晚）。"""
    await enforce_daily_quota(
        session,  # type: ignore[arg-type]
        user_id=user_id,
        per_user_limit=settings.token_daily_limit_per_user,
        global_limit=settings.token_daily_limit_global,
    )


async def _echo_stream(payload: ChatRequest, request: Request) -> AsyncIterator[str]:
    settings = get_settings()
    ctx = parse_user_context(request)
    rid = current_request_id()
    thread_id = payload.conversation_id or f"conv-{uuid.uuid4().hex[:16]}"
    config = {"configurable": {"thread_id": thread_id}}
    graph = request.app.state.chat_graph
    last_event_id = request.headers.get(HEADER_LAST_EVENT_ID)
    resuming = last_event_id is not None
    offset = int(last_event_id) if (last_event_id or "").isdigit() else 0

    try:
        if resuming:
            reply, history_len = await _load_turn_from_checkpoint(graph, config)
            if not reply:
                yield _sse("error", {"msg": "会话不存在或无可续传内容", "code": 404})
                return
        else:
            # 1) 限额：超限直接熔断，不再调用 LLM
            # 注意：session 获取也放在 try 内——连工厂/建连异常同样属基础设施故障，
            # 不得让审计/计量链路把用户对话打断（AGENTS §6.1 不静默，但也不得穿透）
            if settings.audit_enabled:
                try:
                    async with get_sessionmaker()() as session:
                        await _meter(
                            session, settings=settings, scene=payload.scene, user_id=ctx.user_id
                        )
                except QuotaExceededError as exc:
                    yield _sse("error", {"msg": str(exc), "code": 429})
                    return
                except SQLAlchemyError as exc:
                    logger.warning(
                        "计量库不可用，跳过限额判断 rid=%s err=%s",
                        rid,
                        type(exc).__name__,
                        exc_info=True,
                    )

            # 2) 执行图（checkpointer 自动持久化本轮状态）
            result = await graph.ainvoke(  # type: ignore[attr-defined]
                {"messages": [HumanMessage(content=payload.message)], "scene": payload.scene},
                config,
            )
            reply = str(result.get("reply", ""))
            history_len = len(result.get("messages", []))

            # 3) 留痕与计量（基础设施故障不中断对话；编码错误仍应暴露）
            if settings.audit_enabled:
                try:
                    async with get_sessionmaker()() as session:
                        tokens = int(result.get("tokens_in", 0)) + int(result.get("tokens_out", 0))
                        await record_decision(
                            session,
                            scene=payload.scene,
                            action="chat.respond",
                            request_id=rid,
                            user_id=ctx.user_id,
                            input_context={"message": payload.message, "thread_id": thread_id},
                            llm_output={"reply": reply, "model": result.get("model")},
                            target_type="conversation",
                            target_id=thread_id,
                            result=RESULT_OK,
                            cost_tokens=tokens,
                            confidence=None,
                        )
                        await record_message(
                            session,
                            conversation_id=thread_id,
                            user_id=ctx.user_id,
                            # seq = 助手消息在会话中的真实位置（首轮为 2：用户1、助手2）
                            seq=history_len,
                            msg_role=2,
                            content=reply,
                            model=str(result.get("model")) if result.get("model") else None,
                            tokens_in=int(result.get("tokens_in", 0)),
                            tokens_out=int(result.get("tokens_out", 0)),
                            request_id=rid,
                        )
                except SQLAlchemyError as exc:
                    logger.warning(
                        "留痕/计量写入降级（对话继续）rid=%s err=%s",
                        rid,
                        type(exc).__name__,
                        exc_info=True,
                    )
    except LlmError as exc:
        logger.warning("chat llm failed rid=%s: %s", rid, exc)
        yield _sse("error", {"msg": str(exc), "code": 502})
        return

    yield _sse(
        "meta",
        {
            "conversation_id": thread_id,
            "request_id": rid,
            "scene": payload.scene,
            "user": ctx.user_name or "system",
            "mode": "echo",
            "resumed": resuming,
            "history_len": history_len,
        },
    )

    for index, chunk in enumerate(_chunks(reply), start=1):
        if await request.is_disconnected():  # 客户端断开：立即停止，不空转（§2.2）
            logger.info("client disconnected rid=%s at frame=%s", rid, index)
            return
        if index <= offset:  # 断线重连：跳过已发送帧
            continue
        yield _sse("delta", {"text": chunk}, event_id=index)
        await asyncio.sleep(_INTERVAL_S)

    yield _sse(
        "done", {"finish_reason": "echo", "history_len": history_len, "frames": len(_chunks(reply))}
    )


@router.post("/chat", summary="流式对话（SSE；0-10 起带会话持久化与断点续传）")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """返回 text/event-stream。

    响应头显式禁用缓冲：Java 网关侧（StreamingResponseBody）与本服务都要保证逐帧输出。
    """
    return StreamingResponse(
        _echo_stream(payload, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            HEADER_REQUEST_ID: current_request_id(),
        },
    )
