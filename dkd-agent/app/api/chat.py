"""对话接口（任务 0-9 / 0-10 / 0-12，streaming 由 1-5 前置改进引入）。

本模块承载四件事：
1. **流式透传**（0-9）：SSE 逐帧输出，必须可取消（AGENTS §2.2）；
2. **会话持久化与断线重连**（0-10）：图带 SQLite checkpointer，`thread_id = conversation_id`；
   客户端带 `Last-Event-ID` 重连时**不重跑图**，从 checkpoint 读回本轮回复并从中断位置续传；
3. **留痕与计量**（0-12）：每轮写 agent_decision_log / agent_message，并在请求前做 token 限额熔断；
4. **token 级流式**（M1 验收登记的缺口，本次修复）：LLM 节点逐 token 下发，而不是整段取回后分帧。

帧顺序契约（方案 §3.3.1）：`meta` 先于所有 `delta`，`done` 最后；`delta` 帧带自增 `id`
（即帧序号），客户端按 `Last-Event-ID` 续传，`_CHUNK` 只是 echo 兜底分片的粒度，不参与 id 语义。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
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

"""对话图里承担“生成回复”的节点名（build_chat_graph 注册名，echo/LLM 两种实现同名）。"""
_RESPOND_NODE = "respond"


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


async def _load_turn_from_checkpoint(graph: object, config: dict) -> tuple[str, int, str | None]:
    """断线重连：从 checkpoint 读回上一轮回复、历史长度与实际模型（不重跑图，避免重复追加上下文）。

    为什么连模型一起读回：meta/done 帧要如实标注本轮是不是走了真 LLM（`mode`），
    而续传路径不重跑图，模型名只能从 checkpoint 取。
    """
    snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
    values = getattr(snapshot, "values", None) or {}
    model = values.get("model")
    return (
        str(values.get("reply", "")),
        len(values.get("messages", [])),
        (str(model) if model else None),
    )


def _mode_of(model: str | None) -> str:
    """如实标注本轮回复来源（M1 起 DKD_AGENT_USE_LLM=1）。

    为什么不能硬编码 "echo"（2026-09-21 M1 验收发现）：echo 节点写 `model="echo"`，
    LLM 节点写服务端实际返回的模型名；若 meta 永远写 echo，审计与排障会把真 LLM 调用误判为链路回显，
    也让排期 0-12 的成本归因失去依据。
    """
    return "echo" if not model or model == "echo" else "llm"


class _TurnOutcome:
    """一轮生成的收尾信息（流式期间逐步填充，供 done 帧与留痕/计量共用）。

    为什么需要一个可变对象跨生成器传递：`async for` 只能拿到 yield 出来的分片，
    而图执行完的最终 state（reply/model/tokens/messages）需要另开一条通道回传；
    用带 __slots__ 的小类比 dict 更不容易写错字段名。
    """

    __slots__ = ("reply", "streamed", "history_len", "model", "tokens_in", "tokens_out")

    def __init__(self) -> None:
        self.reply = ""
        self.streamed = ""
        self.history_len = 0
        self.model: str | None = None
        self.tokens_in = 0
        self.tokens_out = 0


def _chunk_text(token: object) -> str:
    """从 LLM 回调分片里取文本（content 可能是 str，也可能是 content blocks 列表）。"""
    content = getattr(token, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text", ""), str)
        )
    return ""


async def _stream_graph_tokens(
    graph: object, *, message: str, scene: int, config: dict, outcome: _TurnOutcome
) -> AsyncIterator[str]:
    """执行对话图，把 LLM 逐 token 回调转成可直接下发的文本分片。

    为什么用 `astream(..., stream_mode=["messages", "values"])`（M1 登记的缺口修复）：
    - `messages`：LangGraph 把节点内 LLM 的**流式回调**逐块吐出 → 真正 token 级，
      首字延迟从“整段 LLM 时延”降到“首 token 时延”（此前 `ainvoke` 整段取回后再分 24 字符帧）；
    - `values`：每步后的完整 state（结束时即 `reply/model/tokens_in/tokens_out/messages`），
      留痕与计量依赖它；
    - 只订阅 messages 拿不到最终 state，只订阅 values 拿不到中间 token，两者都需要。

    节点名过滤：只有 respond 节点的 token 才是给用户看的回复。Phase 1 引入 Supervisor/工具节点后，
    工具节点内部的小模型输出不能混进回复流，因此显式过滤而不是“收到什么发什么”。

    分片类型过滤（实测踩坑）：`stream_mode="messages"` 除了 LLM 的分片回调，还会把节点**直接返回的
    完整 `AIMessage`** 也当作一条 messages 事件吐出（echo 节点就是这样）。若不过滤，echo 模式会先按
    “整段 token”发一帧、再被兜底分片重复发一遍。只有 `AIMessageChunk` 才是真分片。
    """
    final_state: dict[str, Any] = {}
    async for mode, chunk in graph.astream(  # type: ignore[attr-defined]
        {"messages": [HumanMessage(content=message)], "scene": scene},
        config,
        stream_mode=["messages", "values"],
    ):
        if mode == "values":
            final_state = chunk or {}
            continue
        token, metadata = chunk
        if (metadata or {}).get("langgraph_node") != _RESPOND_NODE:
            continue
        if not isinstance(token, AIMessageChunk):
            continue
        text = _chunk_text(token)
        if text:
            outcome.streamed += text
            yield text

    outcome.reply = str(final_state.get("reply", "")) or outcome.streamed
    outcome.history_len = len(final_state.get("messages", []))
    model = final_state.get("model")
    outcome.model = str(model) if model else None
    outcome.tokens_in = int(final_state.get("tokens_in", 0) or 0)
    outcome.tokens_out = int(final_state.get("tokens_out", 0) or 0)


async def _meter(session: object, *, settings: Settings, scene: int, user_id: int | None) -> None:
    """请求前限额熔断（LLM 一旦发出就产生费用，事后告警已晚）。"""
    await enforce_daily_quota(
        session,  # type: ignore[arg-type]
        user_id=user_id,
        per_user_limit=settings.token_daily_limit_per_user,
        global_limit=settings.token_daily_limit_global,
    )


async def _resume_stream(
    graph: object,
    *,
    config: dict,
    thread_id: str,
    rid: str,
    payload: ChatRequest,
    request: Request,
    offset: int,
) -> AsyncIterator[str]:
    """断线续传：从 checkpoint 读回本轮回复，只补发客户端未收到的帧。"""
    reply, history_len, model = await _load_turn_from_checkpoint(graph, config)
    if not reply:
        yield _sse("error", {"msg": "会话不存在或无可续传内容", "code": 404})
        return
    yield _sse(
        "meta",
        {
            "conversation_id": thread_id,
            "request_id": rid,
            "scene": payload.scene,
            "user": parse_user_context(request).user_name or "system",
            "mode": _mode_of(model),
            "model": model,
            "resumed": True,
            "history_len": history_len,
        },
    )
    frames = 0
    for index, chunk in enumerate(_chunks(reply), start=1):
        frames = index
        if index <= offset:  # 已收到的帧不重发
            continue
        if await request.is_disconnected():
            logger.info("client disconnected rid=%s at frame=%s (resume)", rid, index)
            return
        yield _sse("delta", {"text": chunk}, event_id=index)
        await asyncio.sleep(_INTERVAL_S)
    yield _sse(
        "done",
        {
            "finish_reason": _mode_of(model),
            "history_len": history_len,
            "frames": frames,
            "model": model,
            "resumed": True,
        },
    )


async def _record_turn(
    *,
    settings: Settings,
    payload: ChatRequest,
    ctx_user_id: int | None,
    rid: str,
    thread_id: str,
    outcome: _TurnOutcome,
) -> None:
    """写决策留痕与消息投影（基础设施故障降级放行，不打断对话，AGENTS §6.1/§6.3）。"""
    if not settings.audit_enabled:
        return
    try:
        async with get_sessionmaker()() as session:
            await record_decision(
                session,
                scene=payload.scene,
                action="chat.respond",
                request_id=rid,
                user_id=ctx_user_id,
                input_context={"message": payload.message, "thread_id": thread_id},
                llm_output={"reply": outcome.reply, "model": outcome.model},
                target_type="conversation",
                target_id=thread_id,
                result=RESULT_OK,
                cost_tokens=outcome.tokens_in + outcome.tokens_out,
                confidence=None,
            )
            await record_message(
                session,
                conversation_id=thread_id,
                user_id=ctx_user_id,
                # seq = 助手消息在会话中的真实位置（首轮为 2：用户1、助手2）
                seq=outcome.history_len,
                msg_role=2,
                content=outcome.reply,
                model=outcome.model,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
                request_id=rid,
            )
    except SQLAlchemyError as exc:
        logger.warning(
            "留痕/计量写入降级（对话继续）rid=%s err=%s", rid, type(exc).__name__, exc_info=True
        )


async def _chat_stream(payload: ChatRequest, request: Request) -> AsyncIterator[str]:
    """SSE 主流程：限额 → `meta` 帧 → 逐 token `delta` 帧 → `done` 帧（含留痕/计量）。

    因为要“边生成边下发”，`meta` 只能携带**开始时已知**的信息（会话/身份/mode/续传标记）；
    服务端实际模型名与 history_len 要等生成结束才知道，由 `done` 帧补全——
    宁可留空也不写配置里的假值（见面试复盘“配置的模型 ≠ 实际服务的模型”）。
    """
    settings = get_settings()
    ctx = parse_user_context(request)
    rid = current_request_id()
    thread_id = payload.conversation_id or f"conv-{uuid.uuid4().hex[:16]}"
    config = {"configurable": {"thread_id": thread_id}}
    graph = request.app.state.chat_graph
    last_event_id = request.headers.get(HEADER_LAST_EVENT_ID)
    resuming = last_event_id is not None
    offset = int(last_event_id) if (last_event_id or "").isdigit() else 0

    if resuming:
        async for frame in _resume_stream(
            graph,
            config=config,
            thread_id=thread_id,
            rid=rid,
            payload=payload,
            request=request,
            offset=offset,
        ):
            yield frame
        return

    # 1) 限额：超限直接熔断，不再调用 LLM
    # 注意：session 获取也放在 try 内——连工厂/建连异常同样属基础设施故障，
    # 不得让审计/计量链路把用户对话打断（AGENTS §6.1 不静默，但也不得穿透）
    if settings.audit_enabled:
        try:
            async with get_sessionmaker()() as session:
                await _meter(session, settings=settings, scene=payload.scene, user_id=ctx.user_id)
        except QuotaExceededError as exc:
            yield _sse("error", {"msg": str(exc), "code": 429})
            return
        except SQLAlchemyError as exc:
            logger.warning(
                "计量库不可用，跳过限额判断 rid=%s err=%s", rid, type(exc).__name__, exc_info=True
            )

    # mode 由图装配时的开关决定（同一份 settings），因此可以在生成前如实标注
    mode = "llm" if settings.use_llm else "echo"
    yield _sse(
        "meta",
        {
            "conversation_id": thread_id,
            "request_id": rid,
            "scene": payload.scene,
            "user": ctx.user_name or "system",
            "mode": mode,
            "model": None,  # 服务端实际模型名要等回包 → done 帧补全
            "resumed": False,
            "history_len": None,  # 同上
        },
    )

    outcome = _TurnOutcome()
    frames = 0
    try:
        # 2) 执行图并逐 token 下发（checkpointer 自动持久化本轮状态）
        async for text in _stream_graph_tokens(
            graph, message=payload.message, scene=payload.scene, config=config, outcome=outcome
        ):
            frames += 1
            if frames <= offset:
                continue
            if await request.is_disconnected():  # 客户端断开：立即停止，不空转（§2.2）
                logger.info("client disconnected rid=%s at frame=%s", rid, frames)
                return
            yield _sse("delta", {"text": text}, event_id=frames)

        # 没有 token 分片时兜底分帧：echo 节点（USE_LLM=0 的链路自检/降级演练）不产生 LLM 分片回调，
        # 按固定粒度下发以保持“逐帧到达”的观感与断点续传锚点不变。
        if not outcome.streamed:
            for chunk in _chunks(outcome.reply):
                frames += 1
                if frames <= offset:
                    continue
                if await request.is_disconnected():
                    logger.info("client disconnected rid=%s at frame=%s", rid, frames)
                    return
                yield _sse("delta", {"text": chunk}, event_id=frames)
                await asyncio.sleep(_INTERVAL_S)
    except LlmError as exc:
        # 可能已下发部分 token：仍以 error 帧收尾，让前端明确提示失败（不留悬挂气泡）
        logger.warning("chat llm failed rid=%s: %s", rid, exc)
        yield _sse("error", {"msg": str(exc), "code": 502})
        return

    # 3) 留痕与计量（旁路能力，失败降级放行）
    await _record_turn(
        settings=settings,
        payload=payload,
        ctx_user_id=ctx.user_id,
        rid=rid,
        thread_id=thread_id,
        outcome=outcome,
    )

    yield _sse(
        "done",
        {
            "finish_reason": mode,
            "history_len": outcome.history_len,
            "frames": frames,
            "model": outcome.model,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "resumed": False,
        },
    )


@router.post("/chat", summary="流式对话（SSE；token 级流式 + 会话持久化 + 断点续传）")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """返回 text/event-stream。

    响应头显式禁用缓冲：本服务与 Java 网关侧都要保证逐帧输出
    （网关为 Servlet 输出流逐块 flush；两侧缺一不可）。
    """
    return StreamingResponse(
        _chat_stream(payload, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            HEADER_REQUEST_ID: current_request_id(),
        },
    )
