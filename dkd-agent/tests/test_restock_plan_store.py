"""补货计划存储单测（排期任务 1-6）。

不连 MySQL：用假会话断言 **SQL 形状**。理由与 1-2 相同——这几条约束靠“结果对不对”看不出来：
- upsert 只在原状态 ∈ (1,2) 时覆盖明细（否则重跑会把“已建单/已复盘”的计划改回建议）；
- 查询必须过滤软删 `del_flag='0'`；
- 通篇不得出现 DELETE（AGENTS §7.4）；
- 表名必须是 `agent_*` 自有表（写业务表是红线，AGENTS §7.3）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from app.db import AGENT_OWNED_TABLES, TableNotAllowedError, assert_table_allowed
from app.graphs import restock_plan_store as store_mod
from app.graphs.restock_decisions import PlanDecision
from app.graphs.restock_plan_store import TABLE, SqlPlanStore, _items_json
from app.graphs.restock_state import (
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_SUGGESTED,
    RestockItem,
    RestockPlan,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> FakeResult:
        self.calls.append((str(stmt), dict(params)))
        return FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakeFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session

    @property
    def last(self) -> FakeSession:
        return self.sessions[-1]


def _plan(*, status: int = PLAN_STATUS_SUGGESTED) -> RestockPlan:
    return RestockPlan(
        plan_date="2026-09-21",
        vm_id=80,
        inner_code="A1000001",
        region_id=3,
        status=status,
        assignee_id=10,
        assignee_name="孙权",
        items=[
            RestockItem(
                sku_id=1,
                sku_name="可口可乐 330ml",
                channel_id=4732,
                channel_code="1-1",
                current_quantity=0,
                max_capacity=10,
                suggested_quantity=6,
                after_restock_quantity=6,
                reason="日均需求 2.4 件/天；现库存 0，建议补 6。",
            )
        ],
    )


def test_store_table_is_agent_owned_not_business_table():
    """回归护栏：本模块只能写 `agent_*` 自有表。"""
    assert TABLE in AGENT_OWNED_TABLES
    with pytest.raises(TableNotAllowedError, match="业务表"):
        assert_table_allowed("tb_inventory", writable=True)


async def test_upsert_only_overwrites_items_when_status_is_editable():
    factory = FakeFactory()
    await SqlPlanStore(factory).upsert_plan(_plan(), by="system")

    sql = factory.last.calls[0][0].lower()
    assert "on duplicate key update" in sql
    # 关键约束（DDL 注释 127-131）：已建单/已复盘的计划不得被重跑覆盖
    assert "if(status in (1, 2), values(items), items)" in sql
    assert "if(status in (1, 2), values(sku_count), sku_count)" in sql
    assert "if(status in (1, 2), values(total_quantity), total_quantity)" in sql
    assert "delete" not in sql
    assert factory.last.commits == 1

    _, params = factory.last.calls[0]
    assert params["plan_date"] == "2026-09-21"
    assert params["vm_id"] == 80
    assert params["status"] == PLAN_STATUS_SUGGESTED
    assert params["sku_count"] == 1 and params["total_quantity"] == 6
    # 明细键名必须与 DDL 列注释一致（camelCase），否则前端要到处做字段名转换
    assert '"channelCode"' in params["items"] and '"suggestedQuantity"' in params["items"]


async def test_save_decision_writes_audit_columns_and_task_refs():
    factory = FakeFactory()
    await SqlPlanStore(factory).save_decision(
        _plan(status=PLAN_STATUS_ORDERED),
        by="7",
        adjusted_by=7,
        adjust_reason="周末有活动",
        adjusted_time=datetime(2026, 9, 21, 7, 30),
        task_id=999,
        task_code="202609210001",
    )

    sql = factory.last.calls[0][0].lower()
    assert sql.strip().startswith("update")
    assert "delete" not in sql
    assert "del_flag = '0'" in sql, "回写必须只针对未删除的行"

    _, params = factory.last.calls[0]
    assert params["status"] == PLAN_STATUS_ORDERED
    assert params["adjusted_by"] == 7
    assert params["adjust_reason"] == "周末有活动"
    assert params["task_id"] == 999
    assert params["task_code"] == "202609210001"
    assert params["adjusted_time"] == datetime(2026, 9, 21, 7, 30)


async def test_list_plans_filters_soft_deleted_and_limits():
    factory = FakeFactory()
    rows = await SqlPlanStore(factory).list_plans(date(2026, 9, 21), limit=50)

    sql = factory.last.calls[0][0].lower()
    assert "del_flag = '0'" in sql, "查询默认过滤软删（AGENTS §7.4）"
    assert "order by status, vm_id" in sql, "工作台按状态排序：待处理优先"
    assert sql.count(" limit :limit") == 1
    _, params = factory.last.calls[0]
    assert params["plan_date"] == date(2026, 9, 21)
    assert params["limit"] == 50
    assert rows == []


def test_items_json_is_utf8_safe_and_aligned_with_prototype_fields():
    payload = _items_json(_plan())
    assert "可口可乐" in payload, "中文必须原样落库（ensure_ascii=False，前端直接展示）"
    assert '"suggestedQuantity"' in payload, "落库键名与 DDL 注释一致（camelCase）"
    # 决策对象里带 user_id/原因，用于审计列回写
    decision = PlanDecision(vmId=80, action="skip", reason="撤机", userId=7)
    assert decision.user_id == 7 and decision.reason == "撤机"
    assert store_mod.TABLE == "agent_restock_plan"


def test_plan_round_trips_through_db_payload():
    """落库 → 还原：items 的 camelCase/snake_case 转换必须可逆，否则 1-7 读回来的计划会缺字段。"""
    plan = _plan()
    payload = store_mod._items_payload(plan)
    restored = store_mod.plan_from_row(
        {
            "plan_date": plan.plan_date,
            "vm_id": plan.vm_id,
            "inner_code": plan.inner_code,
            "region_id": plan.region_id,
            "node_id": plan.node_id,
            "status": plan.status,
            "assignee_id": plan.assignee_id,
            "assignee_name": plan.assignee_name,
            "items": payload,
        }
    )
    assert restored.vm_id == plan.vm_id
    assert restored.items[0].channel_code == "1-1"
    assert restored.items[0].suggested_quantity == plan.items[0].suggested_quantity


def test_items_from_db_tolerates_string_json_and_garbage():
    """MySQL JSON 列可能以字符串返回；脏数据只能跳过该行，不能让列表页整页 500。"""
    assert (
        store_mod.items_from_db('[{"channelCode": "1-1", "suggestedQuantity": 3}]')[0][
            "suggested_quantity"
        ]
        == 3
    )
    assert store_mod.items_from_db("不是 JSON") == []
    assert store_mod.items_from_db({"a": 1}) == []
    assert store_mod.items_from_db(None) == []
