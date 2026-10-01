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
from app.prompts.assistant_chat import (
    SCENE_ANALYSIS,
    SCENE_DIAGNOSE,
    SCENE_GENERAL,
    SCENE_RESTOCK,
    system_prompt,
    tool_names_for_scene,
)
from app.tools.assistant_tools import (
    all_assistant_tools,
    build_assistant_tools,
    tools_for_scene,
)


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
    names = {t.name for t in all_assistant_tools()}
    assert names == {
        "list_restock_suggestions",
        "get_machine_inventory",
        "list_inflight_work_orders",
        "get_sales_summary",
        "find_devices",
        "list_device_anomalies",
        "search_device_history",
        "build_device_diagnosis",
    }, "工具清单变了就要同步这份断言（它是“能力面”的唯一清单）"
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


# ----------------------------------------------------------------------------------
# 成本优化（§十 任务 7）：返回体积必须**可断言**，不能只靠“感觉变快了”
# ----------------------------------------------------------------------------------


def _plan_dict(*, items: int = 2, reason: str | None = None) -> dict[str, Any]:
    return {
        "id": 88,
        "innerCode": "A1000001",
        "status": 1,
        "totalQuantity": 12,
        "assigneeName": "张三",
        "items": [
            {
                "channelCode": f"1-{i + 1}",
                "skuName": "可口可乐",
                "currentQuantity": 1,
                "maxCapacity": 10,
                "suggestedQuantity": 8,
                "priority": 4 if i == 0 else 1,
                "estimatedDays": 0,
                "reason": reason if reason is not None else "库存低于预警值",
            }
            for i in range(items)
        ],
    }


def test_plan_brief_omits_channels_unless_requested():
    """默认不带货道明细（token 的大头），但必须**显式说明“已省略”**。

    只删字段不说一声，模型会以为“这台设备没有货道明细”，进而编造货道细节——
    那比多花 token 危险得多。
    """
    from app.tools.assistant_tools import _plan_brief

    quiet = _plan_brief(_plan_dict(), with_channels=False)
    assert "channels" not in quiet
    assert quiet["channelsOmitted"].startswith("该计划未含紧急货道")
    # 汇总信息（台号/数量/最高优先级）必须保留，否则模型连“有几台、多急”都答不出
    assert quiet["innerCode"] == "A1000001"
    assert quiet["totalQuantity"] == 12
    assert quiet["channelCount"] == 2
    assert quiet["maxPriority"] == "紧急"

    detailed = _plan_brief(_plan_dict(), with_channels=True)
    assert len(detailed["channels"]) == 2
    assert detailed["channels"][0]["channelCode"] == "1-1"


def test_plan_brief_clips_reason_with_visible_marker():
    """长 reason 必须截断，且**带上“已截断”标记**（静默截断会让模型把半句话当完整理由）。"""
    from app.tools.assistant_tools import (
        MAX_CHANNELS_PER_PLAN,
        MAX_REASON_CHARS,
        _clip,
        _plan_brief,
    )

    assert _clip(None) is None
    assert _clip(" 短理由 ") == "短理由"

    long_reason = "【降级·规则兜底】" + "很长的模板文本" * 40
    brief = _plan_brief(_plan_dict(reason=long_reason), with_channels=True)
    clipped = brief["channels"][0]["reason"]
    assert clipped.endswith("…（已截断）")
    assert len(clipped) == MAX_REASON_CHARS + len("…（已截断）")

    # 超过货道上限时给出截断来源数（避免“只有 5 条”被当成全部）
    many = _plan_brief(_plan_dict(items=MAX_CHANNELS_PER_PLAN + 3), with_channels=True)
    assert len(many["channels"]) == MAX_CHANNELS_PER_PLAN
    assert many["channelsTruncatedFrom"] == MAX_CHANNELS_PER_PLAN + 3


