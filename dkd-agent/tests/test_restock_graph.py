"""补货子图单测（排期任务 1-6）。

重点验三件事（都是验收标准里的硬指标）：
1. **中断-恢复能跨进程重启**：检查点落在 SQLite 文件，关掉再打开、重新编译图之后
   用 `Command(resume=...)` 仍能续跑，且**不会重跑 analyze**（不重复落库）；
2. **状态机是真的卡口**：非法决策进 `failures` 而不是把计划改坏，也不抛穿图；
3. **降级与幂等**：重复确认不重复建单、无接单人不建单、建单失败不改状态。

全部用假依赖（`FakeDeps`）与假模型：不连 MySQL、不调 LLM、不调 Java（AGENTS §8）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.checkpoint import CheckpointStore
from app.config import Settings
from app.graphs.restock_graph import build_restock_graph, new_restock_state
from app.graphs.restock_state import (
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_SKIPPED,
    PLAN_STATUS_SUGGESTED,
    PLAN_STATUS_UNASSIGNED,
)
from app.tools.read_tools import ChannelDailySales, ChannelStockItem
from app.tools.task_tools import CallbackUnavailableError, CreatedTask

PLAN_DATE = "2026-09-21"
THREAD = "restock-2026-09-21"
TODAY = date(2026, 9, 21)


class FakeModel:
    """假 LLM：默认什么都不校准（1-5 已有独立测试），只为让 calibrate 节点不碰真实 API。"""

    def __init__(self, content: str = '{"calibrations": []}') -> None:
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls += 1
        return AIMessage(
            content=self.content,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


class FakeDeps:
    """假依赖：只记录被调用的事实，便于断言“谁调了什么、顺序如何”。"""

    def __init__(
        self,
        *,
        stock: list[ChannelStockItem],
        daily: list[ChannelDailySales],
        assignee: tuple[int | None, str | None] = (None, None),
        task_error: Exception | None = None,
    ) -> None:
        self.stock = stock
        self.daily = daily
        self.assignee = assignee
        self.task_error = task_error
        self.load_calls = 0
        self.saved_plans: list[Any] = []
        self.saved_decisions: list[tuple[Any, Any, int | None]] = []
        self.created: list[Any] = []

    async def load(self, *, plan_date: date, limit: int) -> tuple[list[Any], list[Any]]:
        self.load_calls += 1
        return self.stock, self.daily

    async def resolve_assignee(self, plan: Any) -> tuple[int | None, str | None]:
        return self.assignee

    async def save_plan(self, plan: Any) -> None:
        self.saved_plans.append(plan)

    async def save_decision(
        self, plan: Any, *, decision: Any, task_id: int | None = None, task_code: str | None = None
    ) -> None:
        self.saved_decisions.append((plan, decision, task_id))

    async def create_task(self, plan: Any, *, request_id: str) -> CreatedTask:
        if self.task_error:
            raise self.task_error
        created = CreatedTask(task_id=999, task_code="202609210001")
        self.created.append(plan)
        return created


def _stock() -> list[ChannelStockItem]:
    return [
        ChannelStockItem(
            vm_id=80,
            inner_code="A1000001",
            channel_id=4732,
            channel_code="1-1",
            inventory_sku_id=1,
            channel_sku_id=1,
            current_stock=0,
            min_stock=None,
            channel_max_capacity=10,
            inventory_max_stock=10,
        )
    ]


def _daily() -> list[ChannelDailySales]:
    return [
        ChannelDailySales(
            inner_code="A1000001",
            channel_code="1-1",
            sale_date=TODAY.replace(day=21 - offset),
            qty=3,
        )
        for offset in range(1, 8)
    ]


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"DKD_AGENT_RESTOCK_CALIBRATION_ENABLED": "0"}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _last_saved(deps: FakeDeps, vm_id: int) -> Any:
    """取某设备**最后一次**落库的计划。

    同一 vm 在一次运行里会被保存两次（F-8 后的刻意设计）：
    `analyze` 存统计基线 → `calibrate` 存校准后的建议。
    真正生效（= 工作台展示、与实际建单一致）的是最后一次。
    """
    saved = [p for p in deps.saved_plans if p.vm_id == vm_id]
    assert saved, f"vm {vm_id} 没有任何落库记录"
    return saved[-1]


async def _interrupt_value(result: dict[str, Any]) -> dict[str, Any]:
    """取出 interrupt 载荷。

    LangGraph 把中断放在 `__interrupt__` 里，取 `.value` 才是节点传入的 payload。
    """
    interrupts = result.get("__interrupt__")
    assert interrupts, "图应在人工确认处中断"
    return dict(interrupts[0].value)


async def _open_graph(path: Any, deps: FakeDeps, *, model: Any | None = None) -> tuple[Any, Any]:
    """按给定检查点文件编译图，返回 (graph, store)。每次调用等价于“一次进程启动”。"""
    store = CheckpointStore(path)
    saver = await store.open()
    graph = build_restock_graph(
        deps=deps, checkpointer=saver, settings=_settings(), model=model or FakeModel()
    )
    return graph, store


# --------------------------------------------------------------------------------------
# 中断与恢复
# --------------------------------------------------------------------------------------


async def test_graph_pauses_at_confirmation_and_persists_suggested_plan(tmp_path):
    deps = FakeDeps(stock=_stock(), daily=_daily())
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    try:
        result = await graph.ainvoke(
            new_restock_state(plan_date=PLAN_DATE, request_id="rid-1"),
            {"configurable": {"thread_id": THREAD}},
        )
    finally:
        await store.close()

    payload = await _interrupt_value(result)
    assert payload["plan_date"] == PLAN_DATE
    assert len(payload["plans"]) == 1

    # 中断前必须已经落库：06:00 定时任务即使没人确认，工作台也要能看到待确认清单。
    # 落**两次**是刻意的（F-8）：analyze 存统计基线，calibrate 存校准后的建议——
    # 只存前者会让库里（工作台）与 state（实际建单）各说一个数。
    assert len(deps.saved_plans) == 2, "analyze（基线）+ calibrate（校准后）各落一次"
    plan = _last_saved(deps, 80)
    assert plan.status == PLAN_STATUS_SUGGESTED
    assert plan.items[0].suggested_quantity > 0
    # ★ F-8 不变量：**库里那条**必须与中断载荷（= 运营看到并确认的量、= 实际建单采用的量）逐字一致。
    # 本用例的假模型不做校准，所以两次落库同值；带系数的场景见
    # `test_calibrated_quantity_is_persisted_so_ui_matches_state`。
    assert plan.items[0].suggested_quantity == payload["plans"][0]["items"][0]["suggestedQuantity"]
    assert plan.total_quantity == payload["plans"][0]["totalQuantity"]
    # 无接单人（1-8 未落地）：不得伪造
    assert plan.assignee_id is None
    assert deps.created == [], "未确认前不得建单"


async def test_resume_with_skip_marks_plan_terminal_without_touching_java(tmp_path):
    deps = FakeDeps(stock=_stock(), daily=_daily(), assignee=(10, "孙权"))
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-2"), config)
        result = await graph.ainvoke(
            Command(
                resume=[{"vmId": 80, "action": "skip", "reason": "该点位下周撤机", "userId": 7}]
            ),
            config,
        )
    finally:
        await store.close()

    assert result["plans"][0]["status"] == PLAN_STATUS_SKIPPED
    assert deps.created == [], "跳过的计划绝不能建单"
    saved_plan, decision, _ = deps.saved_decisions[0]
    assert saved_plan.status == PLAN_STATUS_SKIPPED
    assert decision.reason == "该点位下周撤机"


async def test_resume_with_confirm_without_assignee_goes_unassigned(tmp_path):
    deps = FakeDeps(stock=_stock(), daily=_daily(), assignee=(None, None))
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-3"), config)
        result = await graph.ainvoke(Command(resume=[{"vmId": 80, "action": "confirm"}]), config)
    finally:
        await store.close()

    assert result["plans"][0]["status"] == PLAN_STATUS_UNASSIGNED
    assert deps.created == [], "无接单人不建单（Java 会因区域不一致拒绝）"
    assert result["created_tasks"] == []


async def test_resume_with_confirm_creates_task_and_marks_ordered(tmp_path):
    deps = FakeDeps(stock=_stock(), daily=_daily(), assignee=(10, "孙权"))
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-4"), config)
        result = await graph.ainvoke(
            Command(resume=[{"vmId": 80, "action": "confirm", "userId": 7}]), config
        )
    finally:
        await store.close()

    assert result["plans"][0]["status"] == PLAN_STATUS_ORDERED
    assert result["created_tasks"][0]["task_id"] == 999
    # 落库必须带工单号：Python 侧据此做“重复确认直接返回已建单”的幂等对账（排期 1-8）
    ordered_plan, _, task_id = deps.saved_decisions[-1]
    assert ordered_plan.status == PLAN_STATUS_ORDERED
    assert task_id == 999


async def test_create_task_failure_keeps_status_and_records_reason(tmp_path):
    deps = FakeDeps(
        stock=_stock(),
        daily=_daily(),
        assignee=(10, "孙权"),
        task_error=CallbackUnavailableError("Java 服务不可达，建单未完成"),
    )
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-5"), config)
        result = await graph.ainvoke(Command(resume=[{"vmId": 80, "action": "confirm"}]), config)
    finally:
        await store.close()

    # 失败不改状态：改回“建议”会让运营以为没点过而重复点（模块 docstring §4）
    assert result["plans"][0]["status"] == PLAN_STATUS_SUGGESTED
    failures = [f for f in result["failures"] if f.get("stage") == "create_task"]
    assert failures and "不可达" in failures[0]["reason"]
    assert result["created_tasks"] == []


# --------------------------------------------------------------------------------------
# P1-13：建单回调有界并发
# --------------------------------------------------------------------------------------


def _multi_vm_stock(count: int) -> list[ChannelStockItem]:
    """N 台设备各一条缺货货道（复用 _stock() 的单货道参数，只换设备维度）。"""
    return [
        ChannelStockItem(
            vm_id=80 + i,
            inner_code=f"A100000{i + 1}",
            channel_id=4732 + i,
            channel_code="1-1",
            inventory_sku_id=1,
            channel_sku_id=1,
            current_stock=0,
            min_stock=None,
            channel_max_capacity=10,
            inventory_max_stock=10,
        )
        for i in range(count)
    ]


def _multi_vm_daily(count: int) -> list[ChannelDailySales]:
    rows: list[ChannelDailySales] = []
    for i in range(count):
        rows.extend(
            ChannelDailySales(
                inner_code=f"A100000{i + 1}",
                channel_code="1-1",
                sale_date=TODAY.replace(day=21 - offset),
                qty=3,
            )
            for offset in range(1, 8)
        )
    return rows


class SlowTrackingDeps(FakeDeps):
    """P1-13 回归用：慢回调（可测并发度）+ 记录并发峰值，可指定部分设备失败。"""

    def __init__(
        self,
        *,
        delay_s: float,
        fail_vm_ids: set[int] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.delay_s = delay_s
        self.fail_vm_ids = fail_vm_ids or set()
        self._inflight = 0
        self.max_inflight = 0

    async def create_task(self, plan: Any, *, request_id: str) -> CreatedTask:
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            await asyncio.sleep(self.delay_s)
            if plan.vm_id in self.fail_vm_ids:
                raise CallbackUnavailableError(f"Java 服务不可达（vm {plan.vm_id}）")
            created = CreatedTask(task_id=900 + plan.vm_id, task_code=f"T-{plan.vm_id}")
            self.created.append(plan)
            return created
        finally:
            self._inflight -= 1


async def test_create_tasks_bounded_concurrent_and_input_ordered(tmp_path):
    """P1-13：N 台设备回调走 Semaphore 有界并发，结果按 plans 原序归并。"""
    count = 8
    deps = SlowTrackingDeps(
        delay_s=0.2,
        stock=_multi_vm_stock(count),
        daily=_multi_vm_daily(count),
        assignee=(10, "孙权"),
    )
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-c1"), config)
        started = time.monotonic()
        result = await graph.ainvoke(
            Command(resume=[{"vmId": vm, "action": "confirm"} for vm in range(80, 80 + count)]),
            config,
        )
        elapsed = time.monotonic() - started
    finally:
        await store.close()

    # 并发上界：默认 4，8 台 → 至少两批；峰值不得超过 4（回归串行则峰值恒为 1）
    assert deps.max_inflight <= 4
    assert deps.max_inflight >= 2, "回调应当并发执行（串行版 8 台最坏 8×10s）"
    # 耗时按批缩减：串行 8×0.2s=1.6s，4 并发约 0.4s；阈值取 1.2s 留防抖余量
    assert elapsed < 1.2, f"8 台×0.2s 回调用时 {elapsed:.2f}s，疑似退回串行"
    # 结果按输入序归并（gather 完成序无关，保证可复现）
    assert [c["vm_id"] for c in result["created_tasks"]] == list(range(80, 80 + count))
    assert all(p["status"] == PLAN_STATUS_ORDERED for p in result["plans"])


async def test_create_tasks_partial_failure_does_not_break_others(tmp_path):
    """P1-13：并发下部分设备回调失败，其余照常建单、失败进 failures 不抛穿图。"""
    count = 6
    fail_vm_ids = {81, 84}
    deps = SlowTrackingDeps(
        delay_s=0.05,
        fail_vm_ids=fail_vm_ids,
        stock=_multi_vm_stock(count),
        daily=_multi_vm_daily(count),
        assignee=(10, "孙权"),
    )
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-c2"), config)
        result = await graph.ainvoke(
            Command(resume=[{"vmId": vm, "action": "confirm"} for vm in range(80, 80 + count)]),
            config,
        )
    finally:
        await store.close()

    # 未失败的设备全部建单成功（含输入序）
    assert [c["vm_id"] for c in result["created_tasks"]] == [80, 82, 83, 85]
    # 失败如实上报且不改状态（模块 docstring §4）
    failure_vms = {f["vm_id"] for f in result["failures"] if f.get("stage") == "create_task"}
    assert failure_vms == fail_vm_ids
    status_by_vm = {p["vm_id"]: p["status"] for p in result["plans"]}
    assert status_by_vm[80] == PLAN_STATUS_ORDERED
    assert status_by_vm[81] == PLAN_STATUS_SUGGESTED
    assert status_by_vm[84] == PLAN_STATUS_SUGGESTED


async def test_illegal_resume_payload_is_reported_not_raised(tmp_path):
    deps = FakeDeps(stock=_stock(), daily=_daily(), assignee=(10, "孙权"))
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-6"), config)
        result = await graph.ainvoke(
            Command(resume=[{"vmId": 80, "action": "delete-everything"}]), config
        )
    finally:
        await store.close()

    assert result["plans"][0]["status"] == PLAN_STATUS_SUGGESTED, "非法载荷不得改坏计划"
    failures = [f for f in result["failures"] if f.get("stage") == "decision_parse"]
    assert failures and "不支持的操作" in failures[0]["reason"]
    assert deps.created == []


async def test_adjust_decision_updates_plan_and_records_reason(tmp_path):
    deps = FakeDeps(stock=_stock(), daily=_daily(), assignee=(10, "孙权"))
    graph, store = await _open_graph(tmp_path / "cp.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        await graph.ainvoke(new_restock_state(plan_date=PLAN_DATE, request_id="rid-7"), config)
        result = await graph.ainvoke(
            Command(
                resume=[
                    {
                        "vmId": 80,
                        "action": "adjust",
                        "reason": "周末点位有活动，先补 3 件试试",
                        "items": [{"channelCode": "1-1", "suggestedQuantity": 3}],
                        "userId": 7,
                    }
                ]
            ),
            config,
        )
    finally:
        await store.close()

    plan = result["plans"][0]
    assert plan["status"] == 2, "调整后状态应为 2-已调整"
    item = plan["items"][0]
    assert item["suggested_quantity"] == 3
    assert "先补 3 件试试" in item["reason"]
    assert deps.created == [], "本次只调整不确认，不能建单"


# --------------------------------------------------------------------------------------
# 验收核心：中断-恢复跨进程重启（依赖 0-10）
# --------------------------------------------------------------------------------------


async def test_interrupt_survives_process_restart(tmp_path):
    """关掉 store（=停进程）→ 重新打开 → 重新编译图 → 用 resume 续跑。

    并且断言：**analyze 没有被重跑**（`deps2.load_calls == 0`，`deps2.saved_plans == []`）——
    这正是“interrupt 之前不做非幂等副作用”的验证；若把落库放在中断节点之后，
    这里就会看到重复写计划。
    """
    path = tmp_path / "restart.db"
    deps1 = FakeDeps(stock=_stock(), daily=_daily(), assignee=(10, "孙权"))
    graph1, store1 = await _open_graph(path, deps1)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        paused = await graph1.ainvoke(
            new_restock_state(plan_date=PLAN_DATE, request_id="rid-restart"), config
        )
        assert await _interrupt_value(paused), "第一次运行应停在人工确认"
        assert len(deps1.saved_plans) == 2, "analyze（基线）+ calibrate（校准后）"
    finally:
        await store1.close()

    # ---- 进程重启分界线：新的 store、新的图、新的依赖实例 ----
    deps2 = FakeDeps(stock=[], daily=[], assignee=(10, "孙权"))
    graph2, store2 = await _open_graph(path, deps2)
    try:
        result = await graph2.ainvoke(
            Command(resume=[{"vmId": 80, "action": "confirm", "userId": 7}]), config
        )
    finally:
        await store2.close()

    assert deps2.load_calls == 0, "恢复不应重跑取数/基线（否则会重复落库）"
    assert deps2.saved_plans == []
    assert result["plans"][0]["status"] == PLAN_STATUS_ORDERED
    assert result["created_tasks"][0]["task_id"] == 999
    assert deps2.created, "恢复后应真的建单"


async def test_resume_with_empty_plans_does_not_hang(tmp_path):
    """没有需要补货的货道时不应挂一个空中断（运营会以为系统卡住）。"""
    deps = FakeDeps(
        stock=[
            ChannelStockItem(
                vm_id=80,
                inner_code="A1000001",
                channel_id=4732,
                channel_code="1-1",
                current_stock=10,
                channel_max_capacity=10,
                inventory_max_stock=10,
            )
        ],
        daily=[],
    )
    graph, store = await _open_graph(tmp_path / "empty.db", deps)
    try:
        result = await graph.ainvoke(
            new_restock_state(plan_date=PLAN_DATE, request_id="rid-8"),
            {"configurable": {"thread_id": "empty"}},
        )
    finally:
        await store.close()

    assert "__interrupt__" not in result
    assert result["plans"] == []
    assert result["summary"]["device_count"] == 0


async def test_repeat_invoke_same_thread_reupserts_without_error(tmp_path):
    """重入安全（排期 1-9）：同一日期重复触发等于重跑一次分析，不得报错、不得多出一份计划。

    为什么允许重跑而不是「已分析过就拒绝」：数据修好后需要能重跑，把重跑变成“必须人工改库”
    的动作只会制造事故。计划行由 `(vm_id, plan_date)` 唯一键 upsert 兜住，不会变成两份。
    为什么在**图**这一层证明：1-9 的 thread_id 约定（`restock-<plan_date>`）就是这里传进去的，
    执行器的其余部分是薄封装。
    """
    deps = FakeDeps(stock=_stock(), daily=_daily())
    graph, store = await _open_graph(tmp_path / "repeat.db", deps)
    config = {"configurable": {"thread_id": THREAD}}
    try:
        first = await graph.ainvoke(
            new_restock_state(plan_date=PLAN_DATE, request_id="rid-first"), config
        )
        second = await graph.ainvoke(
            new_restock_state(plan_date=PLAN_DATE, request_id="rid-second"), config
        )
    finally:
        await store.close()

    assert await _interrupt_value(first) == await _interrupt_value(second)
    # 每次运行都完整跑「取数 → 基线落库 → 校准 → 校准后落库」（幂等由存储层唯一键保证，不是靠跳过）
    assert len(deps.saved_plans) == 4, "两次运行 × （基线 + 校准后）"
    assert [plan.vm_id for plan in deps.saved_plans] == [80, 80, 80, 80]
    assert second.get("failures") == [], "重跑不得把第一次的结果记成失败"


async def test_calibrated_quantity_is_persisted_so_ui_matches_state(tmp_path):
    """F-8 回归：校准后的建议量必须**落库**，否则工作台与实际建单是两个数。

    实测形态（2026-10-03 真环境）：库中 7/8/5 = 20 件，而 state / 中断载荷 = 21 件——
    因为落库发生在 `analyze`、校准在它之后，且校准结果从不回写。
    运营在界面上看到 20 件并点确认，`create_tasks` 却按 21 件建单。

    本用例用**带系数**的假模型让两次落库真的不同，从而：
      ① 断言最后一次落库 = 校准后的量（只看 `deps.saved_plans[0]` 会假绿）；
      ② 断言库中那条与中断载荷逐字一致（这才是运营对得上的那个数）。
    """
    deps = FakeDeps(stock=_stock(), daily=_daily(), assignee=(10, "孙权"))
    # 假模型把货道 1-1 的系数放大到上限 1.5，保证校准后的量与基线**不同**
    model = FakeModel(
        content='{"calibrations": [{"channelCode": "1-1", "factor": 1.5, "reason": "节前客流"}]}'
    )
    graph, store = await _open_graph(tmp_path / "f8.db", deps, model=model)
    try:
        result = await graph.ainvoke(
            new_restock_state(plan_date=PLAN_DATE, request_id="rid-f8"),
            {"configurable": {"thread_id": THREAD}},
        )
    finally:
        await store.close()

    payload = await _interrupt_value(result)
    baseline = deps.saved_plans[0].items[0].suggested_quantity
    calibrated = _last_saved(deps, 80).items[0].suggested_quantity

    assert len(deps.saved_plans) == 2, "两次落库都要发生（基线 + 校准后）"
    assert calibrated != baseline, (
        "本用例必须让校准真的改变数量（否则「回写」与「不回写」结果相同，断言失去意义）"
    )
    # ★ 库里的量 == 中断载荷的量 == 未来建单采用的量
    assert calibrated == payload["plans"][0]["items"][0]["suggestedQuantity"]
    assert _last_saved(deps, 80).total_quantity == payload["plans"][0]["totalQuantity"]
    # 反向：基线值**不能**是最终留在库里的那个（这正是 F-8 的错法）
    assert baseline not in {p.items[0].suggested_quantity for p in [deps.saved_plans[-1]]}
