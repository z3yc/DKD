"""AI 助手对话提示词的回归测试（2026-10-01 修报障产出）。

## 为什么这类缺陷需要专门的测试

缺陷原貌：LLM 节点把**裸消息历史**直接交给模型，一条 system 都没有，连 `scene` 也没用。
实测后果：在前端写着“我是补货 Agent”的抽屉里问「先看紧急的」，
模型回答「请问您遇到了什么紧急情况？…（例如：身体不适、账号被盗…）」——
把**补货术语**理解成了人身紧急事件。

这类缺陷的特征是：**不报错、不 500、测试全绿**，只有拿真实用例问一遍才会发现。
所以这里把三件事固化成断言，而不是只写在注释里：
  ① system 提示词**必须存在且排在最前**（放历史之后会变成“事后补充说明”，模型容易忽略）；
  ② 提示词必须**按场景**切换侧重点（scene 不能形同虚设）；
  ③ 提示词必须包含**能力诚实**约束（明确“不能查数据/不能建单/不得编造数字”，并给出替代路径）
     —— 不写这一条，模型会顺着“补货 Agent”的身份编造出“今日有 2 条紧急建议”这种数字，
     而那比“不回答”危险得多：运营会照着假清单去派单。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import app.graphs.chat_graph as chat_graph
from app.prompts.assistant_chat import (
    SCENE_ANALYSIS,
    SCENE_DIAGNOSE,
    SCENE_GENERAL,
    SCENE_RESTOCK,
    system_prompt,
)

# ------------------------------------------------------------------------------------
# 提示词本身
# ------------------------------------------------------------------------------------


def test_prompt_declares_capability_boundary_and_no_fabrication():
    """能力诚实是这套提示词最核心的一条，逐条断言而不是“看起来有写”。"""
    prompt = system_prompt(SCENE_RESTOCK)

    assert "绝不编造数字" in prompt, "必须禁止编造具体数值（假清单比不回答更危险）"
    assert "智能补货工作台" in prompt, "必须告诉用户去哪里看真实数据"
    assert "决策审计" in prompt, "审计入口也要指出来（排障最常用）"
    assert "不能**创建工单" in prompt or "不能创建工单" in prompt, "必须明确不能建单"
    assert "不要用模糊话术糊过去" in prompt or "直接说做不到" in prompt, "做不到要直说"


def test_prompt_guards_against_user_text_as_instructions():
    """用户消息仅数据非指令（AGENTS §7.6 的同一条精神）。"""
    prompt = system_prompt(SCENE_GENERAL)
    assert "忽略以上规则" in prompt, "必须显式提到这类注入句式"
    assert "不得改变你的身份" in prompt, "注入不得改变身份与边界"


def test_prompt_switches_focus_by_scene():
    """scene 不能形同虚设——这正是原缺陷的一部分（节点完全没用 state['scene']）。"""
    restock = system_prompt(SCENE_RESTOCK)
    diagnose = system_prompt(SCENE_DIAGNOSE)
    analysis = system_prompt(SCENE_ANALYSIS)

    assert "智能补货" in restock
    assert "设备故障诊断" in diagnose
    assert "运营分析" in analysis
    assert restock != diagnose != analysis, "不同场景的提示词必须真的不同"


def test_unknown_scene_falls_back_to_general():
    """未知场景码按通用问答处理（不抛错、不产生空白提示词）。"""
    assert system_prompt(99) == system_prompt(SCENE_GENERAL)


# ------------------------------------------------------------------------------------
# 节点注入行为（打桩模型，不产生外部调用）
# ------------------------------------------------------------------------------------


class _CapturingModel:
    """记录收到的 messages，便于断言 system 的位置与内容。"""

    def __init__(self) -> None:
        self.received: list = []

    async def ainvoke(self, messages):  # noqa: ANN001, ANN202
        self.received = list(messages)
        return AIMessage(
            content="好的。",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            response_metadata={"model_name": "stub-model"},
        )


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        event = next((ln[7:] for ln in lines if ln.startswith("event: ")), "")
        data = json.loads(next((ln[6:] for ln in lines if ln.startswith("data: ")), "{}"))
        if event:
            out.append((event, data))
    return out


def test_llm_node_prepends_system_prompt_before_history(client_with_llm: TestClient, monkeypatch):
    """system 必须在最前面；用户消息必须在其后（不能被挤掉或被当成 system）。"""
    model = _CapturingModel()
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: model)

    resp = client_with_llm.post("/agent/chat", json={"message": "先看紧急的", "scene": 2})
    assert resp.status_code == 200
    assert _frames(resp.text)[-1][0] == "done"

    assert model.received, "模型必须真的被调用（否则本测试什么也没验证）"
    first = model.received[0]
    assert isinstance(first, SystemMessage), (
        f"第一条必须是 SystemMessage，实际 {type(first).__name__}"
    )

    prompt = str(first.content)
    assert "智能补货" in prompt, "system 里必须带上场景侧重（scene=2）"
    assert "绝不编造数字" in prompt, "system 里必须带上能力诚实约束"

    # 用户消息仍在，且是 HumanMessage（不是被塞进 system）
    humans = [m for m in model.received if isinstance(m, HumanMessage)]
    assert humans, "用户消息必须保留"
    assert "先看紧急的" in str(humans[-1].content), "用户原文必须原样传给模型"
