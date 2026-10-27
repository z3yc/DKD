"""故障诊断场景的图状态（M2 / F-06）。

## 为什么用 TypedDict 而不是 dataclass

LangGraph 的 StateGraph 用「部分字典」驱动节点：节点只返回自己更新的键，框架负责合并。
用 dataclass 会让"部分更新"变得别扭，也更容易在中断恢复时丢字段。

## 为什么只放可序列化数据

`facts`（设备档案/订单/库存等原始对象）**不放进 state**：
- 诊断节点在同一轮里完成"取数 → 组装 → 证据回溯"，之后不再需要原始对象；
- F-09 会给图挂 checkpointer（`interrupt()` 人工确认），届时 state 必须能 JSON 序列化，
  把 ORM/数据类塞进去会在恢复时爆炸。这条约束现在就守住，后面加中断不用返工。
"""

from __future__ import annotations

from typing import Any, TypedDict


class FaultState(TypedDict, total=False):
    """一次故障诊断运行的状态。

    字段生命周期：
    - `run_id / inner_code / window_hours`：入口写入，后续只读；
    - `step_no / tool_calls`：诊断节点写入（一条 step + 若干工具调用）；
    - `diagnosis / evidence_count / dropped_count / dispatch_allowed`：诊断结论；
    - `stop_reason / error`：终止原因（页面直接展示）。
    """

    run_id: str
    inner_code: str
    window_hours: int
    step_no: int
    tool_calls: list[dict[str, Any]]
    diagnosis: dict[str, Any] | None
    evidence_count: int
    dropped_count: int
    dispatch_allowed: bool
    stop_reason: str | None
    error: str | None
