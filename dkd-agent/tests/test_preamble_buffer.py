"""流式前导缓冲的单元测试（FIX-26）。

缺陷原貌：模型在决定调工具前会先输出一句前言（实测英文 `I'll check the device's history.`），
流式模式下它被逐 token 推给用户，造成中英混排碎片——没有报错，只能靠人看见。

这批用例锁三件事：
1. 这次调用**要调工具** → 已缓冲的前言整段丢弃（用户看不到）；
2. 这次调用**是正常回答** → 到阈值或流结束必须吐出来（不能把回答吞掉）；
3. 阈值可关闭（0）且关闭时行为与修复前一致（首字最快，接受前言漏出）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk

import app.api.chat as chat
from app.api.chat import _PreambleBuffer, _TurnOutcome


class _FakeGraph:
    """按给定的 (mode, chunk) 序列回放 astream 事件。"""

    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events

    async def astream(self, *_args: object, **_kwargs: object) -> AsyncIterator[tuple[str, Any]]:
        for event in self._events:
            yield event


def _chunk(text: str, *, step: int = 1, tools: bool = False) -> tuple[str, Any]:
    token = AIMessageChunk(
        content=text,
        id=f"msg-{step}",
        tool_call_chunks=(
            [{"name": "list_device_anomalies", "args": "", "id": "call-1", "index": 0}]
            if tools
            else []
        ),
    )
    return ("messages", (token, {"langgraph_node": "respond", "langgraph_step": step}))


def _values(state: dict[str, Any]) -> tuple[str, Any]:
    return ("values", state)


def _state(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [],
        "reply": "",
        "model": "fake",
        "tokens_in": 0,
        "tokens_out": 0,
        "focus_inner_code": "",
    }
    base.update(extra or {})
    return base


@pytest.fixture(autouse=True)
def _fixed_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定阈值 40：避免本地 .env 的 DKD_AGENT_PREAMBLE_BUFFER_CHARS 让用例漂移。"""
    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(preamble_buffer_chars=40))


async def _collect(events: list[tuple[str, Any]]) -> tuple[list[str], _TurnOutcome]:
    outcome = _TurnOutcome()
    graph = _FakeGraph(events)
    chunks = [
        text
        async for text in chat._stream_graph_tokens(
            graph, message="A1000001 不出货", scene=3, config={}, outcome=outcome
        )
    ]
    return chunks, outcome


async def test_preamble_is_dropped_when_llm_calls_tools() -> None:
    chunks, outcome = await _collect(
        [
            _chunk("I'll check the history. ", step=1),
            _chunk("", step=1, tools=True),
            _values(_state({"reply": ""})),
        ]
    )
    assert chunks == [], "要调工具那一轮的前言不得下发"
    assert outcome.streamed == ""


async def test_normal_answer_is_flushed_at_stream_end() -> None:
    chunks, outcome = await _collect(
        [
            _chunk("结论如上。", step=1),
            _values(_state({"reply": "结论如上。"})),
        ]
    )
    assert "".join(chunks) == "结论如上。"
    assert outcome.streamed == "结论如上。"


async def test_long_answer_is_released_once_threshold_crossed() -> None:
    """超过阈值即放行：不能为了挡一句前言把整段回答扣到结束。"""
    long_text = (
        "这是一段明显超过四十个字符阈值的正常回答内容，用户应当尽快看到它，"
        "而不是等模型把整段话都生成完。"
    )
    assert len(long_text) > 40
    chunks, _ = await _collect(
        [
            _chunk(long_text, step=1),
            _values(_state({"reply": long_text})),
        ]
    )
    assert "".join(chunks) == long_text


async def test_buffer_disabled_passes_text_through() -> None:
    buf = _PreambleBuffer(0)
    assert buf.feed("我先查一下。", tools_pending=False) == ["我先查一下。"]
    assert buf.finish(tools_pending=True) == []


def test_buffer_drops_everything_after_tools_pending() -> None:
    buf = _PreambleBuffer(24)
    assert buf.feed("I'll check", tools_pending=False) == []
    assert buf.feed(" the history.", tools_pending=True) == []
    assert buf.feed("more text", tools_pending=True) == []
    assert buf.finish(tools_pending=True) == []


def test_buffer_release_is_irreversible_and_documented() -> None:
    """已放行的文字收不回来：再次出现工具调用时只丢弃**还没发出去的**部分。"""
    buf = _PreambleBuffer(4)
    assert buf.feed("一二三四五", tools_pending=False) == ["一二三四五"]
    assert buf.feed("后续前言", tools_pending=True) == []
    assert buf.finish(tools_pending=True) == []


def test_buffer_release_keeps_original_chunk_granularity() -> None:
    """放行时按**原始分片**交出，而不是拼成一大帧——否则 token 级流式会退化。"""
    buf = _PreambleBuffer(3)
    assert buf.feed("一", tools_pending=False) == []
    assert buf.feed("二", tools_pending=False) == []
    assert buf.feed("三", tools_pending=False) == ["一", "二", "三"]
    assert buf.feed("四", tools_pending=False) == ["四"]  # 已放行，后续直通
