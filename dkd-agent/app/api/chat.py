"""对话接口（任务 0-9 的 SSE 全链路骨架）。

本阶段只做 echo：用于验证「前端 → Java 网关 → 本服务」的流式透传与取消，
LLM 接入在任务 0-3 完成（当前 DeepSeek Key 已失效，见 .env 标注）。

流式约束（AGENTS §2.2）：必须可取消；客户端断开即停止生成，不得继续占用资源。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.logging_conf import current_request_id
from app.security import parse_user_context

router = APIRouter(prefix="/agent", tags=["chat"])
logger = logging.getLogger("dkd.agent.chat")

# SSE 单帧最大长度提示：避免一次吐出超大块导致网关层缓冲行为不可控
_CHUNK = 24
_INTERVAL_S = 0.05


class ChatRequest(BaseModel):
    """对话入参（对外契约，Pydantic 约束，AGENTS §2.3）。"""

    message: str = Field(min_length=1, max_length=4000, description="用户输入")
    conversation_id: str | None = Field(
        default=None, max_length=64, description="会话ID，缺省则新建"
    )
    scene: int = Field(
        default=1, ge=1, le=4, description="场景：1-通用问答 2-补货 3-诊断 4-运营分析"
    )


def _sse(event: str, data: dict[str, object]) -> str:
    """组装一帧 SSE。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _echo_stream(payload: ChatRequest, request: Request) -> AsyncIterator[str]:
    """echo 流：分片回显 + 心跳，并在客户端断开时立即退出。"""
    ctx = parse_user_context(request)
    rid = current_request_id()
    conversation_id = payload.conversation_id or f"echo-{rid[:12]}"

    yield _sse(
        "meta",
        {
            "conversation_id": conversation_id,
            "request_id": rid,
            "scene": payload.scene,
            "user": ctx.user_name or "system",
            "mode": "echo",
        },
    )

    text = f"[echo] {payload.message}"
    for i in range(0, len(text), _CHUNK):
        # 关键：客户端取消时 Starlette 会取消本 task，is_disconnected 仅作二次确认
        if await request.is_disconnected():
            logger.info("client disconnected, stop streaming")
            return
        yield _sse("delta", {"text": text[i : i + _CHUNK]})
        await asyncio.sleep(_INTERVAL_S)

    yield _sse("done", {"finish_reason": "echo"})


@router.post("/chat", summary="流式对话（SSE，当前为 echo 实现）")
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
            "Connection": "keep-alive",
        },
    )
