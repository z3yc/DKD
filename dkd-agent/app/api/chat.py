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
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any, NamedTuple

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
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
from app.services import grounding, time_hint

router = APIRouter(prefix="/agent", tags=["chat"])
logger = logging.getLogger("dkd.agent.chat")

_CHUNK = 24
_INTERVAL_S = 0.03
HEADER_LAST_EVENT_ID = "Last-Event-ID"

# 存储类异常的类型名标记（详见 _is_storage_error）：
#   Operational/Interface —— psycopg（PG 挂断 / 事件循环不兼容）
#   DBAPI/SQLAlchemy     —— SQLAlchemy 包装层
#   Database/Integrity   —— sqlite3.DatabaseError（**文件损坏**，2026-10-01 由
#                           verify-3-9-drills.py --phase sqlite-corrupt 实测到）与
#                           sqlalchemy.exc.DatabaseError/IntegrityError
_STORAGE_ERROR_MARKERS = (
    "Operational",
    "Interface",
    "DBAPI",
    "SQLAlchemy",
    "Database",
    "Integrity",
)


def _is_storage_error(exc: BaseException) -> bool:
    """异常是否来自“会话存储”（checkpointer / 连接池 / 磁盘）。

    为什么必须抽成函数而不是写在 except 里：这个判断**对外决定用户看到哪句话**
    （503“会话存储暂时不可用” vs 500“内部错误”），对内决定排查方向，
    因此只能有一处定义。否则 API 的判断与演练脚本的判断会各自漂移，
    最后得到“演练说会归存储类、线上却报内部错误”这类最耗时间的假结论。
    """
    return any(marker in type(exc).__name__ for marker in _STORAGE_ERROR_MARKERS)


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


# ----------------------------------------------------------------------------------
# 模型内部标记的剥离（2026-10-01 实测：回复里出现过 `<｜｜DSML｜｜ calls>`）
# ----------------------------------------------------------------------------------
#
# 这是**展示层缺陷**，不是“模型说错话”：DeepSeek 系列在流式模式下偶尔会把工具调用的
# 内部标记当正文吐出来（实测多轮追问时出现），用户看到的就是一串乱码。
# 处理原则：**在边界处剥掉**（流式分片与落库回复都过同一个函数），并且**静默剥掉不替换**——
# 替换成“（内容已过滤）”会让用户以为我们说漏了什么，而这些标记对用户本来就没有意义。
#
# 为什么用“标记形态”而不是穷举关键词：不同实现用过 `<|tool▁calls▁begin|>`、`<｜｜DSML｜｜…>`
# 等形态，共同特征是**用竖线/全角竖线包着的大写标识 + 可能的尖括号**。匹配形态更耐久。
_MODEL_MARKUP_RE = re.compile(
    # 允许 `</` 闭合成对形态（实测漏过 `</｜｜DSML｜｜ parameter>`）；
    # 关键字放宽到 calls/parameter/invoke/antml —— 都是工具调用的内部语法，对用户无意义。
    r"</?[\uff5c|]{0,2}\s*[A-Za-z_]{0,24}\s*[\uff5c|]{0,2}\s*"
    r"(?:DSML|tool[\u2581_]?calls?|function[\u2581_]?calls?|parameter|invoke|antml)"
    r"[^<>]{0,300}?[\uff5c|]{0,2}>",
    re.IGNORECASE,
)
_MODEL_MARKUP_BARE_RE = re.compile(
    r"</?[\uff5c|]{1,2}\s*(?:DSML|tool[\u2581_]?calls?|parameter|invoke)[^<>]{0,120}",
    re.IGNORECASE,
)


