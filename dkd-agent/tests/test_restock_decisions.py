"""补货计划状态机单测（排期任务 1-6）。

纯函数、零 IO：不连库、不起图、不看时钟。**拒绝路径优先**（AGENTS §8）——
「该拒绝的没拒绝」比「该通过的没通过」严重得多：一个终态计划被复活，
3-6 的复盘口径就再也对不上了。
"""

from __future__ import annotations

import pytest

from app.graphs.restock_decisions import (
    ACTION_ADJUST,
    ACTION_ASSIGN,
    ACTION_CONFIRM,
    ACTION_SKIP,
    RESULT_ADJUSTED,
    RESULT_ALREADY_ORDERED,
    RESULT_ASSIGNED,
    RESULT_PENDING_ORDER,
    RESULT_SKIPPED,
    RESULT_UNASSIGNED,
    DecisionRejectedError,
    ItemOverride,
    PlanDecision,
    apply_decision,
    parse_decisions,
    summarize_plan,
)
from app.graphs.restock_state import (
    PLAN_STATUS_ADJUSTED,
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_REVIEWED,
    PLAN_STATUS_SKIPPED,
    PLAN_STATUS_SUGGESTED,
    PLAN_STATUS_UNASSIGNED,
    RestockItem,
    RestockPlan,
)


def _item(*, code: str = "1-1", qty: int = 8, current: int = 2, capacity: int = 10) -> RestockItem:
    return RestockItem(
        sku_id=1,
        sku_name="可口可乐 330ml",
        channel_id=4732,
        channel_code=code,
        current_quantity=current,
        max_capacity=capacity,
        suggested_quantity=qty,
        after_restock_quantity=current + qty,
        estimated_days=2,
        priority=3,
        reason="日均需求 3 件/天；现库存 2，建议补 8。",
    )


def _plan(
    *,
    status: int = PLAN_STATUS_SUGGESTED,
    assignee_id: int | None = None,
    items: list[RestockItem] | None = None,
) -> RestockPlan:
    return RestockPlan(
        plan_date="2026-09-21",
        vm_id=80,
        inner_code="A1000001",
        region_id=3,
        status=status,
        assignee_id=assignee_id,
        assignee_name="孙权" if assignee_id else None,
        items=items or [_item()],
    )


# --------------------------------------------------------------------------------------
# 确认：无接单人不得建单；已建单重复确认是幂等的
# --------------------------------------------------------------------------------------


def test_confirm_without_assignee_moves_to_unassigned_and_never_orders():
    plan, result, note = apply_decision(_plan(), PlanDecision(vm_id=80, action=ACTION_CONFIRM))
    assert result == RESULT_UNASSIGNED
    assert plan.status == PLAN_STATUS_UNASSIGNED
    assert plan.unassigned_reason, "必须留下“为什么没接单人”的原因给人工处理"
    assert "待指派" in note


def test_confirm_with_assignee_is_allowed_but_status_waits_for_callback():
    plan, result, note = apply_decision(
        _plan(assignee_id=10), PlanDecision(vm_id=80, action=ACTION_CONFIRM)
    )
    assert result == RESULT_PENDING_ORDER
    assert "创建" in note
    # 状态先不动：只有 Java 回调成功后才由 create_tasks 置为 4（避免“没建成却显示已建单”）
    assert plan.status == PLAN_STATUS_SUGGESTED


def test_confirm_on_ordered_plan_is_idempotent_not_an_error():
    """排期 1-8 验收项 ②：重复确认应看到“已建单”，而不是 Java 防重的“设备有未完成工单”。"""
    plan, result, note = apply_decision(
        _plan(status=PLAN_STATUS_ORDERED, assignee_id=10),
        PlanDecision(vm_id=80, action=ACTION_CONFIRM),
        task_id=567,
    )
    assert result == RESULT_ALREADY_ORDERED
    assert plan.status == PLAN_STATUS_ORDERED
    assert "567" in note and "无需重复确认" in note


@pytest.mark.parametrize("terminal", [PLAN_STATUS_SKIPPED, PLAN_STATUS_REVIEWED])
def test_terminal_statuses_cannot_be_changed(terminal: int):
    """终态不可复活：否则“说好跳过”的计划几天后被建单，复盘口径作废。"""
    with pytest.raises(DecisionRejectedError, match="终态"):
        apply_decision(_plan(status=terminal), PlanDecision(vm_id=80, action=ACTION_CONFIRM))
    with pytest.raises(DecisionRejectedError, match="终态"):
        apply_decision(_plan(status=terminal), PlanDecision(vm_id=80, action=ACTION_ADJUST))
    with pytest.raises(DecisionRejectedError, match="终态"):
        apply_decision(_plan(status=terminal), PlanDecision(vm_id=80, action=ACTION_SKIP))


# --------------------------------------------------------------------------------------
# 调整：原因必填、越界拒绝（人工输入不静默夹取）
# --------------------------------------------------------------------------------------


def test_adjust_requires_reason():
    with pytest.raises(DecisionRejectedError, match="必须填写原因"):
        apply_decision(
            _plan(),
            PlanDecision(
                vm_id=80,
                action=ACTION_ADJUST,
                items=[ItemOverride(channel_code="1-1", suggested_quantity=5)],
            ),
        )
    # 只有空白字符也不算填了原因
    with pytest.raises(DecisionRejectedError, match="必须填写原因"):
        apply_decision(
            _plan(),
            PlanDecision(
                vm_id=80,
                action=ACTION_ADJUST,
                reason="   ",
                items=[ItemOverride(channel_code="1-1", suggested_quantity=5)],
            ),
        )


