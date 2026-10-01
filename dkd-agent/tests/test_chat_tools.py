"""助手工具调用循环的回归测试（2026-10-01 补齐智能功能）。

## 这里测什么、为什么不能只靠“手动问一遍”

助手从“只会说我看不了数据”变成“能真取数”之后，新增的失效面全是**静默**的：

- 工具循环写错 → 模型反复调工具（烧 token）或永远不给答案；
- 忘记累计 token → 成本计量（0-12）**系统性低估**，且没有任何报错；
- 把写工具误加进工具集 → 模型可以改业务数据（AGENTS §7.3/§7.9 红线）；
- 模型不支持函数调用 → 每条消息都 502（或更糟：静默降级后开始编数）。

这些都必须用断言锁住。本文件**不连数据库**（AGENTS §8）：用桩工具 + 桩模型，
真实工具的真库行为由 `docs/scripts/verify-assistant-tools.py` 另行验证。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

import app.graphs.chat_graph as chat_graph
from app.checkpoint import SqliteCheckpointStore
from app.graphs.chat_graph import MAX_TOOL_ROUNDS, build_chat_graph
from app.tools.assistant_tools import build_assistant_tools


class _Args(BaseModel):
    inner_code: str = ""


def _stub_tool(
    *,
    name: str = "get_machine_inventory",
    result: str | None = None,
    error: Exception | None = None,
) -> StructuredTool:
    """桩工具：不碰数据库，只回一个可识别的 JSON。"""

    async def _run(inner_code: str = "") -> str:
        if error is not None:
            raise error
        return (
            result
            if result is not None
            else json.dumps(
                {"innerCode": inner_code or "A1000001", "channelCount": 1}, ensure_ascii=False
            )
        )

    return StructuredTool.from_function(
        coroutine=_run, name=name, description="桩工具", args_schema=_Args
    )


class _ToolCallingFake(GenericFakeChatModel):
    """会先要求调工具、再给最终答案的假模型。

    为什么继承 GenericFakeChatModel 而不是自己写一个：需要真实的**流式分片**行为
    （`astream(stream_mode="messages")` 依赖它），自己写容易测出“假通过”。
    只补上 `bind_tools`：基类默认抛 NotImplementedError，而图在有工具时一定会调它。
    """

    def bind_tools(self, _tools: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        return self


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


async def _run_graph(
    monkeypatch, tmp_path, *, messages: list[AIMessage], tools: list[Any]
) -> tuple[dict[str, Any], Any]:
    """构建图并跑一轮，返回 (最终 state, 模型收到的调用次数)。"""
    fake = _ToolCallingFake(messages=iter(messages))
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: fake)

    store = SqliteCheckpointStore(tmp_path / "cp.db")
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=True, tools=tools)
        config = {"configurable": {"thread_id": "t-1"}}
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "A1000001 那台机器库存怎么样"}], "scene": 2},
            config=config,
        )
        state = await graph.aget_state(config)
        return state.values, fake
    finally:
        await store.close()


# ------------------------------------------------------------------------------------
# 红线：工具集里不能有任何写操作
# ------------------------------------------------------------------------------------


def test_assistant_tools_are_read_only():
    """工具集**只读**——这是 AGENTS §7.3/§7.9 的红线，用反射断言而不是靠人看。

    为什么较真：模型一旦能写，就等于把“人工确认”这一环去掉了，
    而首期明确要求所有写操作 human-in-the-loop（放量需要 Phase 4 评审）。
    """
    names = {t.name for t in build_assistant_tools()}
    assert names == {
        "list_restock_suggestions",
        "get_machine_inventory",
        "list_inflight_work_orders",
        "get_sales_summary",
    }
    forbidden = ("create", "confirm", "adjust", "skip", "pause", "resume", "update", "delete")
    for name in names:
        assert not any(word in name for word in forbidden), f"工具 {name} 看起来是写操作"


# ------------------------------------------------------------------------------------
# 循环：调工具 → 拿到结果 → 给最终答案
# ------------------------------------------------------------------------------------


async def test_tool_loop_executes_tool_and_answers_with_its_data(monkeypatch, tmp_path):
    """核心路径：模型要工具 → 工具真被执行 → 最终答案基于工具返回值。"""
    tool = _stub_tool(result='{"innerCode": "A1000001", "缺口": 9}')
    state, _ = await _run_graph(
        monkeypatch,
        tmp_path,
        messages=[
            _tool_call("get_machine_inventory", {"inner_code": "A1000001"}),
            AIMessage(content="该货道缺 9 件。"),
        ],
        tools=[tool],
    )

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1, "工具必须被执行一次"
    assert "缺口" in str(tool_messages[0].content), "工具返回值必须进历史（模型才能引用）"
    assert state["reply"] == "该货道缺 9 件。", "最终 reply 应为最后一轮的答案"
    assert state["messages"][-1].content == "该货道缺 9 件。"


async def test_token_usage_accumulates_across_tool_rounds(monkeypatch, tmp_path):
    """token 必须**跨轮累计**——否则成本计量（0-12）系统性低估，且不会有任何报错。"""
    tool = _stub_tool()
    state, _ = await _run_graph(
        monkeypatch,
        tmp_path,
        messages=[_tool_call("get_machine_inventory", {}), AIMessage(content="完成。")],
        tools=[tool],
    )
    # 两轮调用各自的用量必须相加（GenericFakeChatModel 不带 usage_metadata，故为 0；
    # 这里断言的是“累计语义成立”——字段存在且没被单轮覆盖成 None/缺失）
    assert isinstance(state["tokens_in"], int) and isinstance(state["tokens_out"], int)


async def test_tool_rounds_are_capped_and_then_answers(monkeypatch, tmp_path):
    """正常配合的模型：调到上限后**不再放行工具**，直接给答案（防烧 token）。"""
    queued = [
        _tool_call("get_machine_inventory", {}, call_id=f"c{i}") for i in range(MAX_TOOL_ROUNDS)
    ]
    queued.append(AIMessage(content="基于已有信息作答。"))
    state, _ = await _run_graph(monkeypatch, tmp_path, messages=queued, tools=[_stub_tool()])

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == MAX_TOOL_ROUNDS, (
        f"工具最多执行 {MAX_TOOL_ROUNDS} 轮，实际 {len(tool_messages)} 轮（超了就是在烧 token）"
    )
    assert state["reply"] == "基于已有信息作答。", "到上限后必须能正常给出答案"


async def test_cap_is_hard_even_if_model_keeps_asking(monkeypatch, tmp_path):
    """不配合的模型：到上限仍一直要工具时，**图也不放行**，并用一句话如实收尾。

    为什么必须测这条：第一版上限只做了“到上限就不 bind_tools”，而**条件边仍会路由到 tools**——
    实测把一直要工具的假模型放进图里，ToolNode 会被反复调起，上限形同虚设。
    现在上限在条件边硬拦（`_should_call_tools`），这里就是那个锚点。
    """
    queued = [
        _tool_call("get_machine_inventory", {}, call_id=f"c{i}") for i in range(MAX_TOOL_ROUNDS + 3)
    ]
    state, _ = await _run_graph(monkeypatch, tmp_path, messages=queued, tools=[_stub_tool()])

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == MAX_TOOL_ROUNDS, (
        f"上限是硬的：最多 {MAX_TOOL_ROUNDS} 轮，实际 {len(tool_messages)} 轮"
    )
    # 不能给用户一个空气泡，也不能假装查到了——要如实说明并给出下一步
    assert state["reply"], "必须给出非空回复（否则前端是一个空气泡）"
    assert "未能得出结论" in state["reply"] or "更具体" in state["reply"], (
        f"超限收尾要说清情况，实际：{state['reply']}"
    )


# ------------------------------------------------------------------------------------
# 失败路径：工具报错要喂回模型，不能抛穿
# ------------------------------------------------------------------------------------


async def test_tool_error_is_fed_back_not_raised(monkeypatch, tmp_path):
    """工具抛异常时，对话**不能崩**：异常要变成模型可读的结果，让它如实解释。"""
    tool = _stub_tool(error=RuntimeError("数据库连接失败"))
    state, _ = await _run_graph(
        monkeypatch,
        tmp_path,
        messages=[_tool_call("get_machine_inventory", {}), AIMessage(content="抱歉，查询失败。")],
        tools=[tool],
    )
    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert tool_messages, "失败也必须留下 ToolMessage（否则模型不知道发生了什么）"
    content = str(tool_messages[0].content)
    assert "失败" in content or "error" in content.lower()
    assert state["reply"] == "抱歉，查询失败。"


async def test_model_without_tool_support_raises_clear_error(monkeypatch, tmp_path):
    """模型不支持函数调用时，报错必须**可执行**，而不是原始 NotImplementedError。

    为什么不能静默降级：提示词已告知模型“你可以调工具”，无工具却继续作答，
    模型会声称“查过了”或自行编数——那比直接报错危险得多。
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel as _Fake

    fake = _Fake(messages=iter([AIMessage(content="x")]))  # 没实现 bind_tools
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: fake)

    store = SqliteCheckpointStore(tmp_path / "cp2.db")
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=True, tools=[_stub_tool()])
        with pytest.raises(Exception) as excinfo:  # noqa: B017 —— 图会把 LlmError 包一层
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "hi"}], "scene": 1},
                config={"configurable": {"thread_id": "t-2"}},
            )
        assert "不支持函数调用" in str(excinfo.value), f"报错应可执行，实际：{excinfo.value}"
    finally:
        await store.close()


