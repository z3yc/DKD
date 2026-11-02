"""诊断排查清单状态机的单元测试（任务 2-3 的问诊循环）。

这批用例防的是「模型看起来排查过、其实只查了一半」：结论卡很完整，但没看出货异常、
没看库存——而用户会以为已经排除完毕。清单是确定性规则，所以可以逐条断言：
必需项未完成时**不允许**下结论；下一项由清单顺序决定，与模型措辞无关。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app.graphs.chat_graph as chat_graph
from app.prompts.assistant_chat import (
    SCENE_DIAGNOSE,
    SCENE_GENERAL,
    SCENE_RESTOCK,
    TOOL_NAMES_BY_SCENE,
    system_prompt,
)
from app.services.diagnosis_checklist import (
    CONCLUSION_TOOL,
    STEPS,
    can_conclude,
    completed_keys,
    guidance,
    missing_required,
    next_step,
)


def test_missing_required_without_focus_or_tools() -> None:
    assert [step.key for step in missing_required([], has_focus=False)] == ["locate", "anomalies"]
    assert can_conclude([], has_focus=False) is False
    assert next_step([], has_focus=False).tool == "find_devices"


def test_focus_completes_locate_step() -> None:
    keys = completed_keys([], has_focus=True)
    assert "locate" in keys
    assert [step.key for step in missing_required([], has_focus=True)] == ["anomalies"]
    assert next_step([], has_focus=True).tool == "list_device_anomalies"


def test_required_done_allows_conclusion_and_next_is_optional() -> None:
    names = ["list_device_anomalies"]
    assert can_conclude(names, has_focus=True) is True
    assert missing_required(names, has_focus=True) == ()
    assert next_step(names, has_focus=True).tool == "get_machine_inventory"


def test_step_completion_is_order_independent() -> None:
    """先查异常再定位，也算两项都完成（顺序是建议，完成度是事实）。"""
    keys = completed_keys(["list_device_anomalies", "find_devices"], has_focus=False)
    assert {"locate", "anomalies"} <= keys
    assert can_conclude(["list_device_anomalies", "find_devices"], has_focus=False) is True


def test_unknown_tool_does_not_complete_any_step() -> None:
    assert completed_keys(["aggregate_channel_sales"], has_focus=False) == set()


def test_no_next_step_when_all_done() -> None:
    names = [step.tool for step in STEPS]
    assert next_step(names, has_focus=False) is None
    assert "清单已全部完成" in guidance(names, has_focus=False)


def test_guidance_forbids_conclusion_until_required_done() -> None:
    text = guidance([], has_focus=False)
    assert "find_devices" in text
    assert "不要调用 `build_device_diagnosis`" in text
    assert "已完成：无" in text


def test_guidance_allows_conclusion_when_required_done() -> None:
    text = guidance(["list_device_anomalies"], has_focus=True)
    assert "可以调用 `build_device_diagnosis`" in text
    assert "不要调用" not in text


def test_checklist_tools_match_diagnose_scene_tools() -> None:
    """清单里每一步的工具都必须真实存在于诊断场景工具集，否则提示词会指向不存在的工具。"""
    scene_tools = set(TOOL_NAMES_BY_SCENE[SCENE_DIAGNOSE])
    for step in STEPS:
        assert step.tool in scene_tools, f"清单步骤 {step.key} 的工具 {step.tool} 不在诊断工具集里"
    assert CONCLUSION_TOOL in scene_tools


def test_system_prompt_only_injects_hint_for_diagnose_scene() -> None:
    hint = guidance([], has_focus=False)
    diagnose = system_prompt(SCENE_DIAGNOSE, diagnosis_hint=hint)
    assert "本轮排查进度" in diagnose
    for scene in (SCENE_GENERAL, SCENE_RESTOCK):
        assert "本轮排查进度" not in system_prompt(scene, diagnosis_hint=hint)


def _state(scene: int, messages: list[object], focus: str = "") -> dict[str, object]:
    return {"scene": scene, "messages": messages, "focus_inner_code": focus}


def test_diagnose_hint_derives_next_step_from_current_turn_tools() -> None:
    state = _state(
        SCENE_DIAGNOSE,
        [
            HumanMessage(content="A1000001 不出货"),
            ToolMessage(content="{}", tool_call_id="1", name="find_devices"),
        ],
    )
    hint = chat_graph._diagnosis_hint(state)
    assert "已完成：定位设备" in hint
    assert "list_device_anomalies" in hint


def test_previous_turn_tools_do_not_count() -> None:
    """回归 FIX-29 的切片纪律：跨轮累计会让“本轮排查进度”永远显示已完成。"""
    state = _state(
        SCENE_DIAGNOSE,
        [
            HumanMessage(content="上一轮问题"),
            ToolMessage(content="{}", tool_call_id="1", name="list_device_anomalies"),
            AIMessage(content="上一轮回答"),
            HumanMessage(content="这一轮新问题"),
        ],
    )
    hint = chat_graph._diagnosis_hint(state)
    assert "已完成：无" in hint


def test_non_diagnose_scene_has_no_checklist_hint() -> None:
    state = _state(
        SCENE_RESTOCK,
        [
            HumanMessage(content="补货建议"),
            ToolMessage(content="{}", tool_call_id="1", name="find_devices"),
        ],
    )
    assert chat_graph._diagnosis_hint(state) == ""


def _conclusion_call(inner_code: str = "A1000001") -> dict[str, object]:
    return {
        "name": "build_device_diagnosis",
        "args": {"inner_code": inner_code},
        "id": "call-conclusion",
        "type": "tool_call",
    }


def test_guard_replaces_premature_conclusion_with_missing_step() -> None:
    """过早下结论 → 换成清单里的下一步（对同一台设备真查一次），而不是把活退给用户。"""
    state = _state(SCENE_DIAGNOSE, [HumanMessage(content="A1000001 不出货")], focus="A1000001")
    calls, reply = chat_graph._guard_diagnosis_tool_calls(state, [_conclusion_call()])

    assert reply is None
    assert [call["name"] for call in calls] == ["list_device_anomalies"]
    assert calls[0]["args"] == {"inner_code": "A1000001"}
    assert calls[0]["id"] == "call-conclusion"


def test_guard_keeps_other_tools_and_replaces_only_conclusion() -> None:
    state = _state(SCENE_DIAGNOSE, [HumanMessage(content="A1000001 不出货")], focus="A1000001")
    other = {
        "name": "search_device_history",
        "args": {"inner_code": "A1000001"},
        "id": "call-history",
        "type": "tool_call",
    }
    calls, reply = chat_graph._guard_diagnosis_tool_calls(state, [other, _conclusion_call()])

    assert reply is None
    assert [call["name"] for call in calls] == ["search_device_history", "list_device_anomalies"]


def test_guard_allows_conclusion_after_required_steps() -> None:
    state = _state(
        SCENE_DIAGNOSE,
        [
            HumanMessage(content="A1000001 不出货"),
            ToolMessage(content="{}", tool_call_id="1", name="list_device_anomalies"),
        ],
        focus="A1000001",
    )
    calls, reply = chat_graph._guard_diagnosis_tool_calls(state, [_conclusion_call()])

    assert reply is None
    assert [call["name"] for call in calls] == ["build_device_diagnosis"]


def test_guard_asks_for_device_when_nothing_located() -> None:
    """连设备都没有时无法替他查，必须如实要设备（不得编一个编码去查）。"""
    state = _state(SCENE_DIAGNOSE, [HumanMessage(content="机器不出货")])
    calls, reply = chat_graph._guard_diagnosis_tool_calls(state, [_conclusion_call("")])

    assert calls == []
    assert reply is not None
    assert "设备编码" in reply


def test_guard_ignores_non_diagnose_scene_and_plain_answers() -> None:
    restock = _state(SCENE_RESTOCK, [HumanMessage(content="补货")], focus="A1000001")
    calls, reply = chat_graph._guard_diagnosis_tool_calls(restock, [_conclusion_call()])
    assert calls == [_conclusion_call()]
    assert reply is None

    plain = _state(SCENE_DIAGNOSE, [HumanMessage(content="你好")], focus="A1000001")
    calls, reply = chat_graph._guard_diagnosis_tool_calls(plain, [])
    assert calls == []
    assert reply is None
