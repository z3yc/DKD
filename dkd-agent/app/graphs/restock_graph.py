"""补货子图（Phase 1 / 任务 1-6）：取数+基线 → LLM 校准 → **interrupt 人工确认** → 建单 → 留痕。

## 图结构（节点名即日志里的 `langgraph_node`；改名会让历史 checkpoint 的恢复路径失配，别随意改）

```
START → analyze → calibrate → await_confirmation ⏸ (interrupt)
                                   │  ← Command(resume=[{vmId, action, ...}])
                                   ▼
                             apply_decisions → create_tasks → END
```

## 四个必须写清楚的工程决定

**1. 中断前的副作用必须幂等 —— 由 `interrupt()` 的语义决定。**
LangGraph 恢复中断时会**从头重跑被中断的那个节点**。所以：
- 取数/算基线/落库都在 `await_confirmation` **之前**（`analyze` 节点），不会被重跑；
- `await_confirmation` 节点内**零副作用**（只读 state + interrupt），否则每次恢复都会重复写库；
- 落库本身也用 `(vm_id, plan_date)` 唯一键 upsert（`restock_plan_store`），重跑只留一份。

**2. 取数与基线合并在一个节点，而不是拆两个。**
拆开就必须把「逐日销量原始行」塞进 state 跨节点传递，而 `RestockState` 是 **0-13 冻结契约**
（新增字段要评审）。合并后这些中间量只是函数内的局部变量，state 里只留 `inventory_rows`
与 `plans` 两个**已声明**字段——既不污染冻结 schema，也少一次遍历。

**3. 中断恢复必须能跨进程重启（依赖 0-10）。**
检查点落在 SQLite 文件，进程重启后仍在；恢复路径**不依赖任何内存状态**：
resume 只给「决策」，计划明细与状态全部从 checkpoint 的 state 取回。
`tests/test_restock_graph.py::test_interrupt_survives_process_restart` 用
“关掉 store → 重新打开 → 重新编译图”复现了重启场景。

**4. 建单失败不回滚计划状态。**
回调失败分三类（见 1-3 的 `task_tools`）：不可达可重试、业务拒绝要人工处理、鉴权失败是运维问题。
任何一类都**不该**把计划改回“建议”——那会让运营以为没点过、重复点一次。失败原因写进
`failures`，由 1-7 的干预 API 提供“重试建单”。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.config import Settings, get_settings
from app.graphs.restock_baseline import compute_device_baseline
from app.graphs.restock_calibration import calibrate_state_node
from app.graphs.restock_decisions import (
    ACTION_CONFIRM,
    RESULT_ALREADY_ORDERED,
    RESULT_PENDING_ORDER,
    DecisionRejectedError,
    PlanDecision,
    apply_decision,
    parse_decisions,
    summarize_plan,
)
from app.graphs.restock_plan_store import OrderClaimConflictError
from app.graphs.restock_state import (
    PLAN_STATUS_ORDERED,
    PLAN_STATUS_SUGGESTED,
    RestockPlan,
    RestockState,
    empty_state,
)
from app.tools.read_tools import (
    ASSIGNEE_ROLE_CODE,
    Assignee,
    ChannelDailySales,
    ChannelStockItem,
    MachineProfile,
    default_sales_window,
)
from app.tools.task_tools import CallbackError, CreatedTask, create_restock_task

logger = logging.getLogger("dkd.agent.graphs.restock")


class RestockDeps(Protocol):
    """图的外部依赖（取数 / 落库 / 建单）。

    为什么用具名协议：这张图要读两个数据源、要写库、要调 Java，全部作为零散参数传递会让
    节点签名变成“第 7 个参数是什么来着”；协议也让测试能只替换其中一项（其余走真实逻辑）。
    """

    async def load(
        self, *, plan_date: date, limit: int, inner_codes: list[str] | None = None
    ) -> tuple[list[ChannelStockItem], list[ChannelDailySales]]:
        """取货道库存与逐日销量；`inner_codes` 非空时只取这些设备（人工重算时用）。"""
        ...

    async def resolve_assignee(self, plan: RestockPlan) -> tuple[int | None, str | None]:
        """选接单人（返回 `(emp_id, 姓名)`；无匹配返回 `(None, None)`）。

        生产实现见 `SqlRestockDeps.resolve_assignee`（1-8）：按设备区域匹配启用中的运营人员，
        取当日负载最低者。返回 None 时计划走 6-待指派，**绝不伪造接单人**
        （Java 侧会因区域不一致拒绝，比“待指派”更难排查）；测试替身也必须遵守这条契约。
        """
        ...

    async def save_plan(self, plan: RestockPlan) -> None: ...

    async def save_decision(
        self,
        plan: RestockPlan,
        *,
        decision: PlanDecision,
        task_id: int | None = None,
        task_code: str | None = None,
    ) -> None: ...

    async def create_task(self, plan: RestockPlan, *, request_id: str) -> CreatedTask:
        """回调 Java 建单（**必须幂等保护**，1-8）。

        实现方责任：回调前先在 `agent_restock_plan` 上 CAS 认领该计划
        （`restock_plan_store.claim_for_order`），抢不到就抛 `OrderClaimConflictError`；
        回调失败要释放认领（否则计划会永远卡在 7-建单中）。详见 `SqlRestockDeps.create_task`。
        """
        ...


class SqlRestockDeps:
    """生产实现：读走只读工具（1-1/1-2），写计划走 `agent_*` 自有表，建单走 Java 回调（1-3）。"""

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        store: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        if store is None:
            from app.graphs.restock_plan_store import SqlPlanStore

            store = SqlPlanStore(session_factory)
        self._store = store
        # 一轮分析内的缓存（每次 `load()` 重置）：
        #   _machines         inner_code → 设备档案（接单人分配要 `region_id`）
        #   _staff_by_region  region_id  → 候选接单人（一次批量取完）
        #   _loads            emp_id     → 当日已占用计划数（1-8 的“负载”）
        # 为什么要缓存：`analyze` 会对上百台设备逐个 `resolve_assignee`，
        # 逐台查区域/查人就是 N+1（AGENTS §10 列表接口循环内查库是重点检查项）。
        self._machines: dict[str, MachineProfile] = {}
        self._staff_by_region: dict[int, list[Assignee]] | None = None
        self._staff_regions: set[int] = set()
        self._loads: dict[int, int] | None = None
        self._load_date: date | None = None

    async def load(
        self, *, plan_date: date, limit: int, inner_codes: list[str] | None = None
    ) -> tuple[list[ChannelStockItem], list[ChannelDailySales]]:
        from app.db import get_sessionmaker
        from app.tools import read_tools

        # 新一轮分析：丢掉上一轮的设备/员工/负载缓存。
        # 不清的话，长驻进程（uvicorn）里会拿“上一次分析时的员工名单与负载”去派今天的单，
        # 这类错误特别隐蔽：日志一切正常，只是人分错了。
        self._machines = {}
        self._staff_by_region = None
        self._staff_regions = set()
        self._loads = None
        self._load_date = None

        factory = self._session_factory or get_sessionmaker()
        start, end = default_sales_window(days=max(self._settings.restock_weights), today=plan_date)
        async with factory() as session:
            if inner_codes:
                # 只取指定设备：1-7 的「按新参数重算」没必要把全部设备读一遍
                codes = list(inner_codes)
                profiles = await read_tools.list_machine_profiles(session, inner_codes=codes)
            else:
                machines = await read_tools.list_operating_machines(session, limit=limit)
                profiles = list(machines)
                codes = [m.inner_code for m in machines]
            # 区域映射必须在同一轮里取好：接单人分配（1-8）的输入是 `region_id`，
            # 而 `tb_inventory` 上没有区域列（只能从设备档案拿）
            self._machines = {profile.inner_code: profile for profile in profiles}
            stock = await read_tools.list_channel_stock(session, inner_codes=codes)
            daily = await read_tools.aggregate_channel_daily_sales(
                session, inner_codes=codes, start=start, end=end
            )
        return stock, daily

    async def resolve_assignee(self, plan: RestockPlan) -> tuple[int | None, str | None]:
        """按设备区域匹配启用中的运营人员，取当日负载最低者（排期 1-8 的分配策略）。

        返回 `(None, None)` 的三种情形都是**如实降级**，不是失败：
        ① 设备没有区域档案；② 该区域没有启用中的运营人员（role_code=1002）；③ 候选人全被停用。
        此时计划落 6-待指派，等运营人工派单。
        绝不挑一个跨区域的人凑数——Java 侧会以“员工区域与设备区域不一致”拒绝，
        运营看到的是“系统故障”，而“待指派”才是事实。

        负载取「当日已占用该人的计划数」，并在本轮内**即时递增**：
        否则同一轮的 9 台设备都会看到同一份“初始负载”，全压给同一个人（负载均衡变成摆设）。
        """
        from app.graphs.restock_assignee import pick_assignee

        region_id = plan.region_id
        if region_id is None:
            profile = self._machines.get(plan.inner_code)
            region_id = profile.region_id if profile else None
        if region_id is None:
            logger.warning("设备 %s 无区域档案，无法按区域派单 → 待指派", plan.inner_code)
            return None, None

        candidates = await self._candidates(int(region_id))
        loads = await self._assignee_loads(date.fromisoformat(plan.plan_date))
        picked = pick_assignee(candidates, loads=loads)
        if picked is None:
            logger.info(
                "区域 %s 无可用运营人员（role_code=%s、启用中），设备 %s → 待指派",
                region_id,
                ASSIGNEE_ROLE_CODE,
                plan.inner_code,
            )
            return None, None
        loads[picked.emp_id] = loads.get(picked.emp_id, 0) + 1
        logger.info(
            "接单人分配 vm_id=%s inner_code=%s region=%s → emp_id=%s(%s) 本轮负载=%s",
            plan.vm_id,
            plan.inner_code,
            region_id,
            picked.emp_id,
            picked.user_name,
            loads[picked.emp_id],
        )
        return picked.emp_id, picked.user_name

    async def _staff(self) -> dict[int, list[Assignee]]:
        """候选接单人（按区域分组）：**本轮设备涉及的区域一次批量取完**。

        为什么要缓存：`analyze` 会对上百台设备逐个 `resolve_assignee`，
        逐台查人就是 N+1（AGENTS §10 列表接口循环内查库是重点检查项）。
        """
        if self._staff_by_region is None:
            self._staff_by_region = {}
            await self._load_staff([p.region_id for p in self._machines.values()])
        return self._staff_by_region

    async def _candidates(self, region_id: int) -> list[Assignee]:
        """取某区域的候选接单人（不在已加载区域里则补查一次）。

        为什么需要补查：1-7/1-8 的干预路径会读回带 `region_id` 的计划，
        而该设备可能不在本轮的设备集合里（例如只重算一个点位）。
        只补查**没查过的区域**，且查过就记下来，仍是每区域最多一次。
        """
        directory = await self._staff()
        if region_id not in self._staff_regions:
            await self._load_staff([region_id])
        return directory.get(region_id, [])

    async def _load_staff(self, region_ids: Iterable[int | None]) -> None:
        """按区域补齐员工目录（只查尚未查过的区域，结果合并进缓存）。"""
        pending = sorted({int(r) for r in region_ids if r is not None} - self._staff_regions)
        if not pending:
            return
        from app.db import get_sessionmaker
        from app.tools import read_tools

        factory = self._session_factory or get_sessionmaker()
        async with factory() as session:
            found = await read_tools.list_region_assignees(session, region_ids=pending)
        directory = self._staff_by_region if self._staff_by_region is not None else {}
        for region_id in pending:
            # 区域里没有人也要记下“已查过”，否则每次都会重复查（空结果不缓存是经典漏勺）
            directory[region_id] = found.get(region_id, [])
            self._staff_regions.add(region_id)
        self._staff_by_region = directory

    async def _assignee_loads(self, plan_date: date) -> dict[int, int]:
        """当日各接单人的负载（一轮只查一次，之后在内存里累加）。"""
        if self._loads is None or self._load_date != plan_date:
            self._loads = dict(await self._store.count_open_by_assignee(plan_date))
            self._load_date = plan_date
        return self._loads

    async def save_plan(self, plan: RestockPlan) -> None:
        """落库计划（1-6 的幂等 upsert），顺带补全区域/点位。

        为什么在这里补而不是在节点里：`RestockPlan` 的区域只能从设备档案拿，
        而档案缓存属于 deps（`load()` 已取好）。不补的话 `agent_restock_plan.region_id` 永远是
        NULL——它是接单人分配的依据、也是 `idx_agent_restock_plan_region` 与 3-7 按区域审计的前提。
        """
        await self._store.upsert_plan(self._with_region(plan), by="system")

    def _with_region(self, plan: RestockPlan) -> RestockPlan:
        """把设备区域/点位补进计划（已有则不动，拿不到档案则如实保持 NULL）。"""
        if plan.region_id is not None and plan.node_id is not None:
            return plan
        profile = self._machines.get(plan.inner_code)
        if profile is None:
            return plan
        return plan.model_copy(
            update={
                "region_id": plan.region_id or profile.region_id,
                "node_id": plan.node_id or profile.node_id,
            }
        )

    async def save_decision(
        self,
        plan: RestockPlan,
        *,
        decision: PlanDecision,
        task_id: int | None = None,
        task_code: str | None = None,
    ) -> None:
        await self._store.save_decision(
            plan,
            by=str(decision.user_id or "agent"),
            adjusted_by=decision.user_id,
            adjust_reason=decision.reason,
            adjusted_time=datetime.now(),
            task_id=task_id,
            task_code=task_code,
        )

    async def create_task(self, plan: RestockPlan, *, request_id: str) -> CreatedTask:
        """先 CAS 认领计划，再回调 Java；回调失败则释放认领（排期 1-8）。

        为什么认领放在这里而不是节点里：图（1-6）与人工确认 API（1-7/1-8）**共用这一个写通道**
        （`RestockService._execute` 也是调 `deps.create_task`），并发保护必须落在两条路径的公共处；
        写进节点就会出现“定时任务有保护、人工确认没保护”这类半失灵。
        """
        from app.graphs.restock_plan_store import CLAIMABLE_STATUSES, OrderClaimConflictError

        by = f"agent:{request_id}"[:64]
        if plan.status not in CLAIMABLE_STATUSES:
            # 状态机已经在 `apply_decision` 卡过一道；这里是第二道（防调用方绕过状态机直接建单）
            raise OrderClaimConflictError(
                f"计划当前状态 {plan.status} 不可建单（只允许 {CLAIMABLE_STATUSES}），请刷新后重试"
            )
        claimed = await self._store.claim_for_order(
            plan.plan_date, plan.vm_id, expected_status=plan.status, by=by
        )
        if not claimed:
            raise OrderClaimConflictError(
                "该计划正在建单中或状态已被变更（可能由另一位运营同时操作），请刷新后重试"
            )
        try:
            return await create_restock_task(plan, request_id=request_id)
        except CallbackError:
            # 释放占位：建单没成功，计划应回到原状态供重试（FIX-15 的“再点一次确认”路径）。
            # 不释放会让该计划永远卡在 7-建单中，运营只能等人工改库——比建单失败本身更糟。
            await self._store.release_order_claim(
                plan.plan_date, plan.vm_id, revert_status=plan.status, by=by
            )
            raise


def build_restock_graph(
    *,
    deps: RestockDeps,
    checkpointer: Any,
    settings: Settings | None = None,
    model: Any | None = None,
) -> Any:
    """编译补货子图。

    @param deps 外部依赖（取数/落库/建单）
    @param checkpointer SQLite saver（依赖 0-10）；**必须传**，否则中断无法跨请求/重启恢复
    @param model LLM 客户端（测试注入假实现；None 时按配置真实构造）
    """
    settings = settings or get_settings()

    async def analyze(state: RestockState) -> dict[str, Any]:
        """取数 + 统计基线（1-4）+ 组装计划 + **落库**（幂等 upsert，状态=1-建议）。

        取数、对齐序列、算分位所需的中间量全部是局部变量（见模块 docstring §2）；
        落库必须在 interrupt 之前完成，这样 06:00 定时任务即使没人确认，工作台也有数据可看。
        """
        plan_date = date.fromisoformat(state["plan_date"])
        stock, daily = await deps.load(plan_date=plan_date, limit=settings.read_row_limit)
        by_vm: dict[str, list[ChannelStockItem]] = {}
        for item in stock:
            by_vm.setdefault(item.inner_code, []).append(item)

        plans: list[RestockPlan] = []
        for inner_code, items in sorted(by_vm.items()):
            suggestions = compute_device_baseline(
                stock_items=items,
                daily_rows=[row for row in daily if row.inner_code == inner_code],
                today=plan_date,
                settings=settings,
            )
            actionable = [s for s in suggestions if s.suggested_quantity > 0]
            if not actionable:
                logger.info("设备 %s 无需要补货的货道，跳过", inner_code)
                continue
            plan = RestockPlan(
                plan_date=plan_date.isoformat(),
                vm_id=items[0].vm_id,
                inner_code=inner_code,
                status=PLAN_STATUS_SUGGESTED,
                items=[s.to_restock_item() for s in actionable],
            )
            assignee_id, assignee_name = await deps.resolve_assignee(plan)
            plan = plan.model_copy(
                update={"assignee_id": assignee_id, "assignee_name": assignee_name}
            )
            await deps.save_plan(plan)  # 中断前的副作用：必须幂等（模块 docstring §1）
            plans.append(plan)

        logger.info(
            "补货分析完成 plan_date=%s 设备=%s 货道=%s 销量行=%s 计划=%s",
            plan_date,
            len(by_vm),
            len(stock),
            len(daily),
            len(plans),
        )
        return {
            "candidate_vm_ids": sorted({item.vm_id for item in stock}),
            "inventory_rows": [item.model_dump(mode="json") for item in stock],
            "sales_baseline": {"daily_row_count": len(daily)},
            "plans": [plan.model_dump(mode="json") for plan in plans],
            "summary": {
                "plan_date": plan_date.isoformat(),
                "device_count": len(plans),
                "total_quantity": sum(plan.total_quantity for plan in plans),
            },
        }

    async def await_confirmation(state: RestockState) -> dict[str, Any]:
        """人工确认节点：**只读 state + interrupt，零副作用**（恢复时本节点会重跑）。"""
        plans = [_as_plan(raw) for raw in state.get("plans", [])]
        if not plans:
            # 没有需要补货的货道：不能挂一个空中断让前端空等（运营会以为系统卡住）
            return {"decisions": {}, "adjustments": {}}
        payload = {
            "plan_date": state["plan_date"],
            "plans": [summarize_plan(plan) for plan in plans],
        }
        resumed = interrupt(payload)
        try:
            decisions = parse_decisions(resumed)
        except DecisionRejectedError as exc:
            # 恢复载荷非法（前端传错 action/结构）：不能抛穿编排层（AGENTS §6.3），
            # 记进 failures 并把计划留在原状态等下一次干预。
            logger.warning("恢复载荷非法 plan_date=%s reason=%s", state["plan_date"], exc)
            return {
                "decisions": {},
                "adjustments": {},
                "failures": [{"stage": "decision_parse", "reason": str(exc)}],
            }
        return {
            "decisions": {
                str(decision.vm_id): decision.model_dump(mode="json") for decision in decisions
            },
            "adjustments": {
                str(decision.vm_id): {
                    "reason": decision.reason,
                    "items": [item.model_dump() for item in decision.items],
                }
                for decision in decisions
                if decision.action != ACTION_CONFIRM
            },
        }

    async def apply_decisions(state: RestockState) -> dict[str, Any]:
        """状态机卡口 + 决策落库（1-6 的核心）。

        单条决策被拒**不影响其它设备**（批量确认时不该因为一台填错原因就全失败），
        但被拒的那条必须有中文原因并进 `failures`（AGENTS §8 拒绝路径）。
        """
        decisions = {
            int(vm_id): PlanDecision(**raw) for vm_id, raw in (state.get("decisions") or {}).items()
        }
        plans = [_as_plan(raw) for raw in state.get("plans", [])]
        updated: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        orderable: list[int] = []

        for plan in plans:
            decision = decisions.get(plan.vm_id)
            if decision is None:
                updated.append(plan.model_dump(mode="json"))
                continue
            try:
                new_plan, result, note = apply_decision(plan, decision)
            except DecisionRejectedError as exc:
                logger.warning(
                    "决策被拒 vm_id=%s action=%s reason=%s", plan.vm_id, decision.action, exc
                )
                failures.append({"vm_id": plan.vm_id, "stage": "decision", "reason": str(exc)})
                updated.append(plan.model_dump(mode="json"))
                continue

            if result != RESULT_ALREADY_ORDERED:
                await deps.save_decision(new_plan, decision=decision)
            updated.append(new_plan.model_dump(mode="json"))
            if result == RESULT_PENDING_ORDER:
                orderable.append(plan.vm_id)
            logger.info(
                "决策执行 vm_id=%s action=%s result=%s note=%s",
                plan.vm_id,
                decision.action,
                result,
                note,
            )

        summary = dict(state.get("summary") or {})
        summary["orderable_vm_ids"] = orderable
        return {"plans": updated, "failures": failures, "summary": summary}

    async def create_tasks(state: RestockState) -> dict[str, Any]:
        """建单：只对已放行的计划回调 Java（1-3），成功后把计划置为 4-已建单并回写工单号。

        幂等（1-8 验收项 ② 的基础版）：已建单的计划在 `apply_decisions` 就返回
        `already_ordered`，**不会走到这里**，因此重复确认不会重复建单。
        """
        decisions = {
            int(vm_id): PlanDecision(**raw) for vm_id, raw in (state.get("decisions") or {}).items()
        }
        orderable = set((state.get("summary") or {}).get("orderable_vm_ids") or [])
        plans = [_as_plan(raw) for raw in state.get("plans", [])]
        created: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []

        for plan in plans:
            if plan.vm_id not in orderable:
                updated.append(plan.model_dump(mode="json"))
                continue
            decision = decisions.get(plan.vm_id) or PlanDecision(
                vm_id=plan.vm_id, action=ACTION_CONFIRM
            )
            try:
                result = await deps.create_task(plan, request_id=state.get("request_id", ""))
            except (CallbackError, OrderClaimConflictError) as exc:
                # 失败不改状态（模块 docstring §4）：原因交给 1-7 的“重试建单”。
                # 并发认领失败（1-8）也走这里：**不抛穿图**（AGENTS §6.3），而是作为
                # 该设备的失败原因如实上报（运营看到“正在建单中”而不是整个图崩掉）。
                logger.warning("建单未完成 vm_id=%s err=%s", plan.vm_id, exc)
                failures.append({"vm_id": plan.vm_id, "stage": "create_task", "reason": str(exc)})
                updated.append(plan.model_dump(mode="json"))
                continue

            ordered = plan.model_copy(update={"status": PLAN_STATUS_ORDERED})
            await deps.save_decision(
                ordered, decision=decision, task_id=result.task_id, task_code=result.task_code
            )
            created.append(
                {
                    "vm_id": plan.vm_id,
                    "task_id": result.task_id,
                    "task_code": result.task_code,
                    "quantity": plan.total_quantity,
                }
            )
            updated.append(ordered.model_dump(mode="json"))
            logger.info(
                "建单成功 vm_id=%s task_id=%s task_code=%s",
                plan.vm_id,
                result.task_id,
                result.task_code,
            )

        return {"plans": updated, "created_tasks": created, "failures": failures}

    graph = StateGraph(RestockState)
    graph.add_node("analyze", analyze)
    graph.add_node("calibrate", _wrap_calibration(model))
    graph.add_node("await_confirmation", await_confirmation)
    graph.add_node("apply_decisions", apply_decisions)
    graph.add_node("create_tasks", create_tasks)

    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "calibrate")
    graph.add_edge("calibrate", "await_confirmation")
    graph.add_edge("await_confirmation", "apply_decisions")
    graph.add_edge("apply_decisions", "create_tasks")
    graph.add_edge("create_tasks", END)
    return graph.compile(checkpointer=checkpointer)


def _wrap_calibration(model: Any | None) -> Callable[[RestockState], Awaitable[dict[str, Any]]]:
    """把 1-5 的校准节点包成图节点（注入假模型，测试不打真实 LLM）。"""

    async def calibrate(state: RestockState) -> dict[str, Any]:
        return await calibrate_state_node(state, model=model)

    return calibrate


def _as_plan(raw: dict[str, Any]) -> RestockPlan:
    return RestockPlan(**raw)


def new_restock_state(*, plan_date: str, request_id: str, trigger_type: int = 2) -> RestockState:
    """构造初始 state（06:00 定时任务 trigger_type=2，用户会话 1，人工干预 3）。"""
    return empty_state(plan_date=plan_date, trigger_type=trigger_type, request_id=request_id)


def plan_summaries(state: RestockState) -> Sequence[dict[str, Any]]:
    """从 state 里取计划摘要（API 层回显用）。"""
    return [summarize_plan(_as_plan(raw)) for raw in state.get("plans", [])]