def test_adjust_requires_at_least_one_override():
    with pytest.raises(DecisionRejectedError, match="至少给出一个货道"):
        apply_decision(_plan(), PlanDecision(vm_id=80, action=ACTION_ADJUST, reason="手滑"))


def test_adjust_rejects_unknown_channel_with_channel_name():
    with pytest.raises(DecisionRejectedError, match="9-9"):
        apply_decision(
            _plan(),
            PlanDecision(
                vm_id=80,
                action=ACTION_ADJUST,
                reason="点位促销",
                items=[ItemOverride(channel_code="9-9", suggested_quantity=3)],
            ),
        )


def test_adjust_rejects_quantity_beyond_capacity_instead_of_clamping():
    """人工填错（把容量当数量）必须报错：静默夹取会让“我填了 20”变成“生效了 10”而无人知晓。"""
    with pytest.raises(DecisionRejectedError, match="超出可补空间"):
        apply_decision(
            _plan(items=[_item(qty=1, current=9, capacity=10)]),
            PlanDecision(
                vm_id=80,
                action=ACTION_ADJUST,
                reason="看错了",
                items=[ItemOverride(channel_code="1-1", suggested_quantity=5)],
            ),
        )


def test_adjust_updates_quantity_and_keeps_audit_trail_in_reason():
    plan, result, note = apply_decision(
        _plan(),
        PlanDecision(
            vm_id=80,
            action=ACTION_ADJUST,
            reason="本周末点位有活动",
            items=[ItemOverride(channel_code="1-1", suggested_quantity=6)],
        ),
    )
    assert result == RESULT_ADJUSTED
    assert plan.status == PLAN_STATUS_ADJUSTED
    item = plan.items[0]
    assert item.suggested_quantity == 6
    assert item.after_restock_quantity == 8
    assert "本周末点位有活动" in item.reason, "人工原因必须留在依据里（3-6 复盘的唯一线索）"
    assert "本周末点位有活动" in note


# --------------------------------------------------------------------------------------
# 跳过 / 指派
# --------------------------------------------------------------------------------------


def test_skip_requires_reason_and_becomes_terminal():
    with pytest.raises(DecisionRejectedError, match="必须填写原因"):
        apply_decision(_plan(), PlanDecision(vm_id=80, action=ACTION_SKIP))

    plan, result, note = apply_decision(
        _plan(), PlanDecision(vm_id=80, action=ACTION_SKIP, reason="该点位下周撤机")
    )
    assert result == RESULT_SKIPPED
    assert plan.status == PLAN_STATUS_SKIPPED
    assert "撤机" in note


def test_assign_from_unassigned_restores_orderable_state():
    plan, result, _ = apply_decision(
        _plan(status=PLAN_STATUS_UNASSIGNED),
        PlanDecision(vm_id=80, action=ACTION_ASSIGN, assignee_id=10, assignee_name="孙权"),
    )
    assert result == RESULT_ASSIGNED
    assert plan.status == PLAN_STATUS_ADJUSTED
    assert plan.assignee_id == 10
    assert plan.unassigned_reason is None


def test_assign_requires_assignee_and_is_rejected_for_ordered_plan():
    with pytest.raises(DecisionRejectedError, match="必须提供接单人"):
        apply_decision(
            _plan(status=PLAN_STATUS_UNASSIGNED), PlanDecision(vm_id=80, action=ACTION_ASSIGN)
        )
    with pytest.raises(DecisionRejectedError, match="不允许指派"):
        apply_decision(
            _plan(status=PLAN_STATUS_ORDERED, assignee_id=10),
            PlanDecision(vm_id=80, action=ACTION_ASSIGN, assignee_id=11),
        )


def test_decision_on_other_device_is_rejected():
    with pytest.raises(DecisionRejectedError, match="不一致"):
        apply_decision(_plan(), PlanDecision(vm_id=81, action=ACTION_CONFIRM))


def test_unsupported_action_is_rejected_at_model_level():
    with pytest.raises(ValueError, match="不支持的操作"):
        PlanDecision(vm_id=80, action="delete")


# --------------------------------------------------------------------------------------
# 入参解析（容错但不静默）
# --------------------------------------------------------------------------------------


def test_parse_decisions_accepts_envelope_single_object_and_list():
    assert [d.vm_id for d in parse_decisions([{"vmId": 80, "action": "confirm"}])] == [80]
    assert [d.vm_id for d in parse_decisions({"vmId": 80, "action": "confirm"})] == [80]
    assert [
        d.vm_id for d in parse_decisions({"decisions": [{"vmId": 80, "action": "confirm"}]})
    ] == [80]
    assert parse_decisions(None) == []


def test_parse_decisions_reports_index_of_invalid_entry():
    with pytest.raises(DecisionRejectedError, match="第 2 条"):
        parse_decisions([{"vmId": 80, "action": "confirm"}, {"vmId": 81, "action": "delete"}])
    with pytest.raises(DecisionRejectedError, match="必须是数组"):
        parse_decisions("confirm")


def test_parse_decisions_keeps_user_id_for_audit():
    decisions = parse_decisions([{"vmId": 80, "action": "skip", "reason": "撤机", "user_id": 7}])
    assert decisions[0].user_id == 7


def test_summarize_plan_exposes_prototype_fields_without_sensitive_data():
    summary = summarize_plan(_plan(assignee_id=10), plan_id=99)
    assert summary["id"] == 99
    assert summary["status"] == PLAN_STATUS_SUGGESTED
    assert summary["totalQuantity"] == 8
    assert summary["items"][0]["reason"], "原型 V2 的「依据」字段必须能展开看到"
    assert set(summary).isdisjoint({"mobile", "amount", "phone"})