class ModelMarkupFilter:
    """**流式**剥标记：标记可能被分片切开，所以必须把“疑似标记的尾巴”留在缓冲里。

    为什么按分片替换不行（实测）：`<｜｜DSML｜｜ calls>` 会分几次到达
    （`<｜｜DS` + `ML｜｜ calls` + `>`），逐分片正则永远匹配不到，用户照样看到乱码。
    所以按“最右一个未闭合的 `<`”切一刀：**能判定的立刻吐出，判不了的留在缓冲**；
    流结束时调 `flush()` 收尾（保证不丢正文）。
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        self._buf += text or ""
        if "<" not in self._buf:
            out, self._buf = self._buf, ""
            return out
        idx = self._buf.rfind("<")
        tail = self._buf[idx:]
        # 尾巴里已经有 `>`（标记闭合了）或长到不可能是标记 → 全部可判定，一起处理
        if ">" in tail or len(tail) > 200:
            out, self._buf = _strip_model_markup(self._buf), ""
            return out
        head = self._buf[:idx]
        self._buf = tail
        return _strip_model_markup(head)

    def flush(self) -> str:
        """流结束：把缓冲里剩下的内容吐出来（真标记已在 `feed` 里被剥掉）。"""
        out, self._buf = _strip_model_markup(self._buf), ""
        return out


def _strip_model_markup(text: str) -> str:
    """剥掉模型内部标记（流式与落库都调用它，保证“用户看到的”与“留痕记的”一致）。"""
    if not text or "<" not in text:
        return text
    cleaned = _MODEL_MARKUP_RE.sub("", text)
    cleaned = _MODEL_MARKUP_BARE_RE.sub("", cleaned)
    return cleaned


def _sse(event: str, data: dict[str, object], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str) -> list[str]:
    return [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)] or [""]


def _last_human_text(messages: object) -> str:
    """本轮用户的原话（取证与“用户自己给的数字”都要用它）。"""
    for msg in reversed(list(messages or [])):  # type: ignore[arg-type]
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def _current_turn_messages(messages: object) -> list[Any]:
    """只取**本轮**的消息（最后一次 HumanMessage 之后的部分）。

    为什么必须限定本轮（2026-10-01 修）：`final_state["messages"]` 是**整个会话**
    （checkpointer 恢复历史 + add_messages 累加），而留痕的语义是“**这一轮**回答了哪个问题、
    基于哪几次查询”。不限定就会出现两个静默错误：

      1. **验收假绿**：多轮追问的断言是“第 3 轮带着焦点设备去查了异常”，
         而第 2 轮的那次 `find_devices`/`list_device_anomalies` 会被算进第 3 轮——
         即使第 3 轮根本没查，断言也会通过；
      2. **前端显示陈旧卡片**：某一轮调过 `build_device_diagnosis` 后，
         后续每一轮（哪怕只说了句“谢谢”）的 done 帧都会**再带一次**那张结论卡。
    """
    items = list(messages or [])  # type: ignore[arg-type]
    for index in range(len(items) - 1, -1, -1):
        if isinstance(items[index], HumanMessage):
            return items[index + 1 :]
    return items


class _TurnSnapshot(NamedTuple):
    """断线续传要从 checkpoint 取回的东西（与正常路径同一套提取函数，避免两边漂移）。"""

    reply: str
    history_len: int
    model: str | None
    conclusion: dict | None
    calibers: list[dict[str, Any]]
    grounding: dict[str, Any]
    focus_inner_code: str


async def _load_turn_from_checkpoint(graph: object, config: dict) -> _TurnSnapshot:
    """断线重连：从 checkpoint 读回上一轮回复、历史长度、实际模型与结构化字段。

    不重跑图（避免重复追加上下文）。

    为什么连模型一起读回：meta/done 帧要如实标注本轮是不是走了真 LLM（`mode`），
    而续传路径不重跑图，模型名只能从 checkpoint 取。
    """
    snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
    values = getattr(snapshot, "values", None) or {}
    model = values.get("model")
    messages = values.get("messages")
    turn = _current_turn_messages(messages)
    return _TurnSnapshot(
        reply=str(values.get("reply", "")),
        history_len=len(values.get("messages", [])),
        model=(str(model) if model else None),
        conclusion=_extract_conclusion(turn),
        calibers=_extract_calibers(turn),
        # 保真核对的证据取**整个会话**（引用上一轮的查询结果不算无出处），
        # 而用户原话要取整段会话里最后一条（turn 切片里已经没有 HumanMessage 了）
        grounding=_extract_grounding(messages, user_message=_last_human_text(messages)),
        focus_inner_code=str(values.get("focus_inner_code") or ""),
    )


def _mode_of(model: str | None) -> str:
    """如实标注本轮回复来源（M1 起 DKD_AGENT_USE_LLM=1）。

    为什么不能硬编码 "echo"（2026-09-21 M1 验收发现）：echo 节点写 `model="echo"`，
    LLM 节点写服务端实际返回的模型名；若 meta 永远写 echo，审计与排障会把真 LLM 调用误判为链路回显，
    也让排期 0-12 的成本归因失去依据。
    """
    return "echo" if not model or model == "echo" else "llm"


# ----------------------------------------------------------------------------------
# 结构化诊断结论随 done 帧下发（2-4 的“前端可渲染置信度条”）
# ----------------------------------------------------------------------------------
#
# 为什么从本轮 `messages` 里捞、而不是新增 graph state 字段：0-13 冻结了 state schema
# （新增字段要走兼容性评审），而 `ToolMessage` 本来就在 messages 里，且只在配了工具时出现。
# 只取**最后一个**结论（多轮工具调用时以最终那次为准）。
#
# ⚠️ 发给前端的是**投影**，不是原样转发（实测教训：10 条证据各带 detail 的原始结论约 5~6KB，
# 超过了初版的 4000 字符护栏 → 结论卡片静默不渲染）。为什么不把裁剪放前端：
# done 帧要经 Java 网关逐字节转发，帧越大越容易在链路上被拖慢/截断；而逐条证据（含 detail）
# 是**给模型看的**，卡片只需要档位/依据/未知项/动作与证据条数。所以在这里投影，并在测试里
# 断言“detail 不得出现在帧里”。
_CONCLUSION_TOOL = "build_device_diagnosis"
# 输入侧护栏：ToolMessage 超过这个长度就不解析（防止把整坨内容塞进 done 帧）
_MAX_RAW_CONCLUSION_CHARS = 20000
# 投影里的列表上限（卡片是给人扫读的，不是完整报告）
_MAX_PROJECTED_ITEMS = 10


def _compact_conclusion(payload: dict) -> dict:
    """把工具返回的完整结论投影成**给前端卡片用的字段**（顺序稳定、体量有界）。"""
    evidence = [e for e in (payload.get("evidence") or []) if isinstance(e, dict)]
    return {
        "innerCode": payload.get("innerCode"),
        "confidence": payload.get("confidence"),
        "confidenceBasis": payload.get("confidenceBasis"),
        "cannotConclude": payload.get("cannotConclude"),
        "unknowns": [str(u) for u in (payload.get("unknowns") or [])][:_MAX_PROJECTED_ITEMS],
        "recommendedActions": [str(a) for a in (payload.get("recommendedActions") or [])][
            :_MAX_PROJECTED_ITEMS
        ],
        "evidenceCount": len(evidence),
        "evidenceKinds": sorted({str(e.get("kind")) for e in evidence})[:_MAX_PROJECTED_ITEMS],
        "caliber": payload.get("caliber"),
    }


def _extract_tool_calls(messages: object) -> list[dict[str, object]]:
    """本轮调用过哪些工具、带了什么参数（写进留痕；也是验收的确定性信号）。

    为什么要记这个：
      1. **审计**：只记“回答了”不够，还要能回答“这个回答是基于哪几次查询得出的”
         （尤其当模型把数字说错时，第一件事就是核对它到底查了什么）；
      2. **验收**：多轮追问（2-3）要靠“第 3 轮到底有没有带着焦点设备去查”来判定，
         而**解析模型的回复文本**是不可靠的（实测它会把结果改写、甚至拼接两个窗口的结论）。
         工具调用是结构化的，判得准、可回归。
    参数按需截断（防止把大参数塞进留痕）。
    """
    calls: list[dict[str, object]] = []
    for msg in list(messages or []):  # type: ignore[arg-type]
        for call in getattr(msg, "tool_calls", None) or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
            if not name:
                continue
            text_args = json.dumps(args, ensure_ascii=False)
            calls.append(
                {"name": str(name), "args": text_args[:400] + ("…" if len(text_args) > 400 else "")}
            )
    return calls


def _extract_conclusion(messages: object) -> dict | None:
    """取本轮最后一次 `build_device_diagnosis` 的结论（没有/不可用 → None）。

    三种情况都返回 None，宁可不渲染也不给半个结论：本轮没调该工具 / 内容不是合法 JSON /
    缺 `confidence`（说明不是本工具的返回）。
    """
    for msg in reversed(list(messages or [])):  # type: ignore[arg-type]
        if getattr(msg, "name", None) != _CONCLUSION_TOOL:
            continue
        content = getattr(msg, "content", "")
        text = content if isinstance(content, str) else str(content)
        if len(text) > _MAX_RAW_CONCLUSION_CHARS:
            logger.info("诊断结论原始内容过长（%s 字符），本轮不下发结论卡片", len(text))
            return None
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
        if isinstance(payload, dict) and "confidence" in payload:
            return _compact_conclusion(payload)
        return None
    return None


def _extract_calibers(messages: object) -> list[dict[str, Any]]:
    """本轮工具返回的权威口径（FIX-27）：窗口与计数，供前端展示与留痕/验收判定。

    为什么必须是“工具算的”而不是“模型说的”：实测模型会改写/拼接工具结果（把 90 天窗口的 0 条
    套到 1825 天窗口上），而窗口与计数是工具自己算出来的事实，不存在被措辞污染的可能。
    """
    return grounding.collect_calibers(messages)


def _extract_grounding(messages: object, *, user_message: str, reply: str = "") -> dict[str, Any]:
    """回复里的数字有没有出处（FIX-27 的**检测**面，不是修复面）。

    ⚠️ `messages` 要传**整个会话**（不只是本轮）：模型的回答里引用**上一轮**查到的数字
    是正当的（实测：“和上一轮 90 天窗口的结果不同…90 天窗口内是 0 条”），
    只看本轮会把这句真话标成“无出处”（误报）。留痕里的 `dataCaliber` 仍然是**本轮**的
    （那是“这次查了什么”），两者语义不同、故意不共用一份输入。

    为什么只“标出”不“改写”：改写用户看到的内容比说错更糟——用户会以为模型没说错、
    也失去了“这个模型在这件事上不可靠”的信号。标出来 + 给出权威口径，用户自己能判断。
    """
    evidence = grounding.build_evidence(messages, user_message=user_message)
    report = grounding.check_reply_numbers(
        reply or _last_assistant_text(messages), evidence=evidence
    )
    if not report.grounded:
        # 只记数字，不记回复正文（AGENTS §6.1：日志不出完整正文）
        logger.warning(
            "回复含无出处的数字（以口径帧为准）：checked=%s ungrounded=%s",
            report.checked,
            report.ungrounded,
        )
    return grounding.as_frame(report)


def _last_assistant_text(messages: object) -> str:
    """本轮最后一条助手正文（续传路径拿不到 `outcome.reply` 时的兜底）。"""
    for msg in reversed(list(messages or [])):  # type: ignore[arg-type]
        if isinstance(msg, ToolMessage):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            return content
    return ""


class _TurnOutcome:
    """一轮生成的收尾信息（流式期间逐步填充，供 done 帧与留痕/计量共用）。

    为什么需要一个可变对象跨生成器传递：`async for` 只能拿到 yield 出来的分片，
    而图执行完的最终 state（reply/model/tokens/messages）需要另开一条通道回传；
    用带 __slots__ 的小类比 dict 更不容易写错字段名。
    """

    __slots__ = (
        "reply",
        "streamed",
        "history_len",
        "model",
        "tokens_in",
        "tokens_out",
        "conclusion",
        "tool_calls",
        "calibers",
        "grounding",
        "focus_inner_code",
    )

    def __init__(self) -> None:
        self.reply = ""
        self.streamed = ""
        self.history_len = 0
        self.model: str | None = None
        self.tokens_in = 0
        self.tokens_out = 0
        # 结构化诊断结论（2-4）：仅当本轮调用了 build_device_diagnosis 时非空
        self.conclusion: dict | None = None
        # 本轮的工具调用（名称+参数，已截断）：写进留痕，供审计与该验的确定性判定
        self.tool_calls: list[dict[str, object]] = []
        # 本轮工具返回的权威口径（FIX-27）：窗口/计数，不经模型措辞
        self.calibers: list[dict[str, Any]] = []
        # 回复数字的出处核对结果（FIX-27）
        self.grounding: dict[str, Any] = {}
        # 本轮结束时的诊断焦点（多轮问诊对用户可见，2-3）
        self.focus_inner_code = ""


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
    markup = ModelMarkupFilter()
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
        text = markup.feed(_chunk_text(token))
        if text:
            outcome.streamed += text
            yield text

    tail = markup.flush()  # 收尾：把缓冲里的正文吐出来（真标记已剥掉）
    if tail:
        outcome.streamed += tail
        yield tail
    outcome.reply = _strip_model_markup(str(final_state.get("reply", ""))) or outcome.streamed
    outcome.history_len = len(final_state.get("messages", []))
    model = final_state.get("model")
    outcome.model = str(model) if model else None
    outcome.tokens_in = int(final_state.get("tokens_in", 0) or 0)
    outcome.tokens_out = int(final_state.get("tokens_out", 0) or 0)
    # ⚠️ 结构化字段一律只看**本轮**（见 `_current_turn_messages`：整会话会把上几轮算进来，
    # 既会让“第 3 轮带着焦点设备去查”假绿，也会给前端发上一轮的陈旧结论卡）
    turn = _current_turn_messages(final_state.get("messages"))
    outcome.conclusion = _extract_conclusion(turn)
    outcome.tool_calls = _extract_tool_calls(turn)
    outcome.calibers = _extract_calibers(turn)
    outcome.grounding = _extract_grounding(
        final_state.get("messages"),
        user_message=_last_human_text(final_state.get("messages")),
        reply=outcome.reply,
    )
    outcome.focus_inner_code = str(final_state.get("focus_inner_code") or "")


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
    snap = await _load_turn_from_checkpoint(graph, config)
    if not snap.reply:
        yield _sse("error", {"msg": "会话不存在或无可续传内容", "code": 404})
        return
    yield _sse(
        "meta",
        {
            "conversation_id": thread_id,
            "request_id": rid,
            "scene": payload.scene,
            "user": parse_user_context(request).user_name or "system",
            "mode": _mode_of(snap.model),
            "model": snap.model,
            "resumed": True,
            "history_len": snap.history_len,
        },
    )
    frames = 0
    for index, chunk in enumerate(_chunks(snap.reply), start=1):
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
            "finish_reason": _mode_of(snap.model),
            "history_len": snap.history_len,
            "frames": frames,
            "model": snap.model,
            "resumed": True,
            "conclusion": snap.conclusion,
            # 续传与正常路径必须给出**同样**的口径/保真/焦点字段：只给正常路径会给前端
            # 留下一类“断线后提示条消失”的偏差（只有断线时才能复现）。
            "dataCaliber": snap.calibers,
            "grounding": snap.grounding,
            "focusInnerCode": snap.focus_inner_code,
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
                input_context={
                    "message": payload.message,
                    "thread_id": thread_id,
                    # 本轮调用过哪些工具（名称+截断后的参数）：审计要能回答
                    # “这个回答是基于哪几次查询得出的”，而不是只知道“它回答了”
                    "toolCalls": outcome.tool_calls,
                    "toolCallCount": len(outcome.tool_calls),
                    # 那几次查询**实际生效的口径**（窗口来自工具而非模型措辞，FIX-27/28）：
                    # 有了它，“模型说 5 年、实际查了 90 天”这种偏差在留痕里一眼可见。
                    "dataCaliber": outcome.calibers,
                    # 回复里无出处的数字（保真核对）：用于事后统计这类失败的发生率
                    "grounding": outcome.grounding,
                },
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
    # FIX-28：把**用户原话**里的时间窗口注入本轮上下文（工具层与提示词层读同一个值，
    # 避免“提示词一个口径、工具另一个口径”）。解析不出或有两个窗口时是 None（不猜）。
    window_token = time_hint.set_window_hint_days(time_hint.parse_window_days(payload.message))
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
    except Exception as exc:  # noqa: BLE001 —— 编排层兜底，见下方注释
        # 为什么需要这个“兜底 except”（2026-10-01 由 3-9 演练撞出来）：
        #
        # 此前只抓了 `LlmError`，**基础设施异常会直接抛穿 SSE 生成器**：
        # 演练中终止 PG 连接后再发一轮，服务端抛 psycopg.OperationalError
        # （`consuming input failed: server closed the connection unexpectedly`），
        # 流当场断开 → 客户端拿到的是 httpx.ReadError（断流），
        # **既没有 error 帧、也没有任何可展示的提示**，前端只能表现为“气泡突然不动了”。
        #
        # 这违反了 AGENTS §6.3「工具执行异常由编排层兜底转为结果消息，**不抛穿 LangGraph 循环**」
        # 的同一条精神：编排层（这里就是 SSE 生成器）必须把异常变成**可展示的结果**。
        #
        # 注意：这里不区分“会话存储故障”与“其它故障”去改变**行为**（都是本轮失败 + 可重试），
        # 只在**文案**上区分——运维拿到 rid 去查日志即可，用户不需要知道是哪个组件坏了。
        kind = type(exc).__name__
        storage_related = _is_storage_error(exc)
        logger.warning(
            "chat aborted by infra error rid=%s type=%s storage_related=%s",
            rid,
            kind,
            storage_related,
            exc_info=True,
        )
        yield _sse(
            "error",
            {
                "msg": (
                    "会话存储暂时不可用，本轮对话未保存；请稍后重试"
                    "（若持续出现请联系运维，提供请求号 " + rid + "）"
                    if storage_related
                    else "智能体服务内部错误，本轮对话已中断；请稍后重试（请求号 " + rid + "）"
                ),
                "code": 503 if storage_related else 500,
                "retryable": True,
            },
        )
        return
    finally:
        # 上下文必须复位：不复位会把本轮窗口泄漏给同一进程里后续请求（尤其异常路径）
        time_hint.reset_window_hint_days(window_token)

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
            # 结构化诊断结论（2-4）：前端据此画置信度条；没有该工具调用时严格为 None
            "conclusion": outcome.conclusion,
            # 权威口径（FIX-27）：窗口/计数由**工具**算出，不受模型措辞影响；
            # 前端用它与回复里的数字对照显示
            "dataCaliber": outcome.calibers,
            # 回复数字的出处核对（FIX-27）：grounded=false 时前端提示“以口径帧为准”
            "grounding": outcome.grounding,
            # 多轮问诊的焦点（2-3）：让用户看得见“助手记住的是哪台”（后端早已生效，前端此前不可见）
            "focusInnerCode": outcome.focus_inner_code,
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