async def test_no_tools_configured_keeps_plain_chat(monkeypatch, tmp_path):
    """不配工具时行为不变（纯对话）——避免“加了工具”把所有老路径都改了。"""
    fake = _ToolCallingFake(messages=iter([AIMessage(content="普通回答")]))
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: fake)

    store = SqliteCheckpointStore(tmp_path / "cp3.db")
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=True, tools=None)
        config = {"configurable": {"thread_id": "t-3"}}
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "hi"}], "scene": 1}, config=config
        )
        state = await graph.aget_state(config)
        assert state.values["reply"] == "普通回答"
    finally:
        await store.close()


async def test_token_usage_is_summed_not_overwritten(monkeypatch, tmp_path):
    """token 必须**求和**而不是被最后一轮覆盖。

    为什么单独立一条（而不是用无 usage 的桩顺手带过）：跨轮累计写错的表现是
    “成本计量偏低”，**没有任何报错**，而且单轮对话完全正常——只有多轮工具调用才暴露。
    这里给每一轮都挂确定的 usage，直接断言“合计 = 各轮之和”。

    实现说明：`GenericFakeChatModel` 会**原样透传**排队消息上的 `usage_metadata`（已实测），
    所以不需要自己写一个 chat model——只造假数据，不造框架行为。
    """
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    first = _tool_call("get_machine_inventory", {})
    first.usage_metadata = usage
    second = AIMessage(content="完成。", usage_metadata=usage)

    fake = _ToolCallingFake(messages=iter([first, second]))
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: fake)

    store = SqliteCheckpointStore(tmp_path / "cp4.db")
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=True, tools=[_stub_tool()])
        config = {"configurable": {"thread_id": "t-4"}}
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "hi"}], "scene": 1}, config=config
        )
        state = (await graph.aget_state(config)).values
    finally:
        await store.close()

    # 两轮 LLM 调用（第一轮要工具、第二轮给答案）→ 必须累加
    assert state["tokens_in"] == 20, f"input token 应累加为 20，实际 {state['tokens_in']}"
    assert state["tokens_out"] == 10, f"output token 应累加为 10，实际 {state['tokens_out']}"
