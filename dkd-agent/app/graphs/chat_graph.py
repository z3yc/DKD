"""对话图（任务 0-10 骨架）。

为什么现在就要有图：checkpointer 的价值体现在「多轮会话 + 跨请求状态恢复」，
必须先有一个带状态的图，才能验证 0-10 的验收标准（跨请求恢复 / 断线重连续接）。

state schema 冻结约定（任务 0-13）：本文件 `ChatState` 的字段一经上线不得删除或改名，
新增字段必须带默认值——LangGraph 会把最新图代码应用于所有历史 checkpoint（方案 V1.1 §5.2）。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.llm import LlmError, build_chat_model

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


async def respond_with_llm(state: ChatState) -> dict[str, Any]:
    """LLM 版本回复节点（需 DKD_DEEPSEEK_API_KEY + DKD_AGENT_USE_LLM=1）。"""
    client = build_chat_model()
    history = [
        m if isinstance(m, HumanMessage) else AIMessage(content=str(m.content))
        for m in state.get("messages", [])
    ]
    try:
        resp = await client.ainvoke(history)
    except Exception as exc:  # 统一转 LlmError，由 API 层映射 502（AGENTS §6.3）
        raise LlmError(f"LLM 调用失败：{type(exc).__name__}") from exc
    reply = resp.content if isinstance(resp.content, str) else str(resp.content)
    usage = getattr(resp, "usage_metadata", None) or {}
    meta = getattr(resp, "response_metadata", None) or {}
    return {
        "messages": [AIMessage(content=reply)],
        "reply": reply,
        "model": str(meta.get("model_name") or meta.get("model") or "unknown"),
        "tokens_in": int(usage.get("input_tokens", 0) or 0),
        "tokens_out": int(usage.get("output_tokens", 0) or 0),
    }


def build_chat_graph(*, checkpointer: Any, use_llm: bool = False) -> Any:
    """编译对话图（带 checkpointer）。"""
    builder = StateGraph(ChatState)
    builder.add_node("respond", respond_with_llm if use_llm else respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)
