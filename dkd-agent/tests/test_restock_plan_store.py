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
    PLAN_STATUS_ORDERING,
    PLAN_STATUS_SUGGESTED,
    RestockItem,
    RestockPlan,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    """记录 SQL 与参数；可按调用序返回预置行/影响行数。

    `rowcount` 是 1-8 的乐观锁语义所在：`UPDATE ... WHERE status = 期望值` 到底抢没抢到，
    完全取决于 MySQL 返回的影响行数（SQL 文本本身看不出结果）。
    """

    def __init__(
        self,
        rows_by_call: list[list[dict[str, Any]]] | None = None,
        rowcount_by_call: list[int] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self._rows_by_call = rows_by_call or []
        self._rowcount_by_call = rowcount_by_call or []

    async def execute(self, stmt: Any, params: dict[str, Any]) -> FakeResult:
        self.calls.append((str(stmt), dict(params)))
        index = len(self.calls) - 1
        rows = self._rows_by_call[index] if index < len(self._rows_by_call) else []
        rowcount = self._rowcount_by_call[index] if index < len(self._rowcount_by_call) else 0
        return FakeResult(rows, rowcount=rowcount)

    async def commit(self) -> None:
        self.commits += 1

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakeFactory:
    def __init__(
        self,
        rows_by_call: list[list[dict[str, Any]]] | None = None,
        rowcount_by_call: list[int] | None = None,
    ) -> None:
        self._rows_by_call = rows_by_call or []
        self._rowcount_by_call = rowcount_by_call or []
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(self._rows_by_call, self._rowcount_by_call)
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
    # 唯一键包含软删行：不复活就再也无法为同一天生成计划（回归护栏）
    assert "del_flag = '0'" in sql
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


# --------------------------------------------------------------------------------------
# 1-8：建单占位（CAS 乐观锁）、释放、接单人负载
# --------------------------------------------------------------------------------------


def test_ordering_status_matches_state_machine():
    """7-建单中在两处各定义了一份（存储层不 import 领域层，避免循环依赖），必须一致。"""
    assert store_mod.ORDERING_STATUS == PLAN_STATUS_ORDERING
    assert sorted(store_mod.CLAIMABLE_STATUSES) == [1, 2, 6], "可认领的只有 建议/已调整/待指派"


async def test_claim_for_order_is_a_conditional_update():
    """核心契约：抢占建单权 = `UPDATE ... WHERE status = 期望值`，靠影响行数定胜负。"""
    factory = FakeFactory(rowcount_by_call=[1])
    claimed = await SqlPlanStore(factory).claim_for_order(
        date(2026, 9, 21), 80, expected_status=PLAN_STATUS_SUGGESTED, by="agent:rid-1"
    )

    assert claimed is True
    sql = factory.last.calls[0][0].lower()
    assert sql.strip().startswith("update")
    assert "set status = :ordering_status" in sql, "认领必须把状态写成 7-建单中"
    assert "and status = :expected_status" in sql, "CAS 必须比对期望状态（否则会覆盖别人的修改）"
    assert "del_flag = '0'" in sql, "已软删的行不可被认领"
    assert "delete" not in sql, "本模块不得出现 DELETE（AGENTS §7.4）"
    assert "tb_" not in sql, "只写 agent_* 自有表（AGENTS §7.3）"

    params = factory.last.calls[0][1]
    assert params["ordering_status"] == PLAN_STATUS_ORDERING == 7
    assert params["expected_status"] == PLAN_STATUS_SUGGESTED
    assert params["vm_id"] == 80 and params["plan_date"] == "2026-09-21"
    assert factory.last.commits == 1, "认领必须落库（不能留在未提交事务里等回调）"


async def test_claim_for_order_losing_the_race_returns_false():
    """并发失败方：影响行数 0 → False（调用方据此返回 409，而不是去建第二张工单）。"""
    factory = FakeFactory(rowcount_by_call=[0])
    claimed = await SqlPlanStore(factory).claim_for_order(
        date(2026, 9, 21), 80, expected_status=PLAN_STATUS_SUGGESTED, by="agent:rid-2"
    )
    assert claimed is False


@pytest.mark.parametrize("expected", [7, 4, 3, 5])
async def test_claim_for_order_rejects_non_claimable_statuses(expected: int):
    """拒绝路径：不得认领“建单中/已建单/已跳过/已复盘”。

    传 7 进来等于“把别人刚拿到的锁再抢一次”——正是并发重复建单的成因，
    所以是程序错误，直接报错而不是静默返回 False（静默会让 bug 一直藏在日志里）。
    另一条要点：**校验发生在连接数据库之前**（factory 里一个会话都没建）。
    """
    factory = FakeFactory()
    with pytest.raises(ValueError, match="不是可认领状态"):
        await SqlPlanStore(factory).claim_for_order(
            date(2026, 9, 21), 80, expected_status=expected, by="agent:rid-3"
        )
    assert factory.sessions == [], "非法入参不得发出任何 SQL"


@pytest.mark.parametrize("revert_status", [7, 4, 3, 5])
async def test_release_order_claim_rejects_non_claimable_statuses(revert_status: int):
    """拒绝路径：释放只能回到认领前状态，不能遗留锁或复活终态计划。"""
    factory = FakeFactory()
    with pytest.raises(ValueError, match="不是可恢复状态"):
        await SqlPlanStore(factory).release_order_claim(
            date(2026, 9, 21), 80, revert_status=revert_status, by="agent:test"
        )
    assert factory.sessions == [], "非法恢复状态不得发出任何 SQL"


async def test_release_order_claim_only_releases_the_placeholder():
    """释放（失败补偿）：7 → 原状态，且只有持有者能释放（WHERE status = 7）。"""
    factory = FakeFactory(rowcount_by_call=[1])
    released = await SqlPlanStore(factory).release_order_claim(
        date(2026, 9, 21),
        80,
        revert_status=PLAN_STATUS_SUGGESTED,
        by="agent:rid-1",
    )

    assert released is True
    sql = factory.last.calls[0][0].lower()
    assert "and status = :ordering_status" in sql, "否则会把别人刚抢到的占位释放掉"
    assert "set status = :revert_status" in sql
    params = factory.last.calls[0][1]
    assert params["revert_status"] == PLAN_STATUS_SUGGESTED
    assert params["ordering_status"] == PLAN_STATUS_ORDERING


async def test_release_order_claim_returns_false_when_not_holder():
    factory = FakeFactory(rowcount_by_call=[0])
    released = await SqlPlanStore(factory).release_order_claim(
        date(2026, 9, 21), 80, revert_status=2, by="agent:rid-9"
    )
    assert released is False, "不是持有者时如实返回 False（调用方只记日志，不改库）"


async def test_count_open_by_assignee_groups_and_ignores_soft_deleted():
    """负载口径：当日每人占用计划数；不含软删行、不含未派人的行、不按状态筛。"""
    rows = [{"assignee_id": 6, "open_count": 3}, {"assignee_id": 7, "open_count": 1}]
    factory = FakeFactory(rows_by_call=[rows])

    loads = await SqlPlanStore(factory).count_open_by_assignee(date(2026, 9, 21))

    assert loads == {6: 3, 7: 1}
    sql = factory.last.calls[0][0].lower()
    assert "assignee_id is not null" in sql, "未派人的计划不计入谁的负载"
    assert "del_flag = '0'" in sql
    assert "group by assignee_id" in sql
    assert "status" not in sql.split("from")[0].replace("select", ""), (
        "不按状态筛：已完成/已复盘的也算这个人今天干过的活（否则上午忙完下午又派给他）"
    )


async def test_count_open_by_assignee_returns_empty_mapping_not_none():
    factory = FakeFactory(rows_by_call=[[]])
    assert await SqlPlanStore(factory).count_open_by_assignee(date(2026, 9, 21)) == {}
