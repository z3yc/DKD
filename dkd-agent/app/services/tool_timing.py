"""本轮工具执行的耗时/结果规模采样（排期 4-1 会话详情 + 4-5 上下文治理）。

## 为什么单独一个 ContextVar 通道

`agent_message.tool_calls` 需要记录的不只是“调了哪个工具、传了什么”
（那由 LangGraph 的 AIMessage.tool_calls 给出），还要回答排障时最常问的两句：

- **这次工具查询慢不慢**（`durationMs`）——LLM 回答慢，是模型慢还是工具慢？
- **它到底返回了多少数据**（`resultRows` / `resultChars`）——模型说“没有数据”时，
  要能区分“工具真的零行”与“工具返回了 2000 行但被截断/没被引用”。

这些信息只在**工具真正执行时**可得（ToolNode 执行完就只剩 ToolMessage 正文），
所以用 ContextVar 在工具包装层采样，由 API 收尾时取走——不进 LangGraph state，
避免把“计时/规模”变成会随 checkpoint 持久化的会话字段（AGENTS 0-13 state 冻结约定）。

## 三条纪律

1. **旁路**：采样失败/缺失不得影响对话；`merge_tool_calls` 对不上时以
   LangGraph 的 tool_calls 为准，缺字段给 0/NULL（不编造耗时）。
2. **脱敏**：`argsDigest` 落库前过 `audit.mask_deep`（手机号/长数字脱敏），
   并截断到 300 字符——参数可能含货道/设备标识，详情页不需要全文。
3. **轻量**：包装后的工具必须保留 `name/description/args_schema` 与**异常语义**
   （异常继续抛给 ToolNode 的 `handle_tool_errors`），只加采样，不改行为。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from typing import Any

from langchain_core.tools import StructuredTool

from app.audit import mask_deep

logger = logging.getLogger("dkd.agent.services.tool_timing")

#: 参数摘要上限（`agent_message.tool_calls` 详情页展示用；不落全文，AGENTS §7.8）
MAX_ARGS_DIGEST_CHARS = 300
#: 单轮采样条数上限：异常情况下（工具被反复调用）也不会无限增长
MAX_RECORDS_PER_TURN = 32

_RECORDS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "dkd_tool_timing_records", default=None
)


def begin_turn() -> Token[list[dict[str, Any]] | None]:
    """开始一轮采样（返回 token 供 `end_turn` 复位）。"""
    return _RECORDS.set([])


def end_turn(token: Token[list[dict[str, Any]] | None]) -> None:
    """结束本轮采样并复位 contextvar（异常路径也要调用，避免泄漏到下一轮）。"""
    try:
        _RECORDS.reset(token)
    except (ValueError, LookupError):  # pragma: no cover —— 跨 task 误用时兜底
        _RECORDS.set(None)


def current_records() -> list[dict[str, Any]]:
    """取本轮已采样记录（副本；无采样上下文时返回空列表）。"""
    return [dict(record) for record in (_RECORDS.get() or [])]


def _append_record(record: dict[str, Any]) -> None:
    records = _RECORDS.get()
    if records is None:
        return  # 未开启采样（如其它入口直接调用工具）：不记录、不报错
    if len(records) >= MAX_RECORDS_PER_TURN:
        return
    records.append(record)


def args_digest(arguments: Mapping[str, Any] | None) -> str:
    """参数摘要：脱敏 + 截断（**不落原文**；详情页只用于“调的是什么”）。"""
    if not arguments:
        return ""
    try:
        masked = mask_deep(dict(arguments))
        text_value = json.dumps(masked, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text_value = str(arguments)
    if len(text_value) <= MAX_ARGS_DIGEST_CHARS:
        return text_value
    return text_value[:MAX_ARGS_DIGEST_CHARS] + "…"


def _result_rows(result: Any) -> int:
    """尽量从工具返回值推断“行数”（列表长度 / rows/items/data 等常见键 / 计数字段）。

    为什么必要：assistant 工具统一返回 JSON 字符串（`{"items": [...]}`），
    只靠字符数区分不了“1 行但很长”与“200 行”。
    不是所有工具都遵守同一形状，因此推断失败时宁可给 0，也不编一个像真的数字。
    """
    value = result
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return 0
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, Mapping):
        for key in ("rows", "items", "data", "results", "plans", "channels", "tasks"):
            nested = value.get(key)
            if isinstance(nested, (list, tuple)):
                return len(nested)
        for key in ("count", "total", "rowCount", "row_count"):
            number = value.get(key)
            if isinstance(number, int) and not isinstance(number, bool):
                return number
        return len(value)
    return 0


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def record_execution(
    *,
    name: str,
    arguments: Mapping[str, Any] | None,
    result: Any,
    status: str,
    duration_ms: int,
) -> None:
    """记录一次工具执行（包装层调用；也可由其它入口手工补录）。"""
    text_value = _result_text(result)
    _append_record(
        {
            "name": name,
            "argsDigest": args_digest(arguments),
            "durationMs": int(duration_ms),
            "status": status,
            "resultChars": len(text_value),
            "resultRows": _result_rows(result),
        }
    )


def _wrap_one(tool: Any) -> StructuredTool:
    """轻量包装：保留 name/description/args_schema，只加采样与计时。"""
    tool_name = str(getattr(tool, "name", "") or "")

    async def _coroutine(**kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await tool.ainvoke(kwargs)
        except Exception:
            duration_ms = int(round((time.perf_counter() - started) * 1000))
            record_execution(
                name=tool_name,
                arguments=kwargs,
                result=None,
                status="error",
                duration_ms=duration_ms,
            )
            raise  # 异常语义不变：交给 ToolNode 的 handle_tool_errors 转成结果消息
        duration_ms = int(round((time.perf_counter() - started) * 1000))
        record_execution(
            name=tool_name,
            arguments=kwargs,
            result=result,
            status="ok",
            duration_ms=duration_ms,
        )
        return result

    return StructuredTool.from_function(
        coroutine=_coroutine,
        name=tool_name,
        description=str(getattr(tool, "description", "") or ""),
        args_schema=getattr(tool, "args_schema", None),
    )


def wrap_tools(tools: Sequence[Any]) -> list[StructuredTool]:
    """包装 ToolNode 的工具集（只用于**执行**侧；bind_tools 仍用原始工具对象）。"""
    wrapped: list[StructuredTool] = []
    for tool in tools:
        if not getattr(tool, "name", None):
            logger.warning("工具缺少 name，跳过时序包装：type=%s", type(tool).__name__)
            continue
        wrapped.append(_wrap_one(tool))
    return wrapped


def merge_tool_calls(
    tool_calls: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把采样记录合并进 `agent_message.tool_calls`（**以 tool_calls 为准**）。

    合并规则：
    - 按名称顺序匹配（同一工具多次调用依次消费记录）；
    - 匹配到 → 补 `durationMs/status/resultChars/resultRows/argsDigest`；
    - 匹配不到 / 记录不足 → 数值字段给 0、status 给 None（不编造耗时）；
    - 多出来的记录（LangGraph 没看到的调用）**丢弃**：tool_calls 是权威口径。

    @return 新列表（不修改入参；供留痕与 `agent_message.tool_calls` 共用）
    """
    remaining = [dict(record) for record in records]
    merged: list[dict[str, Any]] = []
    for call in tool_calls:
        item = dict(call)
        item.setdefault("durationMs", 0)
        item.setdefault("status", None)
        item.setdefault("resultChars", 0)
        item.setdefault("resultRows", 0)
        name = item.get("name")
        for index, record in enumerate(remaining):
            if record.get("name") == name:
                matched = remaining.pop(index)
                for key in ("durationMs", "status", "resultChars", "resultRows", "argsDigest"):
                    if matched.get(key) is not None:
                        item[key] = matched[key]
                break
        merged.append(item)
    return merged


__all__ = [
    "MAX_ARGS_DIGEST_CHARS",
    "args_digest",
    "begin_turn",
    "current_records",
    "end_turn",
    "merge_tool_calls",
    "record_execution",
    "wrap_tools",
]