def test_plan_brief_is_much_smaller_without_channels():
    """量化“省了多少”：这是成本优化的验收口径，不是“看起来小了”。"""
    import json as _json

    from app.tools.assistant_tools import _plan_brief

    plan = _plan_dict(items=6, reason="【降级·规则兜底】" + "模板文本" * 30)
    with_channels = len(_json.dumps(_plan_brief(plan, with_channels=True), ensure_ascii=False))
    without = len(_json.dumps(_plan_brief(plan, with_channels=False), ensure_ascii=False))
    assert without * 3 < with_channels, f"省略明细后只省了 {1 - without / with_channels:.0%}"


def test_plans_tool_schema_exposes_detail_switch():
    """`detail` 必须出现在**工具 schema**里，否则模型永远无法按需取明细（等于没有这个开关）。"""
    tools = {t.name: t for t in build_assistant_tools()}
    schema = tools["list_restock_suggestions"].args_schema.model_json_schema()
    assert "detail" in schema.get("properties", {})


# ------------------------------------------------------------------------------------
# 场景 → 工具集：**提示词与绑定必须一致**（“提示与能力脱节”已踩过两次）
# ------------------------------------------------------------------------------------


def test_scene_tool_names_match_prompt_and_registry():
    """唯一事实源三方一致：场景名单 == 实际绑定的工具集 == 提示词里提到的工具。

    为什么这条最重要：本项目已经被“提示与能力脱节”咬过两次——
      ① 提示词写着“能查数据”，但当时**没有工具**；
      ② 工具挂上了，提示词却还写着“我看不了数据”。
    两次都是配置/代码两边各写一份导致的。现在名单只有一份
    （`app/prompts/assistant_chat.TOOL_NAMES_BY_SCENE`），本用例把它钉死。
    """
    registry = {t.name for t in all_assistant_tools()}
    for scene in (SCENE_RESTOCK, SCENE_DIAGNOSE):
        bound = {t.name for t in tools_for_scene(scene)}
        declared = set(tool_names_for_scene(scene))
        prompt = system_prompt(scene)
        assert bound == declared, f"场景 {scene} 绑定与声明不一致：{bound} vs {declared}"
        assert bound <= registry, f"场景 {scene} 声明了未实现的工具：{bound - registry}"
        for name in declared:
            assert f"`{name}`" in prompt, f"场景 {scene} 绑定了 {name}，但提示词没告诉模型有它"
        # 反向：提示词不得提到本场景没绑的工具（否则模型会“去调”一个不存在的工具）
        for name in registry - bound:
            assert f"`{name}`" not in prompt, (
                f"场景 {scene} 的提示词提到了未绑定的工具 {name}——模型会去调它然后失败"
            )


def test_scenes_without_tools_say_so_in_prompt():
    """通用/运营分析场景不挂工具，提示词必须**如实说明**（否则模型凭印象编数字）。"""
    for scene in (SCENE_GENERAL, SCENE_ANALYSIS):
        assert tools_for_scene(scene) == []
        assert "本场景没有挂" in system_prompt(scene)


def test_diagnosis_scene_gets_diagnosis_tools_not_restock_plans():
    """场景 3 拿到的是诊断工具（消歧/异常/历史），不是补货清单工具。"""
    names = {t.name for t in tools_for_scene(SCENE_DIAGNOSE)}
    assert {
        "find_devices",
        "list_device_anomalies",
        "search_device_history",
        "build_device_diagnosis",
    } <= names
    assert "list_restock_suggestions" not in names, (
        "诊断场景不需要补货清单（省 schema token，也少干扰）"
    )
    assert "get_sales_summary" not in names
    # 库存/在途两个工具诊断也需要（“货道还剩多少/有没有在途工单”是诊断高频问题）
    assert {"get_machine_inventory", "list_inflight_work_orders"} <= names


def test_missing_tool_declaration_fails_fast(monkeypatch):
    """名单里写了但没实现的工具 → **报错**，而不是静默少一个。

    静默少工具的后果就是“提示词声称有能力、实际没有”——正是上面那个 bug。
    """
    import app.tools.assistant_tools as at
    from app.prompts import assistant_chat as prompts

    monkeypatch.setitem(prompts.TOOL_NAMES_BY_SCENE, SCENE_RESTOCK, ("definitely_not_implemented",))
    with pytest.raises(RuntimeError, match="未实现"):
        at.tools_for_scene(SCENE_RESTOCK)


