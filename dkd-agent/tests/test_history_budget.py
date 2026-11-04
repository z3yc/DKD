"""4-5 上下文预算裁剪测试（chat_graph.trim_history + respond_with_llm 接入）。

全部离线（不调 LLM、不连库）：消息用 langchain 的消息对象构造；
`respond_with_llm` 用假模型客户端验证“拼 messages 前确实先裁剪、system 仍在最前”。

判据（对应 task-22 验收）：
- 短会话**一个字节都不改**（dropped=0、无截断日志）；
- 长会话（50 轮）超预算会被丢最旧消息，且日志含「会话上下文截断」；
- system 消息永远保留在最前、不参与裁剪；
- 更早的工具结果被摘要化（截断 + 标注「历史工具结果已摘要」）；
- 最近 N 条原样保留。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import get_settings
from app.graphs import chat_graph
from app.graphs.chat_graph import _estimate_messages_tokens, trim_history


def _long_rounds(rounds: int) -> list[Any]:
    """构造 rounds 轮（用户问 + 助手答）的会话历史。"""
    messages: list[Any] = []
    for index in range(rounds):
        messages.append(HumanMessage(content=f"第{index}个问题：" + "请查库存与销量" * 6))
        messages.append(AIMessage(content=f"第{index}个回答：" + "建议补货若干件" * 6))
    return messages


def test_short_history_is_untouched():
    messages = [
        SystemMessage(content="你是帝可得助手"),
        HumanMessage(content="你好"),
        AIMessage(content="你好，有什么可以帮你"),
    ]

    trimmed, dropped = trim_history(messages, budget_tokens=8000, keep_recent=12)

    assert dropped == 0
    assert trimmed == messages
    assert [message.content for message in trimmed] == [message.content for message in messages]


def test_zero_budget_disables_trimming():
    messages = _long_rounds(20)
    trimmed, dropped = trim_history(messages, budget_tokens=0)
    assert dropped == 0 and trim_history(messages, budget_tokens=-1)[0] == messages
    assert trimmed == messages


def test_long_session_drops_oldest_and_keeps_system_first(caplog):
    rounds = 50
    messages = [SystemMessage(content="你是帝可得助手"), *_long_rounds(rounds)]
    keep_recent = 12

    with caplog.at_level("WARNING", logger="dkd.agent.chat_graph"):
        trimmed, dropped = trim_history(
            messages, budget_tokens=300, keep_recent=keep_recent, tool_result_chars=40
        )

    assert dropped > 0, "50 轮长会话必然超预算"
    assert _estimate_messages_tokens(trimmed) <= 300 or len(trimmed) == 1 + keep_recent
    assert isinstance(trimmed[0], SystemMessage)
    assert trimmed[0].content == "你是帝可得助手", "system 不参与裁剪"
    # 最近 keep_recent 条原样保留（对象同一性 + 正文一致）
    assert len(trimmed) >= 1 + keep_recent
    assert [message.content for message in trimmed[-keep_recent:]] == [
        message.content for message in messages[-keep_recent:]
    ]
    assert any("会话上下文截断" in record.getMessage() for record in caplog.records)
    warning = next(
        record.getMessage() for record in caplog.records if "会话上下文截断" in record.getMessage()
    )
    assert "budget=300" in warning and f"dropped={dropped}" in warning


def test_old_tool_result_is_summarized_and_marked(caplog):
    long_tool_result = "库存明细：" + "货道 1-1 现库存 0 容量 10；" * 120
    messages: list[Any] = [
        SystemMessage(content="你是帝可得助手"),
        HumanMessage(content="查一下库存"),
        AIMessage(content="我来查"),
        ToolMessage(content=long_tool_result, tool_call_id="call-1", name="get_machine_inventory"),
        *_long_rounds(10),
    ]
    # 预算取“摘要前的估算 - 一小段”：保证只有把工具结果摘要化才可能装得下
    before = _estimate_messages_tokens(messages)
    budget = before - 60

    with caplog.at_level("WARNING", logger="dkd.agent.chat_graph"):
        trimmed, dropped = trim_history(
            messages, budget_tokens=budget, keep_recent=12, tool_result_chars=40
        )

    tool_messages = [message for message in trimmed if isinstance(message, ToolMessage)]
    assert tool_messages, "旧工具消息仍在（摘要化而非删除）"
    summary = tool_messages[0].content
    assert "历史工具结果已摘要" in summary
    assert len(summary) < len(long_tool_result), "正文必须被截断"
    assert dropped == 0, "摘要后已装进预算，不应再丢消息"
    assert _estimate_messages_tokens(trimmed) <= budget
    assert any("会话上下文截断" in record.getMessage() for record in caplog.records)


def test_recent_tool_result_is_not_summarized():
    messages = [
        HumanMessage(content="查库存"),
        ToolMessage(content="很短的最近工具结果", tool_call_id="c1", name="t"),
        AIMessage(content="结论"),
    ]
    # 最近 keep_recent 条全部原样（预算足够）
    trimmed, dropped = trim_history(messages, budget_tokens=8000, keep_recent=12)
    assert dropped == 0
    assert isinstance(trimmed[1], ToolMessage)
    assert trimmed[1].content == "很短的最近工具结果"


async def test_respond_with_llm_trims_before_building_messages(monkeypatch, caplog):
    captured: dict[str, Any] = {}

    class _FakeClient:
        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            captured["messages"] = messages
            return AIMessage(content="好的")

    def _fake_model(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr(chat_graph, "build_chat_model", _fake_model)
    # 把预算调小，让 50 轮（≈4800 tokens）必然触发裁剪；断言的是“接入点存在”，
    # 不是默认 8000 这个数值本身。
    monkeypatch.setenv("DKD_AGENT_HISTORY_TOKEN_BUDGET", "400")
    get_settings.cache_clear()
    state = {"messages": _long_rounds(50), "scene": 1, "focus_inner_code": ""}

    with caplog.at_level("WARNING", logger="dkd.agent.chat_graph"):
        result = await chat_graph.respond_with_llm(state, tools=None)

    assert result["reply"] == "好的"
    sent = captured["messages"]
    assert isinstance(sent[0], SystemMessage), "system 必须仍在最前"
    assert len(sent) < 1 + len(state["messages"]), "历史被裁剪"
    assert len(sent) >= 1 + 1, "至少保留 system + 最近消息"
    assert any("会话上下文截断" in record.getMessage() for record in caplog.records)


async def test_respond_with_llm_short_history_is_unchanged(monkeypatch):
    captured: dict[str, Any] = {}

    class _FakeClient:
        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            captured["messages"] = messages
            return AIMessage(content="好的")

    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **k: _FakeClient())
    history = [HumanMessage(content="第一句"), AIMessage(content="第一答")]
    state = {"messages": history, "scene": 1, "focus_inner_code": ""}

    await chat_graph.respond_with_llm(state, tools=None)

    sent = captured["messages"]
    assert isinstance(sent[0], SystemMessage)
    assert [message.content for message in sent[1:]] == ["第一句", "第一答"]
