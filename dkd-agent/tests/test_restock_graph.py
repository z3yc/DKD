"""补货子图单测（排期任务 1-6）。

重点验三件事（都是验收标准里的硬指标）：
1. **中断-恢复能跨进程重启**：检查点落在 SQLite 文件，关掉再打开、重新编译图之后
   用 `Command(resume=...)` 仍能续跑，且**不会重跑 analyze**（不重复落库）；
2. **状态机是真的卡口**：非法决策进 `failures` 而不是把计划改坏，也不抛穿图；
3. **降级与幂等**：重复确认不重复建单、无接单人不建单、建单失败不改状态。

全部用假依赖（`FakeDeps`）与假模型：不连 MySQL、不调 LLM、不调 Java（AGENTS §8）。
"""

from __future__ import annotations

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

    # 中断前必须已经落库：06:00 定时任务即使没人确认，工作台也要能看到待确认清单
    assert len(deps.saved_plans) == 1
    plan = deps.saved_plans[0]
    assert plan.status == PLAN_STATUS_SUGGESTED
    assert plan.items[0].suggested_quantity > 0
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
        assert len(deps1.saved_plans) == 1
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
