"""补货 state schema 契约测试（任务 0-13 的可执行冻结）。

冻结不能只写在文档里——这里用测试把「字段清单」「JSON 可序列化」「映射语义」
「状态机合法迁移」「范围夹取拒绝路径」钉住，改 schema 必须改测试，从而强制走评审。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.graphs.restock_state import (
    ALLOWED_TRANSITIONS,
    FROZEN_FIELDS,
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_REVIEWED,
    PLAN_STATUS_SKIPPED,
    PLAN_STATUS_SUGGESTED,
    PLAN_STATUS_UNASSIGNED,
    TASK_TYPE_SUPPLY,
    RestockItem,
    RestockPlan,
    can_transition,
    empty_state,
)

# --- ① 字段冻结：增删字段必须改这里，从而强制走评审 ---

EXPECTED_FROZEN_FIELDS = frozenset(
    {
        "plan_date",
        "trigger_type",
        "request_id",
        "candidate_vm_ids",
        "inventory_rows",
        "sales_baseline",
        "pending_restock_tasks",
        "employee_directory",
        "plans",
        "calibration_notes",
        "adjustments",
        "decisions",
        "reviewed_by",
        "created_tasks",
        "failures",
        "review_metrics",
        "summary",
    }
)


def test_state_fields_are_frozen():
    """冻结字段清单（变更须走 docs 冻结稿 §6 的兼容性评审流程）。"""
    assert FROZEN_FIELDS == EXPECTED_FROZEN_FIELDS


def test_state_is_json_serializable():
    """state 必须可 JSON 序列化——checkpointer 用它落盘，放自定义对象会在升级时炸。"""
    state = empty_state(plan_date="2026-09-21", trigger_type=2, request_id="rid-1")
    plan = RestockPlan(
        plan_date="2026-09-21",
        vm_id=1,
        inner_code="VM-001",
        region_id=3,
        items=[_item()],
        assignee_id=9,
    )
    state["plans"] = [plan.model_dump()]
    dumped = json.dumps(state, ensure_ascii=False)
    assert json.loads(dumped)["plans"][0]["inner_code"] == "VM-001"


def test_empty_state_types_are_stable():
    """空 state 的字段类型稳定（防止有人把 list 写成 dict 之类的静默变更）。"""
    state = empty_state(plan_date="2026-09-21", trigger_type=2, request_id="r")
    assert isinstance(state["candidate_vm_ids"], list)
    assert isinstance(state["sales_baseline"], dict)
    assert state["created_tasks"] == []
    assert state["reviewed_by"] is None


# --- ② 明细模型：范围夹取与拒绝路径 ---


def _item(**overrides: object) -> RestockItem:
    base: dict[str, object] = {
        "sku_id": 11,
        "sku_name": "可乐",
        "channel_id": 5,
        "channel_code": "A1",
        "current_quantity": 2,
        "max_capacity": 10,
        "suggested_quantity": 6,
        "after_restock_quantity": 8,
        "estimated_days": 3,
        "priority": 2,
        "reason": "近 7 天日均 2.1 件，低于预警值",
    }
    base.update(overrides)
    return RestockItem(**base)  # type: ignore[arg-type]


def test_item_ok():
    item = _item()
    assert item.suggested_quantity == 6


def test_item_rejects_quantity_exceeding_capacity_room():
    """拒绝路径：补货量不得超出「容量 - 现库存」。"""
    with pytest.raises(ValidationError, match="超出可补空间"):
        _item(suggested_quantity=9, after_restock_quantity=11)


def test_item_rejects_capacity_mistaken_as_quantity():
    """最高价值的一条：把「容量」当「补货量」填入必须被拦下。

    若放过，dkd-app 补货完成时会把库存加上容量值 → 库存虚增 → 触发错误的下一轮建议。
    """
    with pytest.raises(ValidationError, match="超出可补空间"):
        _item(suggested_quantity=10, after_restock_quantity=12)  # 10 = max_capacity


def test_item_rejects_stock_over_capacity():
    with pytest.raises(ValidationError, match="超过容量"):
        _item(current_quantity=12)


def test_item_rejects_inconsistent_after_quantity():
    with pytest.raises(ValidationError, match="不一致"):
        _item(after_restock_quantity=7)


def test_item_rejects_negative_and_bad_priority():
    with pytest.raises(ValidationError):
        _item(suggested_quantity=-1, after_restock_quantity=1)
    with pytest.raises(ValidationError):
        _item(priority=5)


# --- ③ 映射语义：suggested_quantity → expectCapacity（不是 max_capacity）---


def test_item_maps_quantity_to_expect_capacity():
    payload = _item().to_task_detail_payload()
    assert payload["expectCapacity"] == 6  # = suggested_quantity
    assert payload["expectCapacity"] != 10  # 绝不能是 max_capacity
    assert set(payload) == {"channelCode", "expectCapacity", "skuId", "skuName", "skuImage"}


def test_plan_maps_to_task_dto_with_device_level_granularity():
    """按设备整单：details[] 承载多货道，productTypeId=2（补货）。"""
    plan = RestockPlan(
        plan_date="2026-09-21",
        vm_id=1,
        inner_code="VM-001",
        assignee_id=9,
        items=[
            _item(),
            _item(channel_id=6, channel_code="A2", suggested_quantity=4, after_restock_quantity=6),
        ],
    )
    dto = plan.to_task_dto(assignor_id=100)
    assert dto["innerCode"] == "VM-001"
    assert dto["productTypeId"] == TASK_TYPE_SUPPLY
    assert dto["userId"] == 9
    assert len(dto["details"]) == 2
    assert [d["expectCapacity"] for d in dto["details"]] == [6, 4]
    assert plan.sku_count == 2 and plan.total_quantity == 10


def test_plan_without_assignee_cannot_build_task_dto():
    """拒绝路径：无接单人不得建单（Java 侧会抛「员工区域与设备区域不一致」）。"""
    plan = RestockPlan(
        plan_date="2026-09-21",
        vm_id=1,
        inner_code="VM-001",
        status=PLAN_STATUS_UNASSIGNED,
        items=[_item()],
    )
    with pytest.raises(ValueError, match="缺少接单人"):
        plan.to_task_dto()


# --- ④ 状态机：只允许合法迁移（幂等的应用层防线）---


def test_status_machine_allows_expected_transitions():
    assert can_transition(PLAN_STATUS_SUGGESTED, PLAN_STATUS_ORDERED)
    assert can_transition(PLAN_STATUS_SUGGESTED, PLAN_STATUS_UNASSIGNED)
    assert can_transition(PLAN_STATUS_UNASSIGNED, PLAN_STATUS_ORDERED)
    assert can_transition(PLAN_STATUS_ORDERED, PLAN_STATUS_REVIEWED)


def test_status_machine_rejects_illegal_transitions():
    # 终态不可再变更
    assert not can_transition(PLAN_STATUS_ORDERED, PLAN_STATUS_SUGGESTED)
    assert not can_transition(PLAN_STATUS_REVIEWED, PLAN_STATUS_ORDERED)
    assert not can_transition(PLAN_STATUS_SKIPPED, PLAN_STATUS_ORDERED)
    # 不能跳过确认直接复盘
    assert not can_transition(PLAN_STATUS_SUGGESTED, PLAN_STATUS_REVIEWED)


def test_status_codes_match_ddl_comment():
    """状态码必须与 DDL 列注释一致（三端语义不能漂移）。"""
    from pathlib import Path

    ddl = Path(__file__).resolve().parents[2] / "docs" / "ddl" / "agent_tables.sql"
    text = ddl.read_text(encoding="utf-8")
    assert "1-建议 2-已调整 3-已跳过 4-已建单 5-已复盘 6-待指派" in text
    assert sorted(ALLOWED_TRANSITIONS) == [1, 2, 3, 4, 5, 6]
