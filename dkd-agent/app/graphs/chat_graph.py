"""对话图（任务 0-10 骨架）。

为什么现在就要有图：checkpointer 的价值体现在「多轮会话 + 跨请求状态恢复」，
必须先有一个带状态的图，才能验证 0-10 的验收标准（跨请求恢复 / 断线重连续接）。

state schema 冻结约定（任务 0-13）：本文件 `ChatState` 的字段一经上线不得删除或改名，
新增字段必须带默认值——LangGraph 会把最新图代码应用于所有历史 checkpoint（方案 V1.1 §5.2）。
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.llm import LlmError, build_chat_model
from app.prompts.assistant_chat import SCENE_GENERAL, system_prompt

# 工具调用轮次上限（防“模型反复调工具烧 token”）。3 轮足够覆盖
# “清单 → 查某台设备 → 查销量”这种最多三跳的问题；超出则强制作答。
MAX_TOOL_ROUNDS = 3

logger = logging.getLogger("dkd.agent.chat_graph")

ECHO_PREFIX = "[echo] "


class ChatState(TypedDict):
    """对话状态（冻结字段见模块 docstring）。

    messages:   完整消息序列（add_messages reducer 负责追加合并）
    scene:      场景码 1-通用问答 2-补货 3-诊断 4-运营分析
    reply:      本轮的助手回复文本（流式输出与断点续传都以它为源）
    model:      服务端**实际返回**的模型名（成本计量按实际值，不按配置猜）
    tokens_in / tokens_out: 本轮 token 用量（写 agent_message 与限额判断用）
    """

    messages: Annotated[list[AnyMessage], add_messages]
    scene: int
    reply: str
    model: str
    tokens_in: int
    tokens_out: int


def _last_human_text(state: ChatState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


async def respond(state: ChatState) -> dict[str, Any]:
    """生成回复节点（echo 模式）。

    当前为 echo 实现（Phase 0 只验证链路与持久化）；接入真实补货/诊断子图后，
    本节点替换为 Supervisor 路由（方案 V1.1 §5.2），**state 字段保持不变**。
    """
    text = _last_human_text(state)
    reply = f"{ECHO_PREFIX}{text}"
    return {
        "messages": [AIMessage(content=reply)],
        "reply": reply,
        "model": "echo",
        "tokens_in": 0,
        "tokens_out": 0,
    }


def _tool_rounds_used(state: ChatState) -> int:
    """已用掉的工具轮次 = 历史里的 ToolMessage 条数。

    为什么不新增 state 字段来计数：0-13 冻结了 state schema（新增字段要走兼容性评审），
    而「已执行的工具调用」本来就完整存在于 `messages` 里——**能推导出来的计数不该新增字段**。
    这也让旧 checkpoint 在升级后能直接被新代码读取（老会话里没有 ToolMessage，计数自然为 0）。
    """
    return sum(1 for m in state.get("messages", []) if isinstance(m, ToolMessage))


async def respond_with_llm(state: ChatState, *, tools: Any = None) -> dict[str, Any]:
    """LLM 回复节点（带工具调用，需 DKD_DEEPSEEK_API_KEY + DKD_AGENT_USE_LLM=1）。

    `streaming=True` 是刻意显式打开的：API 层用 `astream(stream_mode="messages")` 逐 token 下发，
    依赖模型的流式回调。显式打开才能保证“首 token 延迟”这个体验指标不被版本升级默默改掉。

    **为什么必须带 system 提示词（2026-10-01 修报障）**：
    本节点原先把裸消息历史直接交给模型，一条 system 都没有，连 `state["scene"]` 也没用。
    实测后果：在前端写着“我是补货 Agent”的抽屉里问**「先看紧急的」**，
    模型回答「请问您遇到了什么紧急情况？…（例如：身体不适、账号被盗…）」——
    把**补货术语**理解成了人身紧急事件。

    ## 工具调用（2026-10-01 补齐智能功能）

    模型可调用**只读**工具（见 `app/tools/assistant_tools.py`）去真取数。三个关键设计：

    1. **轮次上限靠推导、不新增 state 字段**：已用轮次 = messages 里的 ToolMessage 条数
       （见 `_tool_rounds_used`）。达到上限时**不再 bind_tools**，强制模型基于已有信息作答——
       这样既能防“模型反复调工具烧 token”，又不破坏 0-13 的 state 冻结。
    2. **token 跨轮累计**：多轮工具调用会产生多次 LLM 调用，而 `tokens_in/out` 是**后写覆盖**字段。
       若不累加，成本计量（0-12）只会记到最后一次调用的用量——即**系统性低估成本**。
       字段名/类型未变（不违反冻结规则），但语义明确为“本轮所有 LLM 调用的合计”。
    3. **带工具调用的那一轮不宣称是答案**：命中 tool_calls 时 `reply` 置空，
       只让**最后一轮**（真正给用户看的那一轮）写 `reply`——否则中途那句
       “让我查一下”会被当成最终回复记进留痕，与用户实际看到的不一致。

    ## 保留 system 提示词的纪律

    提示词内容与三条纪律（身份/能力诚实/用户输入非指令）见 `app/prompts/assistant_chat.py`。
    ⚠️ **能力变了必须同步改提示词**：提示词说“我看不了数据”而工具已经能查，
    模型就会拒绝回答——这正是本次修的那类“提示与能力脱节”缺陷。
    """
    client = build_chat_model(streaming=True)
    history = [
        m if isinstance(m, HumanMessage) else AIMessage(content=str(m.content))
        for m in state.get("messages", [])
    ]
    # system 必须在最前面（放在历史之后会变成“事后补充说明”，模型容易忽略）
    messages: list[AnyMessage] = [
        SystemMessage(content=system_prompt(int(state.get("scene", SCENE_GENERAL))))
    ]
    messages.extend(history)

    rounds_used = _tool_rounds_used(state)
    # 工具通过**闭包注入**而不是放进 state：工具实例不可 JSON 序列化，
    # 进 state 会被 checkpointer 试图序列化并报错（而且那也不属于“会话状态”）。
    tool_list = list(tools or [])
    if tool_list and rounds_used < MAX_TOOL_ROUNDS:
        try:
            call_model = client.bind_tools(tool_list)
        except NotImplementedError as exc:
            # 不静默降级（这是刻意的）：提示词已告诉模型“你可以调工具”，
            # 若无工具却继续作答，模型会声称“查过了”或自行编数——比直接报错危险得多。
            # 启动阶段的同样探测见 `app/main.py`（更早暴露，不必等到第一条消息）。
            raise LlmError(
                "当前 LLM 客户端不支持函数调用（bind_tools），无法启用助手只读工具"
            ) from exc
    else:
        call_model = client

    try:
        resp = await call_model.ainvoke(messages)
    except Exception as exc:  # 统一转 LlmError，由 API 层映射 502（AGENTS §6.3）
        raise LlmError(f"LLM 调用失败：{type(exc).__name__}") from exc

    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    tool_calls = list(getattr(resp, "tool_calls", None) or [])
    usage = getattr(resp, "usage_metadata", None) or {}
    meta = getattr(resp, "response_metadata", None) or {}
    # 多轮必须累加（见 docstring 第 2 点）：字段是后写覆盖，不累加就会低估成本
    tokens_in = int(state.get("tokens_in", 0) or 0) + int(usage.get("input_tokens", 0) or 0)
    tokens_out = int(state.get("tokens_out", 0) or 0) + int(usage.get("output_tokens", 0) or 0)

    # 已达轮次上限但模型仍然在要工具：**不能**返回空 reply（用户会看到一个空气泡），
    # 也不把 tool_calls 写回历史（否则下一轮还会被当成待执行）。
    # 这里如实收尾：说明查询轮次已用尽，请用户把问题问得更具体。
    if tool_calls and tool_list and rounds_used >= MAX_TOOL_ROUNDS:
        fallback = content.strip() or (
            f"我连续查询了 {MAX_TOOL_ROUNDS} 轮仍未能得出结论。"
            "请把问题说得更具体一些（例如指定设备编码或日期），我再查一次。"
        )
        return {
            "messages": [AIMessage(content=fallback)],
            "reply": fallback,
            "model": str(meta.get("model_name") or meta.get("model") or "unknown"),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }

    return {
        # 带 tool_calls 时把模型的 AIMessage 原样入历史（否则 ToolNode 找不到调用 ID）
        "messages": [resp],
        # 命中工具调用时不算答案（见 docstring 第 3 点）
        "reply": "" if tool_calls else content,
        "model": str(meta.get("model_name") or meta.get("model") or "unknown"),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def _make_llm_node(tools: Any) -> Any:
    """把工具列表闭包进节点函数（LangGraph 节点只接收 state，工具必须从外部带入）。"""

    async def _node(state: ChatState) -> dict[str, Any]:
        return await respond_with_llm(state, tools=tools)

    return _node


def build_chat_graph(*, checkpointer: Any, use_llm: bool = False, tools: Any = None) -> Any:
    """编译对话图（带 checkpointer）。

    @param tools 只读工具列表（`StructuredTool`）；仅 LLM 分支生效。
                 - 传具体列表 → 模型可调工具（生产路径：`main.py` 传 `build_assistant_tools()`）；
                 - 传 `None`/`[]` → 纯对话（不挂 ToolNode，也不 bind_tools）。
                 默认 None 是刻意的：图的能力应当由**装配方显式给出**，
                 而不是“默默具备”——尤这是给模型赋权（能读写什么）的地方。
    """
    builder = StateGraph(ChatState)
    tool_list = list(tools or [])
    builder.add_node("respond", _make_llm_node(tool_list) if use_llm else respond)
    builder.add_edge(START, "respond")

    if use_llm and tool_list:
        # handle_tool_errors 显式传**自定义处理器**而不是默认值/True：
        # 实测默认只处理参数校验类异常，工具体内的异常会抛穿整个图（把对话打成 502）；
        # 而传 True 会把原始异常文本喂给模型与留痕，可能带上连接串（见 _handle_tool_error）。
        builder.add_node("tools", ToolNode(tool_list, handle_tool_errors=_handle_tool_error))
        builder.add_conditional_edges("respond", _should_call_tools, {"tools": "tools", END: END})
        builder.add_edge("tools", "respond")
    else:
        builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


def _handle_tool_error(exc: Exception) -> str:
    """把工具异常变成模型可读的一句话（**不回显原始异常文本**）。

    为什么不直接把异常文本给模型：工具内部抛出的 SQLAlchemy/psycopg 异常常带连接串与
    主机信息，而这段文字会**直接进 prompt、进而可能进留痕**（AGENTS §7.8 脱敏、§6.1 日志规范）。
    只给异常类型名，细节留在服务端日志里查。

    这也是 AGENTS §6.3「工具执行异常由编排层兜底转为结果消息」的落地位置：
    不给 ToolNode 默认行为（实测默认只处理参数校验类异常，工具体内的异常会**抛穿整个图**）。
    """
    logger.warning("助手工具执行异常：%s", type(exc).__name__, exc_info=True)
    return json.dumps(
        {
            "error": (
                f"工具执行失败（{type(exc).__name__}）。请如实告知用户“查询失败”，"
                "不要猜测数据或凭印象作答。"
            )
        },
        ensure_ascii=False,
    )


def _should_call_tools(state: ChatState) -> str:
    """条件边：模型这一轮是否请求了工具。

    **上限在这里硬拦**（而不是只靠“不 bind_tools 让模型自觉”）：
    实测把不合作的假模型放进图里时会持续请求工具——若不在这里拦，
    “不 bind”根本阻止不了 ToolNode 被反复调起，上限就形同虚设（烧 token）。
    `respond_with_llm` 里的“达上限则不 bind”是**配合**措施（让模型倾向直接作答），
    两者一起才是完整的：一个让模型不谈工具，一个让图不放行工具。
    """
    if _tool_rounds_used(state) >= MAX_TOOL_ROUNDS:
        return END
    last = state.get("messages", [])[-1] if state.get("messages") else None
    if last is not None and list(getattr(last, "tool_calls", None) or []):
        return "tools"
    return END
