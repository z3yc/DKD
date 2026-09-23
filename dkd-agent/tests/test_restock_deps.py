"""生产依赖实现（`SqlRestockDeps`）的 1-8 单测：接单人分配 + 建单并发保护。

分两层验：

1. **分配策略的接线**（而不是策略本身，策略见 test_restock_assignee.py）：
   区域怎么来、候选怎么取、负载从哪读、同一轮里怎么递增、缓存什么时候失效；
2. **建单的并发保护**：CAS 认领 → 回调 Java → 成功留 4/失败释放；
   认领抢不到时**绝不能**再回调 Java（否则就是重复建单）。

不连 MySQL、不调 Java、不调 LLM：store 与回调全部用假实现（AGENTS §8）。
真库那一层见 `test_restock_assignee_live.py` 与
`docs/scripts/verify-1-8-assignee-concurrency.py`。
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from app.graphs import restock_graph as graph_mod
from app.graphs.restock_plan_store import OrderClaimConflictError
from app.graphs.restock_state import (
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_SUGGESTED,
    PLAN_STATUS_UNASSIGNED,
    RestockItem,
    RestockPlan,
)
from app.tools.read_tools import Assignee, MachineProfile
from app.tools.task_tools import CallbackUnavailableError, CreatedTask

PLAN_DATE = "2026-09-21"


class FakeStore:
    """假计划存储：记录认领/释放调用，可按需返回成败。"""

    def __init__(
        self,
        *,
        loads: dict[int, int] | None = None,
        claim_result: bool = True,
        release_result: bool = True,
    ) -> None:
        self.loads = loads or {}
        self.claim_result = claim_result
        self.release_result = release_result
        self.claims: list[dict[str, Any]] = []
        self.releases: list[dict[str, Any]] = []
        self.load_calls: list[date] = []
        self.upserted: list[RestockPlan] = []

    async def count_open_by_assignee(self, plan_date: date) -> dict[int, int]:
        self.load_calls.append(plan_date)
        return dict(self.loads)

    async def claim_for_order(
        self, plan_date: date | str, vm_id: int, *, expected_status: int, by: str
    ) -> bool:
        self.claims.append(
            {
                "plan_date": str(plan_date),
                "vm_id": vm_id,
                "expected_status": expected_status,
                "by": by,
            }
        )
        return self.claim_result

    async def release_order_claim(
        self, plan_date: date | str, vm_id: int, *, revert_status: int, by: str
    ) -> bool:
        self.releases.append(
            {"plan_date": str(plan_date), "vm_id": vm_id, "revert_status": revert_status, "by": by}
        )
        return self.release_result

    async def upsert_plan(self, plan: RestockPlan, *, by: str = "system") -> None:
        self.upserted.append(plan)


class FakeCasStore(FakeStore):
    """**真的会互斥**的假 store：用一把锁模拟 `WHERE status = 期望值` 的原子性。

    为什么需要它：并发用例只能用“真会互相挡住”的实现来验证，
    用 `claim_result=True` 的固定返回就等于在测一个永远不冲突的世界。
    """

    def __init__(self, *, status: int = PLAN_STATUS_SUGGESTED) -> None:
        super().__init__()
        self.status = status
        self._lock = asyncio.Lock()

    async def claim_for_order(
        self, plan_date: date | str, vm_id: int, *, expected_status: int, by: str
    ) -> bool:
        async with self._lock:  # 临界区 = MySQL 那一行上的隐式行锁
            self.claims.append({"by": by, "expected_status": expected_status})
            if self.status != expected_status:
                return False
            self.status = 7
            return True

    async def release_order_claim(
        self, plan_date: date | str, vm_id: int, *, revert_status: int, by: str
    ) -> bool:
        async with self._lock:
            self.releases.append({"by": by, "revert_status": revert_status})
            if self.status != 7:
                return False
            self.status = revert_status
            return True


def _profile(inner_code: str, *, region_id: int | None, vm_id: int = 80) -> MachineProfile:
    return MachineProfile(
        vm_id=vm_id,
        inner_code=inner_code,
        addr="北京市海淀区",
        vm_status=1,
        node_id=100,
        node_name="五道口",
        region_id=region_id,
        region_name="华北",
    )


def _plan(
    *, vm_id: int = 80, inner_code: str = "A1", status: int = PLAN_STATUS_SUGGESTED
) -> RestockPlan:
    return RestockPlan(
        plan_date=PLAN_DATE,
        vm_id=vm_id,
        inner_code=inner_code,
        status=status,
        assignee_id=6,
        assignee_name="周晨",
        items=[
            RestockItem(
                sku_id=1,
                sku_name="可乐",
                channel_id=1,
                channel_code="1-1",
                current_quantity=0,
                max_capacity=10,
                suggested_quantity=6,
                after_restock_quantity=6,
                reason="日均 2.4 件",
            )
        ],
    )


class _EmptySession:
    async def __aenter__(self) -> _EmptySession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.fixture
def patched_reads(monkeypatch):
    """把只读工具换成内存数据（SQL 形状另有 test_read_tools.py 负责）。"""
    state = {"machines": [], "assignees": {}, "staff_calls": 0, "profile_calls": 0}

    async def fake_list_operating_machines(
        session: Any, *, region_id=None, limit=None
    ) -> list[Any]:
        return list(state["machines"])

    async def fake_list_machine_profiles(session: Any, *, inner_codes, limit=None) -> list[Any]:
        state["profile_calls"] += 1
        wanted = set(inner_codes)
        return [m for m in state["machines"] if m.inner_code in wanted]

    async def fake_list_region_assignees(session: Any, *, region_ids: Any) -> dict[int, Any]:
        state["staff_calls"] += 1
        return {rid: state["assignees"][rid] for rid in region_ids if rid in state["assignees"]}

    async def fake_list_channel_stock(session: Any, *, inner_codes: Any, limit=None) -> list[Any]:
        return []

    async def fake_daily(
        session: Any, *, inner_codes: Any, start: Any, end: Any, limit=None
    ) -> list[Any]:
        return []

    from app.tools import read_tools

    monkeypatch.setattr(read_tools, "list_operating_machines", fake_list_operating_machines)
    monkeypatch.setattr(read_tools, "list_machine_profiles", fake_list_machine_profiles)
    monkeypatch.setattr(read_tools, "list_region_assignees", fake_list_region_assignees)
    monkeypatch.setattr(read_tools, "list_channel_stock", fake_list_channel_stock)
    monkeypatch.setattr(read_tools, "aggregate_channel_daily_sales", fake_daily)
    return state


def _deps(store: FakeStore) -> graph_mod.SqlRestockDeps:
    return graph_mod.SqlRestockDeps(session_factory=_EmptySession, store=store)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# ① 接单人分配（跨区域 + 负载）
# --------------------------------------------------------------------------------------


async def test_resolve_assignee_uses_device_region_and_balances_within_the_run(patched_reads):
    """一轮分析里必须“边派边累加”，否则同区域设备会全部压给同一个人。

    这是最容易被忽略的一处：如果只按初始负载选（不同步递增），
    9 台设备看到的是同一份 `{6: 0, 7: 0}`，结果是 9 台全给 emp_id 最小的那个。
    """
    patched_reads["machines"] = [
        _profile("A1", region_id=1, vm_id=80),
        _profile("A2", region_id=1, vm_id=86),
        _profile("A3", region_id=2, vm_id=88),
    ]
    patched_reads["assignees"] = {
        1: [
            Assignee(emp_id=6, user_name="周晨", region_id=1),
            Assignee(emp_id=7, user_name="柯涵", region_id=1),
        ],
        2: [Assignee(emp_id=2, user_name="李四", region_id=2)],
    }
    store = FakeStore(loads={})
    deps = _deps(store)

    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)
    picks = []
    for vm_id, inner in ((80, "A1"), (86, "A2")):
        picks.append(await deps.resolve_assignee(_plan(vm_id=vm_id, inner_code=inner)))
    region2 = await deps.resolve_assignee(_plan(vm_id=88, inner_code="A3"))

    assert [p[0] for p in picks] == [6, 7], "同区域两台设备应分给不同的人（负载递增）"
    assert region2 == (2, "李四"), "不同区域各取各的人"
    assert patched_reads["staff_calls"] == 1, "员工名单一轮只查一次（禁止 N+1）"
    assert store.load_calls == [date.fromisoformat(PLAN_DATE)], "负载一轮只查一次"


async def test_resolve_assignee_returns_none_when_region_has_no_business_staff(patched_reads):
    """拒绝路径：该区域没有启用的运营人员 → (None, None)，绝不跨区域凑人。"""
    patched_reads["machines"] = [_profile("A1", region_id=4)]
    patched_reads["assignees"] = {}  # 区域 4 只有维修人员，list_region_assignees 过滤后为空
    store = FakeStore()

    deps = _deps(store)
    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)

    assert await deps.resolve_assignee(_plan()) == (None, None)
    assert store.claims == [], "没接单人就不会有建单动作"


async def test_resolve_assignee_returns_none_when_device_has_no_region(patched_reads):
    """设备没有区域档案时不能猜（宁可待指派，也不能猜一个区域去派人）。"""
    patched_reads["machines"] = [_profile("A1", region_id=None)]
    patched_reads["assignees"] = {1: [Assignee(emp_id=6, user_name="周晨", region_id=1)]}
    deps = _deps(FakeStore())

    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)

    assert await deps.resolve_assignee(_plan()) == (None, None)


async def test_resolve_assignee_prefers_plan_region_when_present(patched_reads):
    """计划自带区域时以它为准（人工重算/干预路径读回来的计划就带区域）。"""
    patched_reads["machines"] = [_profile("A1", region_id=1)]
    patched_reads["assignees"] = {
        1: [Assignee(emp_id=6, user_name="周晨", region_id=1)],
        2: [Assignee(emp_id=2, user_name="李四", region_id=2)],
    }
    deps = _deps(FakeStore())

    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)
    plan = _plan().model_copy(update={"region_id": 2})

    assert await deps.resolve_assignee(plan) == (2, "李四")


async def test_load_clears_per_run_caches(patched_reads):
    """跨轮缓存必须清掉：长驻进程里不能拿昨天的员工名单派今天的单。"""
    patched_reads["machines"] = [_profile("A1", region_id=1)]
    patched_reads["assignees"] = {1: [Assignee(emp_id=6, user_name="周晨", region_id=1)]}
    store = FakeStore()
    deps = _deps(store)

    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)
    assert await deps.resolve_assignee(_plan()) == (6, "周晨")

    # 第二轮：区域 1 的人被停用了，只剩区域 2 的人
    patched_reads["machines"] = [_profile("A1", region_id=2)]
    patched_reads["assignees"] = {2: [Assignee(emp_id=2, user_name="李四", region_id=2)]}
    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)

    assert await deps.resolve_assignee(_plan()) == (2, "李四"), "不得复用上一轮的员工/区域缓存"
    assert patched_reads["staff_calls"] == 2
    assert len(store.load_calls) == 2, "负载也要重新读（第二轮是新一轮的事实）"


async def test_save_plan_fills_region_and_node_from_machine_profile(patched_reads):
    """落库时补全区域/点位：`agent_restock_plan.region_id` 是接单人分配与按区域审计的依据。"""
    patched_reads["machines"] = [_profile("A1", region_id=3)]
    store = FakeStore()
    deps = _deps(store)

    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)
    await deps.save_plan(_plan())

    saved = store.upserted[0]
    assert saved.region_id == 3 and saved.node_id == 100


async def test_save_plan_keeps_existing_region_and_tolerates_unknown_device(patched_reads):
    patched_reads["machines"] = []
    store = FakeStore()
    deps = _deps(store)

    await deps.load(plan_date=date.fromisoformat(PLAN_DATE), limit=100)
    await deps.save_plan(_plan().model_copy(update={"region_id": 9}))

    assert store.upserted[0].region_id == 9, "已有区域不许被覆盖成 NULL"
    await deps.save_plan(_plan(inner_code="NOT-IN-CACHE"))
    assert store.upserted[1].region_id is None, "查不到档案时如实保持 NULL（不猜）"


# --------------------------------------------------------------------------------------
# ② 建单并发保护（CAS 认领）
# --------------------------------------------------------------------------------------


async def test_create_task_claims_before_calling_java(monkeypatch):
    """顺序契约：先认领，后回调（反了就等于没有保护）。"""
    order: list[str] = []
    store = FakeStore()

    async def fake_create(plan: Any, *, request_id: str) -> CreatedTask:
        order.append("callback")
        return CreatedTask(task_id=999, task_code="202609210001")

    original_claim = store.claim_for_order

    async def spy_claim(*args: Any, **kwargs: Any) -> bool:
        order.append("claim")
        return await original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim_for_order", spy_claim)
    monkeypatch.setattr(graph_mod, "create_restock_task", fake_create)

    result = await _deps(store).create_task(_plan(), request_id="rid-1")

    assert result.task_id == 999
    assert order == ["claim", "callback"]
    assert store.claims[0]["expected_status"] == PLAN_STATUS_SUGGESTED
    assert store.claims[0]["plan_date"] == PLAN_DATE
    assert store.releases == [], "成功路径不得释放占位"


async def test_create_task_does_not_call_java_when_claim_lost(monkeypatch):
    """并发失败方：认领抢不到时**绝不能**再回调 Java（那正是重复建单）。"""
    called: list[str] = []

    async def fake_create(plan: Any, *, request_id: str) -> CreatedTask:
        called.append(request_id)
        return CreatedTask(task_id=1, task_code="x")

    monkeypatch.setattr(graph_mod, "create_restock_task", fake_create)
    store = FakeStore(claim_result=False)

    with pytest.raises(OrderClaimConflictError, match="正在建单中"):
        await _deps(store).create_task(_plan(), request_id="rid-2")

    assert called == [], "认领失败不得触发建单回调"
    assert store.releases == [], "没抢到锁就不是自己释放"


async def test_create_task_releases_claim_on_callback_failure(monkeypatch):
    """失败补偿：释放占位回原状态，运营可以马上重试（否则计划永远卡在 7-建单中）。"""

    async def fake_create(plan: Any, *, request_id: str) -> CreatedTask:
        raise CallbackUnavailableError("Java 回调不可达")

    monkeypatch.setattr(graph_mod, "create_restock_task", fake_create)
    store = FakeStore()

    with pytest.raises(CallbackUnavailableError):
        await _deps(store).create_task(_plan(status=PLAN_STATUS_UNASSIGNED), request_id="rid-3")

    assert store.releases == [
        {
            "plan_date": PLAN_DATE,
            "vm_id": 80,
            "revert_status": PLAN_STATUS_UNASSIGNED,
            "by": "agent:rid-3",
        }
    ]


async def test_create_task_refuses_non_claimable_status(monkeypatch):
    """第二道卡口：调用方绕过状态机直接拿“已建单”的计划来建单 → 拒绝，且不发 SQL、不回调。"""
    called: list[str] = []

    async def fake_create(plan: Any, *, request_id: str) -> CreatedTask:
        called.append(request_id)
        return CreatedTask(task_id=1, task_code="x")

    monkeypatch.setattr(graph_mod, "create_restock_task", fake_create)
    store = FakeStore()

    with pytest.raises(OrderClaimConflictError, match="不可建单"):
        await _deps(store).create_task(_plan(status=PLAN_STATUS_ORDERED), request_id="rid-4")

    assert store.claims == [] and called == []


async def test_two_concurrent_creates_reach_java_exactly_once(monkeypatch):
    """验收项 ③（单进程版）：两个并发建单请求，只有一个能回调 Java。

    真库上的同一用例（跨连接、真 SQL 行锁）见 -m live 的
    `test_restock_assignee_live.py::test_live_concurrent_confirm_creates_only_one_task`
    与 `docs/scripts/verify-1-8-assignee-concurrency.py`。
    """
    calls: list[str] = []

    async def fake_create(plan: Any, *, request_id: str) -> CreatedTask:
        await asyncio.sleep(0.05)  # 模拟真实回调耗时，把并发窗口撑开
        calls.append(request_id)
        return CreatedTask(task_id=999, task_code="202609210001")

    monkeypatch.setattr(graph_mod, "create_restock_task", fake_create)
    store = FakeCasStore()
    deps = _deps(store)

    results = await asyncio.gather(
        deps.create_task(_plan(), request_id="rid-a"),
        deps.create_task(_plan(), request_id="rid-b"),
        return_exceptions=True,
    )

    assert calls == ["rid-a"], "只有第一个请求能建单"
    assert isinstance(results[0], CreatedTask)
    assert isinstance(results[1], OrderClaimConflictError)
    assert store.status == 7, "赢家建单成功后由调用方写 4（本测只到 deps 层，故停在占位）"