async def test_graph_binds_only_the_scene_toolset(monkeypatch, tmp_path):
    """图按场景 bind 工具：诊断场景里**看不到**补货清单工具（反之亦然）。

    断言方式是看模型实际收到的工具名（桩模型记录 bind_tools 的参数）——
    这比“看配置对不对”更接近真实行为：模型能看到什么，取决于 bind 了什么。
    """
    seen: list[set[str]] = []

    class _RecordingModel:
        def bind_tools(self, tools):  # noqa: ANN001, ANN202
            seen.append({t.name for t in tools})
            return self

        async def ainvoke(self, messages):  # noqa: ANN001, ANN202
            return AIMessage(
                content="好的。",
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                response_metadata={"model_name": "stub"},
            )

    model = _RecordingModel()
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: model)

    store = SqliteCheckpointStore(tmp_path / "cp-scene.db")
    saver = await store.open()
    try:
        graph = build_chat_graph(
            checkpointer=saver,
            use_llm=True,
            tools_by_scene={
                SCENE_RESTOCK: tools_for_scene(SCENE_RESTOCK),
                SCENE_DIAGNOSE: tools_for_scene(SCENE_DIAGNOSE),
            },
        )
        for scene, expected_contains, expected_absent in (
            (SCENE_RESTOCK, "list_restock_suggestions", "search_device_history"),
            (SCENE_DIAGNOSE, "search_device_history", "list_restock_suggestions"),
        ):
            config = {"configurable": {"thread_id": f"scene-{scene}"}}
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "hi"}], "scene": scene}, config=config
            )
            assert seen, "模型必须被调用（否则本用例什么都没验）"
            bound = seen[-1]
            assert expected_contains in bound, f"场景 {scene} 少绑了 {expected_contains}：{bound}"
            assert expected_absent not in bound, f"场景 {scene} 多绑了 {expected_absent}：{bound}"
    finally:
        await store.close()


# ------------------------------------------------------------------------------------
# 2-4 的前端契约：结构化结论随 done 帧下发（且只在真的调了该工具时才有）
# ------------------------------------------------------------------------------------


async def test_done_frame_carries_diagnosis_conclusion(monkeypatch, tmp_path):
    """本轮调了 `build_device_diagnosis` → done 帧必须带上结构化结论（前端据此画置信度条）。

    为什么单独测这条：它是**跨层契约**（Python 工具返回值 → SSE 帧 → 前端卡片），
    最容易的坏法是“工具改了字段名/结构，前端那条进度条静默消失”——没有报错、没人发现。
    """
    payload = {
        "innerCode": "A1000001",
        "confidence": "high",
        "confidenceBasis": "存在 2 条订单异常",
        "cannotConclude": False,
        "unknowns": ["库存日志未采集"],
        "recommendedActions": ["在「设备管理 → 工单管理」新建维修工单"],
        "evidence": [{"kind": "order_anomaly:paid_not_vended", "severity": "medium"}],
        "caliber": "只由平台已采集的数据得出",
    }
    fake = _ToolCallingFake(
        messages=iter(
            [
                _tool_call("build_device_diagnosis", {"inner_code": "A1000001"}),
                AIMessage(content="结论如上。"),
            ]
        )
    )
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: fake)

    store = SqliteCheckpointStore(tmp_path / "cp-conclusion.db")
    saver = await store.open()
    try:
        # 工具名必须与模型请求的一致（默认桩叫 get_machine_inventory，名字错就捞不到结论）
        graph = build_chat_graph(
            checkpointer=saver,
            use_llm=True,
            tools=[_stub_tool(name="build_device_diagnosis", result=json.dumps(payload))],
        )
        config = {"configurable": {"thread_id": "t-conclusion"}}
        # ainvoke 返回最终 state（dict）；别写 .values（那是 dict.values 方法）
        state = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "给个结论"}], "scene": SCENE_DIAGNOSE},
            config=config,
        )
    finally:
        await store.close()

    from app.api.chat import _extract_conclusion

    conclusion = _extract_conclusion(state.get("messages"))
    assert conclusion is not None, "调了诊断工具却没提取到结论"
    assert conclusion["confidence"] == "high"
    assert conclusion["unknowns"] and conclusion["recommendedActions"]
    # 没调该工具时严格为 None（前端据此决定要不要渲染卡片）
    assert _extract_conclusion([AIMessage(content="普通回答")]) is None


