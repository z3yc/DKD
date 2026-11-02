"""诊断排查清单（任务 2-3 的问诊循环状态机）。

## 为什么需要它

2-3 已落地「焦点 + 工具编排 + 窗口跟随」，但**没有排查次序**：模型可以一上来就
`build_device_diagnosis` 下结论，而用户问的是「这台机器不出货」——结论看起来完整，
实际只查了一半（没看出货异常、没看库存、没看在途工单）。这类回答**最危险的地方是不可见**：
用户以为「已排查完毕」。

本模块把「按现象逐项排除」做成**确定性状态**：

1. 清单固定有序（定位 → 出货/库存异常 → 货道库存 → 在途工单 → 历史维修）；
2. 「某一步是否完成」由**本轮实际调用过的工具**推导——不新增 LangGraph state 字段
   （state schema 已冻结，见 `chat_graph` 模块 docstring；能推导的计数不该新增字段，
   与 `_tool_rounds_used` 同一原则）；
3. 未完成**必需项**前，`build_device_diagnosis` 会被拦（守卫在工具侧，见
   `app/tools/assistant_tools._build_device_diagnosis`），提示词里也会写明下一步；
4. 用户一句问话里只完成一部分也可以，**下一轮接着算**（本轮工具调用已进 messages）。

## 边界（如实登记）

- 清单是**必需的顺序下限**，不是「只能按这个顺序调用」：用户明确要求先看历史维修时，
  模型可以先调 `search_device_history`，该步会被记为完成，但不会因此跳过必需项。
- 不做语义理解（不解析“不出货”这类现象词去跳步）：现象词匹配不可回归，且很容易变成
  “模型措辞一变，排查路径就变”。顺序由清单固定，关键词只影响提示词里的重点说明。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# 结论工具：它是“终点”，不是清单里的一步
CONCLUSION_TOOL = "build_device_diagnosis"


@dataclass(frozen=True)
class Step:
    """清单里的一步。

    @param key 稳定标识（用于断言与留痕，不要用中文 label 当键）
    @param label 给人看的名字
    @param tool 完成该步需要调用的工具名
    @param required 是否必需（必需项未完成时禁止下结论）
    """

    key: str
    label: str
    tool: str
    required: bool


# 顺序即排查次序：先定位，再排除出货/库存异常，然后补细节，最后才是历史线索。
# ⚠️ 工具名必须与 `app/prompts/assistant_chat.TOOL_NAMES_BY_SCENE[SCENE_DIAGNOSE]` 一致，
# 有测试（tests/test_diagnosis_checklist.py）断言两边不漂移。
STEPS: tuple[Step, ...] = (
    Step("locate", "定位设备", "find_devices", True),
    Step("anomalies", "出货/库存异常", "list_device_anomalies", True),
    Step("inventory", "货道库存明细", "get_machine_inventory", False),
    Step("inflight", "在途工单", "list_inflight_work_orders", False),
    Step("history", "历史维修线索", "search_device_history", False),
)

_STEP_BY_TOOL: dict[str, Step] = {step.tool: step for step in STEPS}


def completed_keys(tool_names: Iterable[str], *, has_focus: bool = False) -> set[str]:
    """本轮已完成清单项（顺序无关：调了哪个工具就算哪步完成）。

    @param tool_names 本轮实际调用过的工具名（由 `chat_graph` 从本轮 ToolMessage 推导）
    @param has_focus 是否已有确定的设备焦点（有焦点等价于已完成「定位」）
    """
    done: set[str] = {"locate"} if has_focus else set()
    for name in tool_names:
        step = _STEP_BY_TOOL.get(str(name))
        if step is not None:
            done.add(step.key)
    return done


def missing_required(tool_names: Iterable[str], *, has_focus: bool = False) -> tuple[Step, ...]:
    """尚未完成的**必需**项（按清单顺序）。空 = 允许下结论。"""
    done = completed_keys(tool_names, has_focus=has_focus)
    return tuple(step for step in STEPS if step.required and step.key not in done)


def can_conclude(tool_names: Iterable[str], *, has_focus: bool = False) -> bool:
    """是否允许调用 `build_device_diagnosis` 下结论。"""
    return not missing_required(tool_names, has_focus=has_focus)


def next_step(tool_names: Iterable[str], *, has_focus: bool = False) -> Step | None:
    """清单里下一个未完成的步骤（必需项优先于可选项，因为顺序固定）。全部完成返回 None。"""
    done = completed_keys(tool_names, has_focus=has_focus)
    for step in STEPS:
        if step.key not in done:
            return step
    return None


def _step_name(step: Step) -> str:
    return f"{step.label}（`{step.tool}`）"


def guidance(tool_names: Iterable[str], *, has_focus: bool = False) -> str:
    """生成本轮排查进度提示词片段（注入 system prompt）。

    措辞刻意写成「系统按清单校验，不是建议」：只在提示词里写“建议按顺序”，
    模型会当成软约束；而实际守卫在工具侧（`build_device_diagnosis` 会拒绝过早调用），
    这里说明清楚，避免模型反复尝试被拒的工具。
    """
    done = completed_keys(tool_names, has_focus=has_focus)
    done_labels = [step.label for step in STEPS if step.key in done]
    done_text = "、".join(done_labels) if done_labels else "无"
    missing = missing_required(tool_names, has_focus=has_focus)
    lines = ["\n\n## 本轮排查进度（系统按清单校验，不是建议）", "", f"已完成：{done_text}。"]
    if missing:
        lines.append(f"**下一步必须调用 {_step_name(missing[0])}**——它属于必需排查项。")
        if len(missing) > 1:
            lines.append(
                "其余必需项：" + "、".join(_step_name(step) for step in missing[1:]) + "。"
            )
        lines.append(
            "**在所有必需项完成之前，不要调用 `build_device_diagnosis`**"
            "（即使你已经有“看起来够用”的线索）。"
        )
        lines.append("如果用户的问题与该步骤无关，也要先用它排除，再继续下一项。")
        return "\n".join(lines)
    nxt = next_step(tool_names, has_focus=has_focus)
    if nxt is not None:
        lines.append(
            f"必需项已完成，可以调用 `build_device_diagnosis` 出结论；"
            f"如证据仍不足，可再补 {_step_name(nxt)}（可选）。"
        )
    else:
        lines.append("清单已全部完成，调用 `build_device_diagnosis` 出结论并如实转述。")
    return "\n".join(lines)


__all__ = [
    "CONCLUSION_TOOL",
    "STEPS",
    "Step",
    "can_conclude",
    "completed_keys",
    "guidance",
    "missing_required",
    "next_step",
]
