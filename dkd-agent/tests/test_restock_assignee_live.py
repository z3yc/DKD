"""接单人分配与建单并发的**真实库**验收（排期任务 1-8 验收项 ①/③）。

默认不执行（pyproject 的 addopts 排除了 `-m live`）。手动跑法：

    set -a; source ../.env; set +a
    uv run pytest -m live tests/test_restock_assignee_live.py -v -s

为什么要单独一层：单测只能证明 SQL 形状与策略逻辑，**证明不了**这三件事：
1. 真实 `tb_emp` 数据里 role_code/status/region_id 三道过滤到底筛出了谁（本库 17 行、4 个区域，
   其中区域 4 只有维修人员 → 必须落到“待指派”，这条只有真数据能验）；
2. 10 台跨区域设备是否真的一台都没分错区域（Java 侧会因为区域不匹配直接拒单）；
3. 并发下 `UPDATE ... WHERE status = 期望值` 的行锁是否真的挡住了第二个请求
   （这是唯一无法用假会话证明的一条：假会话没有 MySQL 的行锁）。

证据留档：`docs/scripts/verify-1-8-assignee-concurrency.py` 输出同一批验收的可读记录。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from app.db import dispose_engine, get_sessionmaker
from app.graphs import restock_graph as graph_mod
from app.graphs.restock_plan_store import OrderClaimConflictError, SqlPlanStore
from app.graphs.restock_state import RestockItem, RestockPlan
from app.tools import read_tools
from app.tools.task_tools import CreatedTask

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("DKD_AGENT_DB_PASSWORD"),
        reason="需注入 .env（DKD_AGENT_DB_PASSWORD）才能连真实库",
    ),
]

# 哨兵日期：与开发库里的真实计划（今天/昨天）不可能相撞，
# 这样 live 用例的写入不会覆盖任何真实计划行（upsert 是按 (vm_id, plan_date) 的唯一键）
SENTINEL_DATE = date(2099, 1, 1)


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test() -> AsyncIterator[None]:
    """每个用例后释放连接池（与 test_read_tools_live.py 同因：pytest-asyncio 每例换事件循环）。"""
    yield
    await dispose_engine()


async def _sentinel_status(vm_id: int) -> int:
    """读哨兵行的状态（只查未软删行）。"""
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "select status from agent_restock_plan "
                "where vm_id = :vm_id and plan_date = :plan_date and del_flag = '0'"
            ),
            {"vm_id": vm_id, "plan_date": SENTINEL_DATE},
        )
        return int(rows.all()[0][0])


async def _reset_sentinel(vm_id: int) -> None:
    """把哨兵行复位（**测试专用**）：上一条用例可能把它留在 7-建单中或软删状态。

    注意这不是“生产也想这么干”：生产里残留的 7 必须人工核对后再动（见
    docs/ddl/agent_restock_plan_status_ordering.sql 的处置说明），
    否则“回调已成功但没来得及回写 task_id”会被回退成待确认，导致重复建单。
    测试里我们知道回调是打桩的（肯定没建单），所以可以放心复位。
    """
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "update agent_restock_plan "
                "   set status = 1, del_flag = '0', task_id = NULL, task_code = NULL, "
                "       update_by = 'live-test' "
                " where vm_id = :vm_id and plan_date = :plan_date"
            ),
            {"vm_id": vm_id, "plan_date": SENTINEL_DATE},
        )
        await session.commit()


async def _cleanup_sentinel(vm_id: int) -> None:
    """收尾：软删哨兵行（AGENTS §7.4；账号无 DELETE 权限，只能置 del_flag）。"""
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "update agent_restock_plan set del_flag = '1', update_by = 'live-test' "
                "where vm_id = :vm_id and plan_date = :plan_date"
            ),
            {"vm_id": vm_id, "plan_date": SENTINEL_DATE},
        )
        await session.commit()


async def _running_machines(limit: int = 10) -> list[tuple[int, str, int | None]]:
    """取若干运行中的设备：(vm_id, inner_code, region_id)。"""
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "select id, inner_code, region_id from tb_vending_machine "
                "where vm_status = 1 order by id limit :limit"
            ),
            {"limit": limit},
        )
        return [(int(r[0]), str(r[1]), r[2]) for r in rows.all()]


async def test_live_region_assignees_match_the_java_filter():
    """区域候选人必须与 Java 侧 `EmpController.businessList` 的过滤口径逐字一致。"""
    async with get_sessionmaker()() as session:
        regions = await session.execute(
            text("select distinct region_id from tb_vending_machine where vm_status = 1")
        )
        region_ids = sorted(int(r[0]) for r in regions.all() if r[0] is not None)
        grouped = await read_tools.list_region_assignees(session, region_ids=region_ids)
        expected = await session.execute(
            text(
                "select region_id, id, user_name from tb_emp "
                "where role_code = '1002' and status = 1 order by region_id, id"
            )
        )
        expected_rows = [(int(r[0]), int(r[1]), str(r[2])) for r in expected.all()]

    actual = [
        (region_id, a.emp_id, a.user_name)
        for region_id, people in sorted(grouped.items())
        for a in people
    ]
    assert actual == expected_rows, "候选接单人必须 = role_code=1002 且启用，且区域/顺序一致"
    print(  # noqa: T201 —— live 验收证据输出（需 -s 查看）
        f"[live] 候选接单人 {len(actual)} 人 / 覆盖区域 {sorted(grouped)}"
    )


async def test_live_ten_devices_are_assigned_within_their_own_region():
    """验收项 ①：10 台设备（跨 4 个区域）逐台分配，且每人都在自己设备的区域内。"""
    machines = await _running_machines(limit=10)
    assert len(machines) >= 10, f"开发库应有 ≥10 台运行中设备，实际 {len(machines)}"

    async with get_sessionmaker()() as session:
        members = await session.execute(
            text("select id, region_id from tb_emp where role_code = '1002' and status = 1")
        )
        emp_region = {int(r[0]): r[1] for r in members.all()}

    store = SqlPlanStore()
    deps = graph_mod.SqlRestockDeps(store=store)
    await deps.load(plan_date=SENTINEL_DATE, limit=100)

    assignments: list[tuple[str, int | None, int | None, int | None]] = []
    for vm_id, inner_code, region_id in machines:
        plan = RestockPlan(
            plan_date=SENTINEL_DATE.isoformat(),
            vm_id=vm_id,
            inner_code=inner_code,
            items=[
                RestockItem(
                    sku_id=1,
                    channel_id=1,
                    channel_code="1-1",
                    current_quantity=0,
                    max_capacity=10,
                    suggested_quantity=5,
                    after_restock_quantity=5,
                )
            ],
        )
        emp_id, name = await deps.resolve_assignee(plan)
        assignments.append(
            (inner_code, region_id, emp_id, None if emp_id is None else emp_region[emp_id])
        )
        print(  # noqa: T201
            f"[live] {inner_code} 区域={region_id} → 接单人={emp_id}({name}) 员工区域="
            f"{None if emp_id is None else emp_region[emp_id]}"
        )

    for inner_code, region_id, emp_id, assigned_region in assignments:
        if emp_id is None:
            continue
        assert assigned_region == region_id, (
            f"{inner_code}（区域 {region_id}）被派给了区域 {assigned_region} 的人："
            "Java 侧会以「员工区域与设备区域不一致」直接拒单"
        )

    # 同一区域内不得出现“一台全给同一个人、另一台闲着”的极端倾斜
    per_region: dict[int | None, list[int | None]] = {}
    for _, region_id, emp_id, _ in assignments:
        if emp_id is not None:
            per_region.setdefault(region_id, []).append(emp_id)
    for region_id, picks in per_region.items():
        counts: dict[int | None, int] = {}
        for emp_id in picks:
            counts[emp_id] = counts.get(emp_id, 0) + 1
        assert max(counts.values()) - min(counts.values()) <= 1, (
            f"区域 {region_id} 分配不均：{counts}"
        )


async def test_live_region_without_business_staff_falls_back_to_unassigned():
    """区域里只有维修人员（role_code=1003）时必须落「待指派」，不能跳区派人。

    这里故意不要求设备处于运营状态：该用例验的是**区域匹配**，不是设备状态。
    但也不能随手把 region_id 一填就了事——同时断言该区域候选人确实是 0 人，
    否则“因为设备不在 `load()` 的设备集合里所以区域为 None”会假扮成本用例通过。
    """
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "select v.id, v.inner_code, v.region_id from tb_vending_machine v "
                "where v.region_id is not null "
                "  and not exists (select 1 from tb_emp e where e.region_id = v.region_id "
                "                  and e.role_code = '1002' and e.status = 1) "
                "limit 1"
            )
        )
        lonely = rows.all()
    if not lonely:
        pytest.skip("本库所有区域都至少有 1 名启用中的运营人员，无法构造“无人可派”场景")

    vm_id, inner_code, region_id = int(lonely[0][0]), str(lonely[0][1]), int(lonely[0][2])

    async with get_sessionmaker()() as session:
        no_staff = await read_tools.list_region_assignees(session, region_ids=[region_id])
    assert no_staff.get(region_id, []) == [], (
        f"前提不成立：区域 {region_id} 其实有运营人员，用例无法验证降级路径"
    )

    deps = graph_mod.SqlRestockDeps(store=SqlPlanStore())
    plan = RestockPlan(
        plan_date=SENTINEL_DATE.isoformat(),
        vm_id=vm_id,
        inner_code=inner_code,
        region_id=region_id,  # 干预/重算路径读回来的计划自带区域
        items=[
            RestockItem(
                sku_id=1,
                channel_id=1,
                channel_code="1-1",
                current_quantity=0,
                max_capacity=10,
                suggested_quantity=5,
                after_restock_quantity=5,
            )
        ],
    )
    assert await deps.resolve_assignee(plan) == (None, None), (
        f"区域 {region_id} 没有启用中的运营人员时不得跳区派人"
    )
    print(f"[live] {inner_code}（区域 {region_id}）无运营人员 → 待指派（6）")  # noqa: T201


async def test_live_concurrent_confirm_creates_only_one_task(monkeypatch):
    """验收项 ③（真库版）：两个并发请求抢同一个计划，只有一个能回调 Java。

    这一条**只能在真库上验**：假会话没有 MySQL 的行锁，
    `UPDATE ... WHERE status = 期望值` 的影响行数语义必须由真实事务给出。
    """
    machines = await _running_machines(limit=1)
    vm_id, inner_code, _ = machines[0]
    plan = RestockPlan(
        plan_date=SENTINEL_DATE.isoformat(),
        vm_id=vm_id,
        inner_code=inner_code,
        assignee_id=6,
        assignee_name="周晨",
        items=[
            RestockItem(
                sku_id=1,
                channel_id=1,
                channel_code="1-1",
                current_quantity=0,
                max_capacity=10,
                suggested_quantity=5,
                after_restock_quantity=5,
            )
        ],
    )

    store = SqlPlanStore()
    await _reset_sentinel(vm_id)
    await store.upsert_plan(plan, by="live-test")  # 种子：status=1-建议

    callback_calls: list[str] = []

    async def fake_create_restock_task(p, *, request_id: str) -> CreatedTask:
        await asyncio.sleep(0.05)  # 撑开并发窗口：没有 CAS 时两个请求都会进来
        callback_calls.append(request_id)
        return CreatedTask(task_id=999_999, task_code="LIVE-1")

    monkeypatch.setattr(graph_mod, "create_restock_task", fake_create_restock_task)
    deps = graph_mod.SqlRestockDeps(store=store)

    try:
        results = await asyncio.gather(
            deps.create_task(plan, request_id="live-a"),
            deps.create_task(plan, request_id="live-b"),
            return_exceptions=True,
        )
        ok = [r for r in results if isinstance(r, CreatedTask)]
        conflicts = [r for r in results if isinstance(r, OrderClaimConflictError)]
        print(  # noqa: T201
            f"[live] 并发建单：成功 {len(ok)} 次 / 冲突 {len(conflicts)} 次 / "
            f"回调 Java {len(callback_calls)} 次"
        )
        assert len(ok) == 1, f"只能有一个请求建单：{results}"
        assert len(conflicts) == 1
        assert callback_calls == ["live-a"], "回调 Java 必须只发生一次（重复建单是业务事实污染）"
        assert await _sentinel_status(vm_id) == 7, (
            "赢家持有建单占位（本测只到 deps 层，写 4-已建单由上层完成）"
        )
    finally:
        await _cleanup_sentinel(vm_id)


async def test_live_callback_failure_releases_the_claim(monkeypatch):
    """失败补偿在真库上也要成立：回调失败后计划必须能马上重试（不能卡在 7-建单中）。"""
    from app.tools.task_tools import CallbackUnavailableError

    machines = await _running_machines(limit=1)
    vm_id, inner_code, _ = machines[0]
    plan = RestockPlan(
        plan_date=SENTINEL_DATE.isoformat(),
        vm_id=vm_id,
        inner_code=inner_code,
        assignee_id=6,
        items=[
            RestockItem(
                sku_id=1,
                channel_id=1,
                channel_code="1-1",
                current_quantity=0,
                max_capacity=10,
                suggested_quantity=5,
                after_restock_quantity=5,
            )
        ],
    )
    store = SqlPlanStore()
    await _reset_sentinel(vm_id)
    await store.upsert_plan(plan, by="live-test")

    async def failing_create(p: Any, *, request_id: str) -> CreatedTask:
        raise CallbackUnavailableError("Java 不可达（live 测试故意制造）")

    monkeypatch.setattr(graph_mod, "create_restock_task", failing_create)

    try:
        with pytest.raises(CallbackUnavailableError):
            await graph_mod.SqlRestockDeps(store=store).create_task(plan, request_id="live-fail")

        status = await _sentinel_status(vm_id)
        assert status == 1, f"失败后应释放回 1-建议（实际 {status}），否则运营永远重试不了"
    finally:
        await _cleanup_sentinel(vm_id)