def test_conclusion_extraction_is_defensive():
    """提取函数必须容错：非法 JSON / 缺 confidence / 超长 → None（宁可不渲染，也不给半个结论）。"""
    from langchain_core.messages import ToolMessage

    from app.api.chat import (
        _CONCLUSION_TOOL,
        _MAX_RAW_CONCLUSION_CHARS,
        _extract_conclusion,
    )

    def tool_msg(text: str) -> ToolMessage:
        return ToolMessage(content=text, name=_CONCLUSION_TOOL, tool_call_id="c1")

    assert _extract_conclusion([]) is None
    assert _extract_conclusion([tool_msg("not json")]) is None
    assert _extract_conclusion([tool_msg('{"foo": 1}')]) is None, "缺 confidence 不算结论"
    assert _extract_conclusion([tool_msg("x" * (_MAX_RAW_CONCLUSION_CHARS + 1))]) is None, (
        "超长不下发"
    )
    low = _extract_conclusion([tool_msg('{"confidence": "low"}')])
    assert low and low["confidence"] == "low"

    # 真实量级：10 条证据各带 detail 约 5~6KB —— 必须仍然能下发**投影**，
    # 这是实测踩过的坑（初版给原始内容设了 4000 字符护栏，卡片因此静默不渲染）
    big = {
        "confidence": "high",
        "confidenceBasis": "存在 10 条订单异常",
        "innerCode": "A1000001",
        "evidence": [
            {
                "kind": "order_anomaly:paid_not_vended",
                "severity": "medium",
                "summary": "订单卡在支付完成未出货（很长的中文说明，真实数据里还有订单号与时长）"
                * 12,
                "source": "tb_order（口径：status=1 超过阀值未出货）",
                "detail": {"orderNo": f"NO-{i}", "ageMinutes": 1600000, "payStatus": 1},
            }
            for i in range(10)
        ],
        "unknowns": ["库存日志未采集"],
        "recommendedActions": ["在「设备管理 → 工单管理」新建维修工单"],
    }
    raw = tool_msg(json.dumps(big, ensure_ascii=False))
    assert len(raw.content) > 4000, "构造的样本要真的超过初版护栏，否则测不出这个 bug"
    projected = _extract_conclusion([raw])
    assert projected is not None, "真实量级的结论必须能被投影下发"
    assert projected["evidenceCount"] == 10
    assert projected["evidenceKinds"] == ["order_anomaly:paid_not_vended"]
    # **投影里不得出现逐条 detail**：那是给模型看的，不该进 done 帧（帧体量 + 前端也不需要）
    assert "detail" not in json.dumps(projected)
    assert len(json.dumps(projected, ensure_ascii=False)) < 2000, "投影必须体量有界"


# ------------------------------------------------------------------------------------
# 2-3 多轮问诊：`focus_inner_code`（“正在诊断哪台设备”是状态，不是模型的记性）
# ------------------------------------------------------------------------------------


def test_focus_prefers_explicit_code_then_unique_tool_hit():
    """焦点优先级：用户原话里的编号 > 工具唯一命中 > 保持原值；多台候选**不得**替他选。"""
    from langchain_core.messages import HumanMessage as H
    from langchain_core.messages import ToolMessage

    from app.graphs.chat_graph import _detect_focus, _focus_from_tools

    # 1) 用户明确说了编号 → 以他说的为准（即使上一轮焦点是别的设备）
    state = {"messages": [H(content="看看 A1000001 的问题")], "focus_inner_code": "tCeiyxLx"}
    assert _detect_focus(state) == "A1000001"

    # 2) 本轮没提编号 → 保持原焦点（追问「那它的库存呢」要落到同一台设备）
    state = {"messages": [H(content="那它的库存呢")], "focus_inner_code": "tCeiyxLx"}
    assert _detect_focus(state) == "tCeiyxLx"

    # 3) 工具恰好一台候选 → 算确定
    unique = ToolMessage(
        content=json.dumps({"matchCount": 1, "devices": [{"innerCode": "A1000001"}]}),
        name="find_devices",
        tool_call_id="c1",
    )
    assert _focus_from_tools({"messages": [unique]}) == "A1000001"

    # 4) 多台候选（用户在“五道口”里有 8 台）→ **不能**替他选一台
    many = ToolMessage(
        content=json.dumps({"matchCount": 3, "devices": [{"innerCode": "A"}, {"innerCode": "B"}]}),
        name="find_devices",
        tool_call_id="c2",
    )
    assert _focus_from_tools({"messages": [many]}) == "", "多候选必须留给用户确认，不得静默选一台"


