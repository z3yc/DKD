"""F-10 审核官：维修工单完成后的"是否真的修好"复核与四分类结论。

## 为什么是纯逻辑模块

审核官要回答的问题只有一句：**这次维修是不是真的把设备修好了**。
它不需要 LLM 发挥，四分类是**可枚举、可审计**的判定（与故障图"代码路由"同一思路），
因此本模块不连库、不调模型：
- 工单事实由 `app.tools.review_tools.get_task_detail` 提供；
- 修复前后订单异常由现有 `read_tools.list_order_anomalies` 提供（复用，不新查库）；
- 本模块只负责分类、证据点名和"缺什么数据"的说明。

## 四分类与硬约束（对齐设计稿 §5.3）

| category | 含义 | 必须带上 |
| --- | --- | --- |
| `fix_verified` | 修复前有异常、修复后消失，且派单对象与提案一致 | 修复前异常计数 + 工单号 |
| `not_fixed` | 修复后仍有异常 | **点名哪条证据表明没修好**（订单号） |
| `wrong_dispatch` | 执行人 ≠ 提案接单人 | **点名依据错在哪**（期望/实际 + 依据） |
| `evidence_insufficient` | 未完成/非维修/无数据/缺提案 | **列出要补的数据**，禁止"再观察" |

判定优先级（互斥）：**证据不足 > 派错人 > 未修复 > 已验证**。
理由：证据不足时任何结论都是猜；派错人即使"碰巧修好了"也仍是审核要暴露的问题；
只有前两者都不成立时，才讨论修复效果本身。

## 与 D8 的关系

D8 决定审核官"先做页面点复核"。本模块只做判定，触发入口（HTTP/网关/页面）在后续切片接，
**不改动 M3 已验收的 `fault_graph`**，避免给已上线链路加回归面。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from app.tools.review_tools import (
    TASK_STATUS_CANCEL,
    TASK_STATUS_FINISH,
    TASK_TYPE_REPAIR,
    RepairEffectiveness,
    RepairTaskDetail,
)

# 四分类（前端/审计页按这四个值渲染，不得拼写漂移）
CATEGORY_FIX_VERIFIED = "fix_verified"
CATEGORY_NOT_FIXED = "not_fixed"
CATEGORY_WRONG_DISPATCH = "wrong_dispatch"
CATEGORY_EVIDENCE_INSUFFICIENT = "evidence_insufficient"
REVIEW_CATEGORIES = frozenset(
    {
        CATEGORY_FIX_VERIFIED,
        CATEGORY_NOT_FIXED,
        CATEGORY_WRONG_DISPATCH,
        CATEGORY_EVIDENCE_INSUFFICIENT,
    }
)

_TASK_STATUS_LABELS = {1: "待接单", 2: "进行中", 3: "已取消", 4: "已完成"}


class ReviewEvidence(BaseModel):
    """一条可点名的证据（页面点它应能回到对应工单/订单/提案）。"""

    type: str
    ref: str | int | None = None
    detail: str


class ReviewConclusion(BaseModel):
    """审核结论（四分类 + 证据 + 缺什么数据）。"""

    category: str
    summary: str
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    required_data: list[str] = Field(default_factory=list)
    task_id: int | None = None
    task_code: str | None = None
    expected_assignee_id: int | None = None
    actual_assignee_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """转成前端契约（camelCase，与故障接口一致）。"""
        return {
            "category": self.category,
            "summary": self.summary,
            "evidence": [item.model_dump() for item in self.evidence],
            "requiredData": list(self.required_data),
            "taskId": self.task_id,
            "taskCode": self.task_code,
            "expectedAssigneeId": self.expected_assignee_id,
            "actualAssigneeId": self.actual_assignee_id,
        }


def _insufficient(
    summary: str,
    *,
    required_data: list[str],
    task: RepairTaskDetail | None = None,
    evidence: list[ReviewEvidence] | None = None,
) -> ReviewConclusion:
    """构造"证据不足"结论：必须写清需要补什么（禁止"再观察"式空话）。"""
    return ReviewConclusion(
        category=CATEGORY_EVIDENCE_INSUFFICIENT,
        summary=summary,
        evidence=evidence or [],
        required_data=required_data,
        task_id=task.task_id if task is not None else None,
        task_code=task.task_code if task is not None else None,
    )


def assess_repair(
    *,
    task: RepairTaskDetail | None,
    proposal: Mapping[str, Any] | None,
    effectiveness: RepairEffectiveness,
) -> ReviewConclusion:
    """给出审核四分类结论（纯函数）。

    @param task 工单详情（`get_task_detail` 的结果；不存在时为 None）
    @param proposal 派单提案（`agent_step` step2 的 `evidence_json`，含 assigneeId/assigneeBasis）
    @param effectiveness 修复效果判定（`check_repair_effective` 的结果）
    @return 四分类结论，含证据与所需补充数据
    """
    # 1) 工单不存在 / 不是维修单 / 未完成 —— 都不能判"修好"
    if task is None:
        return _insufficient(
            "未找到对应工单，无法复核维修效果",
            required_data=["正确的 taskId", "或补录该设备的维修工单"],
        )
    if task.product_type_id != TASK_TYPE_REPAIR:
        return _insufficient(
            f"工单 {task.task_code or task.task_id} 不是维修工单"
            f"（productTypeId={task.product_type_id}），不能用它复核维修效果",
            required_data=["taskId 对应的维修工单（productTypeId=3）"],
            task=task,
        )
    if task.task_status != TASK_STATUS_FINISH:
        status_label = _TASK_STATUS_LABELS.get(task.task_status, f"未知({task.task_status})")
        if task.task_status == TASK_STATUS_CANCEL:
            return _insufficient(
                f"工单 {task.task_code or task.task_id} 已取消，没有可复核的维修作业",
                required_data=["重新派单并完成维修后的工单"],
                task=task,
            )
        return _insufficient(
            f"工单 {task.task_code or task.task_id} 当前状态为「{status_label}」，需完成后才能复核",
            required_data=["待工单完成（task_status=4）", "工单完成时间"],
            task=task,
        )

    # 2) 派单审计：没有提案或没有选人依据时，不能宣称"派对了"
    if proposal is None:
        return _insufficient(
            "缺少派单提案，无法判断是否派对人",
            required_data=["agent_step 中 step2 的派单提案（assigneeId + assigneeBasis）"],
            task=task,
        )
    expected_assignee = proposal.get("assigneeId")
    assignee_basis = proposal.get("assigneeBasis")
    if expected_assignee is None or not isinstance(assignee_basis, Mapping) or not assignee_basis:
        return _insufficient(
            "派单提案缺少接单人或选人依据，无法审计是否派错",
            required_data=[
                "派单提案中的 assigneeId",
                "选人依据 assigneeBasis（区域/候选数/在途数）",
            ],
            task=task,
        )
    if task.user_id is None:
        return _insufficient(
            "工单缺少执行人（user_id），无法对比派单提案",
            required_data=["工单执行人 user_id"],
            task=task,
        )
    if task.user_id != expected_assignee:
        return ReviewConclusion(
            category=CATEGORY_WRONG_DISPATCH,
            summary=(
                f"实际执行人（{task.user_name or task.user_id}，user_id={task.user_id}）"
                f"与提案接单人（assigneeId={expected_assignee}）不一致，"
                "派单依据需要复核（区域/技能/负载）"
            ),
            evidence=[
                ReviewEvidence(
                    type="dispatch",
                    ref=task.task_id,
                    detail=(
                        f"提案 assigneeId={expected_assignee}，实际 user_id={task.user_id}；"
                        f"选人依据(assigneeBasis)={dict(assignee_basis)}"
                    ),
                )
            ],
            required_data=[],
            task_id=task.task_id,
            task_code=task.task_code,
            expected_assignee_id=int(expected_assignee),
            actual_assignee_id=int(task.user_id),
        )

    # 3) 修复效果：未修复必须点名证据；无数据不得判"修好"
    if effectiveness.status == "not_fixed":
        if not effectiveness.evidence:
            # 防御：状态说"没修好"却没证据，不能硬给结论（宁可证据不足）
            return _insufficient(
                "判定为未修复但缺少订单异常证据，无法点名",
                required_data=["修复后窗口内的异常订单明细"],
                task=task,
            )
        named = "、".join(str(item.get("ref")) for item in effectiveness.evidence[:3])
        more = " 等" if effectiveness.after_count > len(effectiveness.evidence[:3]) else ""
        return ReviewConclusion(
            category=CATEGORY_NOT_FIXED,
            summary=(
                f"工单 {task.task_code or task.task_id} 完成后，修复后窗口内仍有 "
                f"{effectiveness.after_count} 条订单异常（{named}{more}），不能认定为已修复"
            ),
            evidence=[ReviewEvidence(**item) for item in effectiveness.evidence],
            required_data=[],
            task_id=task.task_id,
            task_code=task.task_code,
            expected_assignee_id=int(expected_assignee),
            actual_assignee_id=int(task.user_id),
        )
    if effectiveness.status == "no_data":
        return _insufficient(
            effectiveness.note or "修复前后窗口无可比对数据，无法判断维修效果",
            required_data=[
                "修复后至少一个完整窗口（建议 24h）的订单数据",
                "工单完成时间（用于切分窗口）",
            ],
            task=task,
        )

    # 4) 已验证
    return ReviewConclusion(
        category=CATEGORY_FIX_VERIFIED,
        summary=(
            f"工单 {task.task_code or task.task_id} 完成后，修复后窗口内未再出现订单异常"
            f"（修复前 {effectiveness.before_count} 条），判定维修生效"
        ),
        evidence=[
            ReviewEvidence(
                type="task",
                ref=task.task_id,
                detail=(
                    f"维修工单 {task.task_code or task.task_id} 已完成，"
                    f"执行人 {task.user_name or task.user_id}"
                ),
            ),
            *[ReviewEvidence(**item) for item in effectiveness.evidence],
        ],
        required_data=[],
        task_id=task.task_id,
        task_code=task.task_code,
        expected_assignee_id=int(expected_assignee),
        actual_assignee_id=int(task.user_id),
    )


__all__ = [
    "CATEGORY_EVIDENCE_INSUFFICIENT",
    "CATEGORY_FIX_VERIFIED",
    "CATEGORY_NOT_FIXED",
    "CATEGORY_WRONG_DISPATCH",
    "REVIEW_CATEGORIES",
    "ReviewConclusion",
    "ReviewEvidence",
    "assess_repair",
]
