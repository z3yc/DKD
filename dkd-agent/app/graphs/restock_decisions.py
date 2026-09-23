"""补货计划的人工决策状态机（Phase 1 / 任务 1-6）。

状态取值与迁移表来自 0-13 冻结稿（`restock_state.ALLOWED_TRANSITIONS`，与
`docs/ddl/agent_tables.sql:134-137` 的注释逐字一致）：

```
1-建议 ──┬─→ 2-已调整 ──┬─→ 4-已建单 ──→ 5-已复盘（终态）
         │              │
         └─→ 3-已跳过 ←──┘   3 → 1 仅由显式 restore 触发（需原因、限当天）
                  └────→ 6-待指派 ──→ 2/4
合法迁移：1→2/3/4/6，2→3/4/6，6→2/4，4→5，**3→1（restore）**
```

## 为什么要一个"纯函数状态机"而不是把判断写在节点里

1. **可单测**：拒绝路径（该拒绝的没拒绝比该通过的没通过严重得多，AGENTS §8）全部能用
   一个函数调用覆盖，不需要起图、不需要 DB；
2. **可复用**：定时任务重跑、前端点确认、运维补指派三条入口都要走同一套卡口——
   写在节点里就会出现三份逐渐走样的判断；
3. **可解释**：每次拒绝都返回“为什么不能这么干”的中文原因，直接回给运营（原型 V2 的 toast 文案）。

## 幂等（1-6 做基础版，1-8 做并发版）

- 「已建单」再点确认 → **不报错**，返回 `already_ordered`（运营看到“已建单”而不是
  “设备有未完成工单”这种 Java 侧防重异常，排期 1-8 验收项 ②）；
- 终态（3-已跳过 / 5-已复盘）不可复活：`DecisionRejectedError("已跳过/已复盘为终态…")`。
  这是**有意的**约束——否则复盘口径会被改写（说好的跳过，几天后又被建单）；
- 「建单中」（`PLAN_STATUS_ORDERING = 7`）拒绝一切人工操作（1-8 补）：该状态是
  `agent_restock_plan` 上的**建单占位**，由 store 层的条件 UPDATE 抢得，抢不到的并发请求被拒为 409，
  而不是产生两份工单；
- 并发重复确认的**唯一性保证**（唯一键 + 乐观锁）在 1-8 落地：
  `restock_plan_store.claim_for_order` / `release_order_claim` 的 CAS 才是排他位置，
  本模块仍只保证**单次决策**的正确性（纯函数，不碰库、不持时钟）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.graphs.restock_state import (
    PLAN_STATUS_ADJUSTED,
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_ORDERING,
    PLAN_STATUS_REVIEWED,
    PLAN_STATUS_SKIPPED,
    PLAN_STATUS_SUGGESTED,
    PLAN_STATUS_UNASSIGNED,
    RestockItem,
    RestockPlan,
    can_transition,
    status_label,
)

logger = logging.getLogger("dkd.agent.graphs.restock_decisions")

ACTION_CONFIRM = "confirm"
ACTION_ADJUST = "adjust"
ACTION_SKIP = "skip"
ACTION_ASSIGN = "assign"
ACTION_RESTORE = "restore"

ACTIONS = (ACTION_CONFIRM, ACTION_ADJUST, ACTION_SKIP, ACTION_ASSIGN, ACTION_RESTORE)

# 决策结果（调用方据此决定“要不要回调建单”“要不要提示已建单”）
RESULT_PENDING_ORDER = "pending_order"  # 已放行，交由建单回调
RESULT_ALREADY_ORDERED = "already_ordered"  # 重复确认，直接告知“已建单”
RESULT_UNASSIGNED = "unassigned"  # 无匹配接单人 → 6-待指派，不回调
RESULT_ADJUSTED = "adjusted"
RESULT_SKIPPED = "skipped"
RESULT_ASSIGNED = "assigned"
RESULT_RESTORED = "restored"

# 不可变更的终态：**3-已跳过可以通过显式 restore 复原**（原型 V2 的「恢复建议」按钮），
# 5-已复盘则是真正的终态（复盘结论一旦固化就不该被改写）。
TERMINAL_STATUSES = (PLAN_STATUS_SKIPPED, PLAN_STATUS_REVIEWED)


class DecisionRejectedError(ValueError):
    """非法决策（迁移不合法/缺必填原因/数量越界）——消息直接展示给运营，必须是中文人话。"""


class ItemOverride(BaseModel):
    """人工调整后的单条货道数量（字段名与前端/原型一致用 camelCase，同时容忍 snake_case）。"""

    model_config = ConfigDict(populate_by_name=True)

    channel_code: str = Field(alias="channelCode", min_length=1, max_length=32)
    suggested_quantity: int = Field(
        alias="suggestedQuantity", ge=0, description="人工给定的补货量（增量，不是容量）"
    )
    note: str | None = Field(
        default=None,
        max_length=460,
        description=(
            "该项的新依据（排期 1-7 的“按人工参数重算”用）：给了就用它替换基线依据——"
            "重算出的理由比原始基线更贴近当前参数，留着旧的会自相矛盾"
        ),
    )


class PlanDecision(BaseModel):
    """一份计划的决策入参（前端「确认/调整/跳过」提交的结构）。

    camelCase 别名 + `populate_by_name`：前端按原型传 `vmId/assigneeId`，
    Python 侧内部代码用 snake_case，两者都能构造——**不要求调用方记住是哪一种**。
    """

    model_config = ConfigDict(populate_by_name=True)

    vm_id: int = Field(alias="vmId")
    action: str
    reason: str | None = Field(default=None, max_length=500)
    items: list[ItemOverride] = Field(default_factory=list)
    assignee_id: int | None = Field(default=None, alias="assigneeId")
    assignee_name: str | None = Field(default=None, alias="assigneeName", max_length=64)
    user_id: int | None = Field(
        default=None,
        alias="userId",
        description="操作人（来自网关 X-Agent-User），写入 agent_restock_plan.adjusted_by",
    )

    @field_validator("action")
    @classmethod
    def _check_action(cls, value: str) -> str:
        if value not in ACTIONS:
            raise ValueError(f"不支持的操作 {value}（可选：{'/'.join(ACTIONS)}）")
        return value


def _reject(reason: str) -> DecisionRejectedError:
    return DecisionRejectedError(reason)


def _reject_transition(plan: RestockPlan, what: str) -> DecisionRejectedError:
    """状态不允许该操作时的统一文案。

    为什么用中文标签而不是状态数字：这条消息是**直接给运营看的**（原型 V2 的 toast），
    “当前状态 7 不允许跳过”对运营毫无意义，“当前状态「建单中」不允许跳过”才有指导性。
    """
    return _reject(f"当前状态「{status_label(plan.status)}」不允许{what}")


def _require_reason(decision: PlanDecision, *, what: str) -> str:
    """跳过/调整必须填原因。

    排期 1-7 的验收项，也是 3-6 复盘的唯一线索：没有原因的人工改动，
    事后无法区分“运营比模型更懂这个点位”还是“手滑点错了”。
    """
    reason = (decision.reason or "").strip()
    if not reason:
        raise _reject(f"{what}必须填写原因（用于复盘归因，不能留空）")
    return reason


def apply_adjust(plan: RestockPlan, decision: PlanDecision, *, reason: str) -> RestockPlan:
    """把人工给定的数量覆盖到计划明细上（越界即拒绝，不静默夹取）。

    为什么人工调整用**拒绝**而不是像 LLM 那样夹取：LLM 输出是不可信输入，
    夹取是防它胡说；人工输入是操作者意图，超容量说明他看错了数据（比如把容量填进了数量），
    静默夹取会让“我明明填了 20”变成“生效了 10”而无人知晓 —— 必须让他看见错误。
    """
    if not decision.items:
        raise _reject("调整操作必须至少给出一个货道的数量")
    overrides = {item.channel_code: item for item in decision.items}
    existing = {item.channel_code for item in plan.items}
    unknown = sorted(set(overrides) - existing)
    if unknown:
        raise _reject(f"货道 {','.join(unknown)} 不在本计划内，不能调整")

    new_items: list[RestockItem] = []
    for item in plan.items:
        override = overrides.get(item.channel_code)
        if override is None:
            new_items.append(item)
            continue
        room = item.max_capacity - item.current_quantity
        if override.suggested_quantity > room:
            raise _reject(
                f"货道 {item.channel_code} 调整后数量 {override.suggested_quantity} "
                f"超出可补空间 {room}（容量 {item.max_capacity} - 现库存 {item.current_quantity}）"
            )
        tag = f"；人工调整：{reason}"
        # 重算路径会用重算依据替换基线依据（两者参数不同，拼在一起自相矛盾）
        base_reason = override.note or item.reason
        new_items.append(
            item.model_copy(
                update={
                    "suggested_quantity": override.suggested_quantity,
                    "after_restock_quantity": item.current_quantity + override.suggested_quantity,
                    "reason": (base_reason + tag)[:500],
                }
            )
        )
    return plan.model_copy(update={"items": new_items, "status": PLAN_STATUS_ADJUSTED})


def apply_decision(
    plan: RestockPlan,
    decision: PlanDecision,
    *,
    user_id: int | None = None,
    now: datetime | None = None,
    task_id: int | None = None,
) -> tuple[RestockPlan, str, str | None]:
    """执行一次人工决策。

    @param task_id 已建单的计划在库里的工单号（RestockPlan 是冻结契约、不带 task_id，
        因此由调用方从 `agent_restock_plan` 读出后传入，用于“重复确认”的回显文案）
    @return `(新计划, 结果码, 说明)`；结果码见模块常量（RESULT_*），说明用于回显与留痕
    @raises DecisionRejectedError 非法迁移 / 缺原因 / 数量越界
    """
    if decision.vm_id != plan.vm_id:
        raise _reject(f"决策的设备 {decision.vm_id} 与计划设备 {plan.vm_id} 不一致")

    # user_id / now 是给调用方写审计列用的入参（agent_restock_plan.adjusted_by/adjusted_time）；
    # 状态机本身不做 IO（纯函数），拒绝路径才能不开图、不连库地单测。这里显式引用一次，
    # 表示“签名的一部分”，避免被误认为废参数而删掉（删了审计就会退化成 NULL）。
    _ = (user_id, now)

    # 0) 「恢复建议」：唯一被允许读 3-已跳过 的动作。必须带原因；
    #    “只能恢复当天”的时钟判断由服务层做——状态机不持有时钟，写死“今天”会让测试与
    #    跨时区部署都变脆（也让它能纯函数单测）。
    if decision.action == ACTION_RESTORE:
        reason = _require_reason(decision, what="恢复")
        if plan.status != PLAN_STATUS_SKIPPED:
            raise _reject(
                f"只有「已跳过」的计划可以恢复，当前状态为「{status_label(plan.status)}」"
            )
        if not can_transition(plan.status, PLAN_STATUS_SUGGESTED):
            raise _reject("状态机不允许从「已跳过」恢复")
        return (
            plan.model_copy(update={"status": PLAN_STATUS_SUGGESTED}),
            RESULT_RESTORED,
            f"已恢复建议：{reason}",
        )

    # 1) 终态保护：其余操作都不能把 3-已跳过/5-已复盘 拉回来
    if plan.status in TERMINAL_STATUSES:
        label = "已跳过" if plan.status == PLAN_STATUS_SKIPPED else "已复盘"
        raise _reject(f"该计划为「{label}」终态，不可再变更；如需重新补货请新建计划")

    # 2) 幂等：已建单再点确认 → 不报错，直接返回“已建单”（排期 1-8 验收项 ②）
    if plan.status == PLAN_STATUS_ORDERED and decision.action == ACTION_CONFIRM:
        return plan, RESULT_ALREADY_ORDERED, f"计划已建单（task_id={task_id}），无需重复确认"

    # 2b) 建单占位中（排期 1-8）：另一个请求已经抢到这一行的建单权。
    #     为什么不靠 can_transition 统一报错：它只能给出“当前状态 7 不允许…”这种无信息量的话，
    #     而运营此时真正需要知道的是“有人正在建单，我该等一下再刷新”。
    if plan.status == PLAN_STATUS_ORDERING:
        raise _reject("该计划正在建单中（可能由另一位运营同时操作），请稍后刷新后重试")

    if decision.action == ACTION_CONFIRM:
        if plan.assignee_id is None:
            # 无接单人不能建单（Java 侧要求 emp.regionId == vm.regionId）；
            # 分配策略与并发幂等属排期 1-8，这里只落到 6-待指派，绝不伪造接单人。
            new_plan = plan.model_copy(
                update={
                    "status": PLAN_STATUS_UNASSIGNED,
                    "unassigned_reason": "未匹配到同区域接单人，待人工指派",
                }
            )
            return new_plan, RESULT_UNASSIGNED, "未匹配到同区域接单人，已置为「待指派」"
        if not can_transition(plan.status, PLAN_STATUS_ORDERED):
            raise _reject_transition(plan, "建单")
        return plan, RESULT_PENDING_ORDER, "已确认，正在创建补货工单"

    if decision.action == ACTION_ADJUST:
        reason = _require_reason(decision, what="调整")
        if not can_transition(plan.status, PLAN_STATUS_ADJUSTED):
            raise _reject_transition(plan, "调整")
        return apply_adjust(plan, decision, reason=reason), RESULT_ADJUSTED, f"已调整：{reason}"

    if decision.action == ACTION_SKIP:
        reason = _require_reason(decision, what="跳过")
        if not can_transition(plan.status, PLAN_STATUS_SKIPPED):
            raise _reject_transition(plan, "跳过")
        return (
            plan.model_copy(update={"status": PLAN_STATUS_SKIPPED}),
            RESULT_SKIPPED,
            f"已跳过：{reason}",
        )

    if decision.action == ACTION_ASSIGN:
        if not decision.assignee_id:
            raise _reject("指派操作必须提供接单人")
        if not can_transition(plan.status, PLAN_STATUS_ADJUSTED):
            raise _reject_transition(plan, "指派")
        return (
            plan.model_copy(
                update={
                    "status": PLAN_STATUS_ADJUSTED,
                    "assignee_id": decision.assignee_id,
                    "assignee_name": decision.assignee_name,
                    "unassigned_reason": None,
                }
            ),
            RESULT_ASSIGNED,
            f"已指派给 {decision.assignee_name or decision.assignee_id}",
        )

    # Pydantic 的 action 校验已保证走不到这里；显式报错而不是静默什么都不做
    raise _reject(f"不支持的操作 {decision.action}")


def parse_decisions(raw: Any) -> list[PlanDecision]:
    """解析前端提交的决策数组（容错：单条非法不影响其它条）。

    为什么要容错而不是整体拒绝：批量确认时一条填错原因，不该让另外九台设备的确认全部失败；
    但非法的那条必须原样报错（返回给运营看），不能静默丢掉。
    """
    if raw is None:
        return []
    if isinstance(raw, dict) and "decisions" in raw:
        # 前端与网关可能提交 {"decisions": [...]} 信封（与 interrupt payload 对齐）
        raw = raw["decisions"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise _reject("decisions 必须是数组或对象")
    decisions: list[PlanDecision] = []
    for index, entry in enumerate(raw):
        try:
            decisions.append(PlanDecision(**entry))
        except (ValidationError, TypeError) as exc:
            raise _reject(f"第 {index + 1} 条决策不合法：{exc}") from exc
    return decisions


def summarize_plan(plan: RestockPlan, *, plan_id: int | None = None) -> dict[str, Any]:
    """给前端/日志的紧凑摘要（不含敏感信息，字段名对齐原型 V2）。"""
    return {
        "id": plan_id,
        "vmId": plan.vm_id,
        "innerCode": plan.inner_code,
        "status": plan.status,
        "skuCount": plan.sku_count,
        "totalQuantity": plan.total_quantity,
        "assigneeId": plan.assignee_id,
        "assigneeName": plan.assignee_name,
        "items": [
            {
                "channelCode": item.channel_code,
                "skuName": item.sku_name,
                "currentQuantity": item.current_quantity,
                "maxCapacity": item.max_capacity,
                "suggestedQuantity": item.suggested_quantity,
                "priority": item.priority,
                "estimatedDays": item.estimated_days,
                "reason": item.reason,
            }
            for item in plan.items
        ],
    }


__all__ = [
    "ACTION_ADJUST",
    "ACTION_ASSIGN",
    "ACTION_RESTORE",
    "ACTION_CONFIRM",
    "ACTION_SKIP",
    "ACTIONS",
    "DecisionRejectedError",
    "ItemOverride",
    "PlanDecision",
    "RESULT_ADJUSTED",
    "RESULT_ALREADY_ORDERED",
    "RESULT_ASSIGNED",
    "RESULT_PENDING_ORDER",
    "RESULT_RESTORED",
    "RESULT_SKIPPED",
    "RESULT_UNASSIGNED",
    "apply_decision",
    "parse_decisions",
    "summarize_plan",
]