def test_prompt_carries_focus_only_when_known():
    """焦点进提示词（否则模型无从知道“它”指哪台）；未确定时不得出现该段。"""
    from app.prompts.assistant_chat import system_prompt

    with_focus = system_prompt(SCENE_DIAGNOSE, focus_inner_code="A1000001")
    assert "本次会话正在诊断的设备" in with_focus
    assert "`A1000001`" in with_focus
    assert "不要再向他索要编号" in with_focus

    without = system_prompt(SCENE_DIAGNOSE)
    assert "本次会话正在诊断的设备" not in without


async def test_focus_survives_across_turns_through_checkpoint(monkeypatch, tmp_path):
    """两轮对话：第 2 轮的 system 提示词必须带上第 1 轮确定的设备（跨 checkpoint 生效）。

    这条是 2-3 的核心可用性：**追问同一台设备**不该靠模型记性。
    """
    seen: list[str] = []

    class _Capturing:
        def bind_tools(self, _tools):  # noqa: ANN001, ANN202
            return self

        async def ainvoke(self, messages):  # noqa: ANN001, ANN202
            seen.append(str(messages[0].content))
            return AIMessage(
                content="好的。",
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                response_metadata={"model_name": "stub"},
            )

    model = _Capturing()
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: model)

    store = SqliteCheckpointStore(tmp_path / "cp-focus.db")
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=True, tools=[])
        config = {"configurable": {"thread_id": "t-focus"}}
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "看看 A1000001"}], "scene": 3}, config=config
        )
        # 第一轮就带焦点是正确的：`resolve_focus` 在 `respond` **之前**跑，
        # 用户原话里的编号当轮即可确定（本测试第一版断言“第一轮不该有”，那是错的）
        assert "本次会话正在诊断的设备" in seen[0]
        assert "`A1000001`" in seen[0]

        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "那它的库存呢"}], "scene": 3}, config=config
        )
        assert "`A1000001`" in seen[-1], "第二轮必须带上焦点设备"
        assert "不要再向他索要编号" in seen[-1]

        state = await graph.aget_state(config)
        assert state.values.get("focus_inner_code") == "A1000001"
    finally:
        await store.close()


def test_tool_calls_are_recorded_for_audit():
    """留痕要能回答“这个回答基于哪几次查询”：工具调用必须被提取（名称+截断参数）。

    为什么这条重要：模型会说错数字（实测把 90 天窗口的 0 条套到 1825 天窗口上，FIX-27），
    排障第一步就是核对它**到底查了什么**；而“解析回复文本”既不可靠也没法回归。
    """
    from langchain_core.messages import AIMessage

    from app.api.chat import _extract_tool_calls

    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "list_device_anomalies",
                "args": {"inner_code": "A1000001", "days": 1825},
                "id": "c1",
            },
            {"name": "build_device_diagnosis", "args": {"inner_code": "A1000001"}, "id": "c2"},
        ],
    )
    calls = _extract_tool_calls([ai])
    assert [c["name"] for c in calls] == ["list_device_anomalies", "build_device_diagnosis"]
    assert "1825" in str(calls[0]["args"])
    # 参数超长要截断（不许把大参数塞进留痕）
    huge = AIMessage(
        content="",
        tool_calls=[{"name": "x", "args": {"blob": "y" * 1000}, "id": "c3"}],
    )
    assert len(str(_extract_tool_calls([huge])[0]["args"])) <= 401
    assert _extract_tool_calls([]) == []
