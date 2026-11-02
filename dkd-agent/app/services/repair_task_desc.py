"""维修工单备注的结论摘要组装（任务 2-5）。

## 为什么单独成纯函数

派单提案的 `desc` 会被写进 `tb_task.desc`（维修工单备注），是**运营在移动端唯一能看到的
诊断上下文**。它有三个不能靠“调用处各写一份”的要求：

1. **必须含结论依据**（置信度 + 依据），否则现场只看到“建议维修”，无法判断该不该信；
2. **必须列出异常证据与关联订单号**（异常订单清单附档）——维修人员要能在现场核对；
3. **必须有长度上限**：`desc` 是备注列，塞进整段 JSON 会挤掉别的信息，也可能超列宽。

纯函数 + 显式上限，才能用单测锁住这三条；组装逻辑放在 `fault_graph.build_dispatch_proposal`
里会让这些约束随调用处漂移。
"""

from __future__ import annotations

from typing import Any

# 备注列是给人看的：证据最多列 5 条、每条摘要截断，整体留 450 字符（含在途工单提示）。
MAX_EVIDENCE_ITEMS = 5
MAX_EVIDENCE_SUMMARY_CHARS = 40
MAX_ORDER_NUMBERS = 3
MAX_DESC_CHARS = 450

# 与 `app/services/diagnosis.py` 的结论字段一致（camelCase 出口）
_NO_CAUSE_NOTE = "说明：本结论不含病因推断（平台无结构化故障知识库），请现场核实后处置。"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _evidence_line(item: dict[str, Any]) -> str:
    severity = str(item.get("severity") or "")
    kind = str(item.get("kind") or "")
    summary = str(item.get("summary") or "").strip()
    head = f"- [{severity}] {kind}" if severity else f"- {kind}"
    if summary:
        return f"{head} · {_truncate(summary, MAX_EVIDENCE_SUMMARY_CHARS)}"
    return head


def _order_numbers(evidence: list[dict[str, Any]]) -> list[str]:
    """从证据 detail 里取关联订单号（去重、保序）。没有就返回空列表。"""
    numbers: list[str] = []
    for item in evidence:
        detail = item.get("detail")
        if not isinstance(detail, dict):
            continue
        for key in ("orderNo", "order_no"):
            value = detail.get(key)
            text = str(value).strip() if value is not None else ""
            if text and text not in numbers:
                numbers.append(text)
    return numbers


def compose_repair_task_desc(
    diagnosis: dict[str, Any],
    *,
    max_chars: int = MAX_DESC_CHARS,
) -> str:
    """把诊断结论组装成维修工单备注（有界、可核对、不含病因推断）。

    @param diagnosis 结论 payload（camelCase：innerCode/confidence/confidenceBasis/evidence/
                     recommendedActions）；缺字段时按“没有”处理，不抛异常
    @param max_chars 整体长度上限（默认 450）
    """
    inner_code = str(diagnosis.get("innerCode") or "").strip()
    head: list[str] = [f"【智能诊断建议】{inner_code}" if inner_code else "【智能诊断建议】"]

    confidence = str(diagnosis.get("confidence") or "").strip()
    basis = str(diagnosis.get("confidenceBasis") or "").strip()
    if confidence and basis:
        head.append(f"置信度：{confidence}（{basis}）")
    elif confidence or basis:
        head.append(f"置信度：{confidence or basis}")

    evidence = [item for item in (diagnosis.get("evidence") or []) if isinstance(item, dict)]
    body: list[str] = []
    if evidence:
        body.append(f"异常证据（{len(evidence)} 条）：")
        body.extend(_evidence_line(item) for item in evidence[:MAX_EVIDENCE_ITEMS])

    # 尾巴（其余条数提示 / 关联订单 / 平台动作 / 免责声明）**优先保留**：
    # 直接对整段做尾部截断会把「不含病因推断」这句最先砍掉，而它恰恰是最不能丢的。
    tail: list[str] = []
    if len(evidence) > MAX_EVIDENCE_ITEMS:
        tail.append(f"…其余 {len(evidence) - MAX_EVIDENCE_ITEMS} 条见决策审计页")
    order_numbers = _order_numbers(evidence)
    if order_numbers:
        tail.append("关联订单：" + "、".join(order_numbers[:MAX_ORDER_NUMBERS]))
    actions = [str(action) for action in (diagnosis.get("recommendedActions") or []) if action]
    if actions:
        tail.append("平台动作：" + "；".join(actions[:3]))
    tail.append(_NO_CAUSE_NOTE)

    tail_text = "\n".join(tail)
    budget = max(0, max_chars - len(tail_text) - 1)
    body_text = "\n".join(head + body)
    return _truncate(body_text, budget) + "\n" + tail_text


__all__ = [
    "MAX_DESC_CHARS",
    "MAX_EVIDENCE_ITEMS",
    "compose_repair_task_desc",
]
