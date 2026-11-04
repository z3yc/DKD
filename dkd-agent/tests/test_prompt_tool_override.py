"""排期 3-4 收尾：内置场景停用工具后，提示词工具名单必须同步（但仍保持内置语义）。

## 背景

工具注册表（agent_tool）允许运营停用某个只读工具。`chat_graph` 会按启用集过滤**绑定**，
但 `system_prompt()` 原先对内置场景用静态 `TOOL_NAMES_BY_SCENE` 渲染 `tool_list`——
于是“工具已停用、提示词还说能查”，模型会去调一个没绑的工具（提示与能力脱节）。

修法：内置场景也传 `tool_names_override`（来自本轮实际绑定），但显式 `custom_safety=False`，
保证：不追加自定义安全 footer、写操作约束仍是内置场景文案、**未过滤时逐字不变**。
"""

from __future__ import annotations

from app.prompts.assistant_chat import (
    SCENE_GENERAL,
    SCENE_RESTOCK,
    TOOL_NAMES_BY_SCENE,
    system_prompt,
)


def test_builtin_default_tool_override_is_byte_identical() -> None:
    """未过滤时（override=内置名单）提示词与现状逐字相同。"""
    for scene in (SCENE_GENERAL, SCENE_RESTOCK):
        names = TOOL_NAMES_BY_SCENE.get(scene, ())
        assert system_prompt(scene) == system_prompt(
            scene, tool_names_override=tuple(names), custom_safety=False
        )


def test_builtin_filtered_tool_removed_from_prompt_without_custom_footer() -> None:
    """停用一个工具：提示词里消失，但不出现自定义 footer、仍是内置写操作约束。"""
    base = system_prompt(SCENE_RESTOCK)
    filtered = TOOL_NAMES_BY_SCENE[SCENE_RESTOCK][1:]
    prompt = system_prompt(SCENE_RESTOCK, tool_names_override=tuple(filtered), custom_safety=False)
    removed = TOOL_NAMES_BY_SCENE[SCENE_RESTOCK][0]
    assert f"`{removed}`" not in prompt
    for name in filtered:
        assert f"`{name}`" in prompt
    # 内置场景不追加自定义安全 footer（该 footer 以“自定义智能体”开头）
    assert "自定义智能体" not in prompt
    assert len(prompt) < len(base) + 200


def test_custom_scene_keeps_safety_footer() -> None:
    """自定义场景（传 override 自动判定）仍追加安全 footer——不能被本次修改带丢。"""
    prompt = system_prompt(101, tool_names_override=("get_machine_inventory",))
    assert "get_machine_inventory" in prompt
    assert "自定义智能体" in prompt
