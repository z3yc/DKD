"""补货计划的持久化（`agent_restock_plan`，Phase 1 / 任务 1-6）。

为什么计划要落库而不是只存在 checkpoint 里：checkpoint 是**会话**语义（一张图跑到哪一步），
`agent_restock_plan` 是**业务**语义（今天该给哪台设备补什么、谁确认的、建了哪张工单）。
两者生命周期不同：会话可以重开，计划要能被工作台按日期查询、被 3-6 复盘统计。

三条来自 DDL 注释（`docs/ddl/agent_tables.sql:127-132`）的硬约束，这里逐条落实：

1. **幂等 upsert**：唯一键 `(vm_id, plan_date)`；06:00 定时任务重跑不会产生第二份计划；
2. **已建单/已复盘的计划不得被重跑覆盖**：`ON DUPLICATE KEY UPDATE` 里 items/total 只在
   原 status ∈ (1,2) 时更新（用 MySQL 的 `IF(status IN (1,2), ...)`，未限定列名即“当前行旧值”）；
3. **不物理删除**：本模块只有 INSERT/UPDATE（AGENTS §7.4 软删），账号本身也没有 DELETE 权限。

第四条（排期 1-8）：**建单前必须先“认领”这一行**（`claim_for_order`）。
确认建单是「先读计划 → 回调 Java → 回写状态」的三步非幂等流程，两个并发请求会各建一张工单。
Agent 能把哪一步做成原子的？只有 `agent_restock_plan` 这一行的条件 UPDATE：

```
UPDATE agent_restock_plan SET status = 7-建单中
 WHERE vm_id=? AND plan_date=? AND del_flag='0' AND status = 期望状态   -- CAS
```

影响行数为 1 的请求才去回调 Java；为 0 的那个直接告知运营“正在建单中”。
为什么这一点必须落在 **DB** 而不是进程内锁：本项目的部署形态从 3-9 演练起就包含"Python 宕机重启"，
而进程内锁一重启就消失（且 1-9 的定时任务与 1-7 的人工确认是两条入口）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import text

from app.db import assert_table_allowed, get_sessionmaker
from app.graphs.restock_state import RestockItem, RestockPlan

logger = logging.getLogger("dkd.agent.graphs.restock_plan_store")

TABLE = "agent_restock_plan"
# 自动分析开关表（1-7）：单行、按 scope 唯一
PAUSE_TABLE = "agent_restock_pause"
PAUSE_SCOPE_GLOBAL = "global"

# 建单占位状态（与 restock_state.PLAN_STATUS_ORDERING 同值）。
# 为什么在这里再定义一个而不是 import：本模块是存储层，不希望反向依赖图/领域模块
# （`restock_state` 已经被本模块 import，再加一条反向 import 会形成环）。
# 一致性由 tests/test_restock_plan_store.py::test_ordering_status_matches_state_machine 钉住。
ORDERING_STATUS = 7
# 允许被认领的状态（能被“确认建单”放行的状态）：1-建议 / 2-已调整 / 6-待指派
CLAIMABLE_STATUSES = (1, 2, 6)


class OrderClaimConflictError(RuntimeError):
    """建单认领失败（1-8 乐观锁未抦到）：该计划正在建单中，或状态已被别人改变。

    为什么单独一个异常类型而不是复用 `CallbackError`：两者的上层反应完全不同——
    回调失败要 502（“Java 那边出问题了”），认领失败要 409（“别重复点，有人正在处理”）。
    合成一类就逼上层靠字符串猜（与 1-3 的三分类同一个理由）。
    """


_CLAIM_SQL = """
update agent_restock_plan
   set status = :ordering_status,
       update_by = :by,
       update_time = :now
 where vm_id = :vm_id
   and plan_date = :plan_date
   and del_flag = '0'
   and status = :expected_status
"""

_RELEASE_CLAIM_SQL = """
update agent_restock_plan
   set status = :revert_status,
       update_by = :by,
       update_time = :now
 where vm_id = :vm_id
   and plan_date = :plan_date
   and del_flag = '0'
   and status = :ordering_status
"""

# 接单人负载：当日已被占用的计划数（1-8 分配策略的输入，见 restock_assignee）
_ASSIGNEE_LOAD_SQL = """
select assignee_id, count(*) as open_count
  from agent_restock_plan
 where plan_date = :plan_date
   and del_flag = '0'
   and assignee_id is not null
 group by assignee_id
"""

_UPSERT_SQL = """
insert into agent_restock_plan
  (plan_date, vm_id, inner_code, region_id, node_id, items, sku_count, total_quantity,
   status, assignee_id, assignee_name, create_by, create_time, update_by, update_time, del_flag)
values
  (:plan_date, :vm_id, :inner_code, :region_id, :node_id, :items, :sku_count, :total_quantity,
   :status, :assignee_id, :assignee_name, :by, :now, :by, :now, '0')
on duplicate key update
  region_id = values(region_id),
  node_id = values(node_id),
  items = if(status in (1, 2), values(items), items),
  sku_count = if(status in (1, 2), values(sku_count), sku_count),
  total_quantity = if(status in (1, 2), values(total_quantity), total_quantity),
  update_by = values(update_by),
  update_time = values(update_time),
  -- 复活软删行：唯一键 (vm_id, plan_date) 包含已软删的行，
  -- 不在此复活则某天被软删的计划将永远无法重新生成（每天都会撞唯一键）
  del_flag = '0'
"""

_UPDATE_SQL = """
update agent_restock_plan
   set status = :status,
       items = :items,
       sku_count = :sku_count,
       total_quantity = :total_quantity,
       adjust_reason = :adjust_reason,
       adjusted_by = :adjusted_by,
       adjusted_time = :adjusted_time,
       assignee_id = :assignee_id,
       assignee_name = :assignee_name,
       task_id = :task_id,
       task_code = :task_code,
       update_by = :update_by,
       update_time = :now
 where vm_id = :vm_id
   and plan_date = :plan_date
   and del_flag = '0'
"""

_SELECT_SQL = """
select id, plan_date, vm_id, inner_code, region_id, node_id, items, sku_count, total_quantity,
       status, adjust_reason, adjusted_by, adjusted_time, assignee_id, assignee_name,
       task_id, task_code
  from agent_restock_plan
 where plan_date = :plan_date
   and del_flag = '0'
 order by status, vm_id
 limit :limit
"""


class PlanStore(Protocol):
    """计划存储契约（图依赖注入用；测试可替换为内存实现，不连 MySQL）。"""

    async def upsert_plan(self, plan: RestockPlan, *, by: str = "system") -> None: ...

    async def save_decision(
        self,
        plan: RestockPlan,
        *,
        by: str,
        adjusted_by: int | None = None,
        adjust_reason: str | None = None,
        adjusted_time: datetime | None = None,
        task_id: int | None = None,
        task_code: str | None = None,
    ) -> None: ...

    async def list_plans(self, plan_date: date, *, limit: int = 200) -> list[dict[str, Any]]: ...

    async def count_open_by_assignee(self, plan_date: date) -> dict[int, int]: ...

    async def claim_for_order(
        self, plan_date: date | str, vm_id: int, *, expected_status: int, by: str
    ) -> bool: ...

    async def release_order_claim(
        self, plan_date: date | str, vm_id: int, *, revert_status: int, by: str
    ) -> bool: ...


# items JSON 的键名映射：**与 DDL 注释逐字一致**（camelCase，前端工作台直接消费）
# 为什么不直接用 model_dump()：那得到 snake_case，与 `docs/ddl/agent_tables.sql:146` 的列注释不符，
# 前端要么到处做字段名转换、要么就得维护一份隐式契约。显式映射表反而是最不容易走样的做法。
ITEM_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("sku_id", "skuId"),
    ("sku_name", "skuName"),
    ("channel_id", "channelId"),
    ("channel_code", "channelCode"),
    ("current_quantity", "currentQuantity"),
    ("max_capacity", "maxCapacity"),
    ("suggested_quantity", "suggestedQuantity"),
    ("after_restock_quantity", "afterRestockQuantity"),
    ("estimated_days", "estimatedDays"),
    ("priority", "priority"),
    ("reason", "reason"),
)


def _items_payload(plan: RestockPlan) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in plan.items:
        raw = item.model_dump(mode="json")
        out.append({external: raw[internal] for internal, external in ITEM_FIELD_MAP})
    return out


def _items_json(plan: RestockPlan) -> str:
    return json.dumps(_items_payload(plan), ensure_ascii=False)


def items_from_db(raw: Any) -> list[dict[str, Any]]:
    """把库里的 items（camelCase）转回 `RestockItem` 的 snake_case 入参。

    MySQL JSON 列经 aiomysql 取出时可能是 str（旧驱动/不同配置）或已解析好的 list，
    两种都容忍；解析失败返回空列表而不是抛异常——列表页不该因为一行脏数据整页 500。
    """
    if raw is None:
        return []
    payload = raw
    if isinstance(raw, str | bytes):
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("items 字段不是合法 JSON，已跳过：types=%s", type(raw).__name__)
            return []
    if not isinstance(payload, list):
        return []
    reverse = {external: internal for internal, external in ITEM_FIELD_MAP}
    items: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                reverse.get(key, key): value
                for key, value in entry.items()
                if reverse.get(key, key) in RestockItem.model_fields
            }
        )
    return items


def plan_from_row(row: dict[str, Any]) -> RestockPlan:
    """把 `agent_restock_plan` 一行还原为 `RestockPlan`（1-7 干预 API / 3-6 复盘要用）。"""
    return RestockPlan(
        plan_date=str(row["plan_date"]),
        vm_id=int(row["vm_id"]),
        inner_code=str(row["inner_code"]),
        region_id=row.get("region_id"),
        node_id=row.get("node_id"),
        status=int(row.get("status") or 1),
        assignee_id=row.get("assignee_id"),
        assignee_name=row.get("assignee_name"),
        items=[RestockItem(**raw) for raw in items_from_db(row.get("items"))],
    )


class SqlPlanStore:
    """MySQL 实现（`agent_*` 自有表：可写，但无 DELETE/DDL，见 app/db.py）。"""

    def __init__(self, session_factory: Any | None = None) -> None:
        self._session_factory = session_factory or get_sessionmaker()

    async def upsert_plan(self, plan: RestockPlan, *, by: str = "system") -> None:
        assert_table_allowed(TABLE, writable=True)
        params = {
            "plan_date": plan.plan_date,
            "vm_id": plan.vm_id,
            "inner_code": plan.inner_code,
            "region_id": plan.region_id,
            "node_id": plan.node_id,
            "items": _items_json(plan),
            "sku_count": plan.sku_count,
            "total_quantity": plan.total_quantity,
            "status": plan.status,
            "assignee_id": plan.assignee_id,
            "assignee_name": plan.assignee_name,
            "by": by,
            "now": datetime.now(),
        }
        async with self._session_factory() as session:
            await session.execute(text(_UPSERT_SQL), params)
            await session.commit()
        logger.info(
            "计划落库（幂等 upsert）vm_id=%s plan_date=%s status=%s sku=%s total=%s",
            plan.vm_id,
            plan.plan_date,
            plan.status,
            plan.sku_count,
            plan.total_quantity,
        )

    async def save_decision(
        self,
        plan: RestockPlan,
        *,
        by: str,
        adjusted_by: int | None = None,
        adjust_reason: str | None = None,
        adjusted_time: datetime | None = None,
        task_id: int | None = None,
        task_code: str | None = None,
    ) -> None:
        """写回人工决策结果（状态/明细/原因/接单人/工单号）。

        **不判断状态迁移合法性**：合法性由 `restock_decisions.apply_decision` 负责，
        这里只负责如实落库（职责分离：状态机可单测，SQL 不重复业务规则）。

        人工干预元数据（原因/操作人/时间）为什么走参数而不是 RestockPlan 的字段：
        `RestockPlan` 是 0-13 冻结稿里的领域契约，DDL 里的 `adjust_reason/adjusted_by`
        是**审计列**（谁在什么时候因为什么动了这份计划）。把审计列塞进领域模型会让
        “计划内容”与“对计划的操作”混在一起，也让冻结契约被迫增字段。
        """
        assert_table_allowed(TABLE, writable=True)
        now = datetime.now()
        params = {
            "plan_date": plan.plan_date,
            "vm_id": plan.vm_id,
            "status": plan.status,
            "items": _items_json(plan),
            "sku_count": plan.sku_count,
            "total_quantity": plan.total_quantity,
            "adjust_reason": adjust_reason,
            "adjusted_by": adjusted_by,
            "adjusted_time": adjusted_time,
            "assignee_id": plan.assignee_id,
            "assignee_name": plan.assignee_name,
            "task_id": task_id,
            "task_code": task_code,
            "update_by": by,
            "now": now,
        }
        async with self._session_factory() as session:
            await session.execute(text(_UPDATE_SQL), params)
            await session.commit()
        logger.info(
            "计划状态回写 vm_id=%s status=%s task_id=%s by=%s",
            plan.vm_id,
            plan.status,
            task_id,
            by,
        )

    async def list_plans(self, plan_date: date, *, limit: int = 200) -> list[dict[str, Any]]:
        assert_table_allowed(TABLE)
        async with self._session_factory() as session:
            result = await session.execute(
                text(_SELECT_SQL), {"plan_date": plan_date, "limit": limit}
            )
            return [dict(row) for row in result.mappings().all()]

    async def count_open_by_assignee(self, plan_date: date) -> dict[int, int]:
        """当日每个接单人已被占用的计划数（分配策略的负载输入，1-8）。

        不筛 `status`：已建单（4）/已复盘（5）也算这个人今天干过的活——
        若只算“待处理”，那么一个上午干掉 10 单的人下午又会被优先派单。
        """
        assert_table_allowed(TABLE)
        async with self._session_factory() as session:
            result = await session.execute(text(_ASSIGNEE_LOAD_SQL), {"plan_date": plan_date})
            return {
                int(row["assignee_id"]): int(row["open_count"]) for row in result.mappings().all()
            }

    async def claim_for_order(
        self, plan_date: date | str, vm_id: int, *, expected_status: int, by: str
    ) -> bool:
        """抢占“建单权”（CAS，排期 1-8）。返回 True = 抢到，可以回调 Java。

        为什么 CAS 的 WHERE 里同时要 `expected_status`：
        调用方是「先读行、后决策」的，两次读之间可能有人把计划调了/跳了。
        只用 `status IN (1,2,6)` 会把这些变更一起放行，造成
        “按旧计划建的工单覆盖掉别人刚改好的数量”。

        `expected_status` 必须是可认领状态（1/2/6）——传 7 进来是调用方的程序错误，
        因为那等于“把别人的锁再抢一次”（正是并发重复建单的成因），故直接拒绝而不是静默返回 False。

        @raises ValueError expected_status 不在可认领集合内
        """
        if expected_status not in CLAIMABLE_STATUSES:
            raise ValueError(
                f"expected_status={expected_status} 不是可认领状态 {CLAIMABLE_STATUSES}："
                "「建单中」的计划不得被再次认领（那正是并发重复建单）"
            )
        assert_table_allowed(TABLE, writable=True)
        async with self._session_factory() as session:
            result = await session.execute(
                text(_CLAIM_SQL),
                {
                    "ordering_status": ORDERING_STATUS,
                    "expected_status": expected_status,
                    "vm_id": vm_id,
                    "plan_date": str(plan_date),
                    "by": by[:64],  # update_by 列 VARCHAR(64)：呼叫方名过长会直接报错
                    "now": datetime.now(),
                },
            )
            await session.commit()
            claimed = int(result.rowcount or 0) == 1
        logger.info(
            "建单认领 vm_id=%s plan_date=%s expected=%s claimed=%s by=%s",
            vm_id,
            plan_date,
            expected_status,
            claimed,
            by,
        )
        return claimed

    async def release_order_claim(
        self, plan_date: date | str, vm_id: int, *, revert_status: int, by: str
    ) -> bool:
        """释放建单占位（失败补偿）：7-建单中 → 原状态（1/2/6），供运营重试。

        为什么要“释放”而不是把状态改成别的：建单失败（Java 不可达 / 业务拒绝）时
        计划本身没有变，把标签改回原样才符合事实，运营也能直接再点一次确认
        （接口文档缺这句说明，已登记 FIX-15）。

        WHERE 里限定 `status = 7`：只有持有占位的请求能释放；
        否则“释放一个别人刚刚抢到的占位”会把别人的建单变成无锁状态。
        返回 False 说明自己已经不再持有占位（例如已被人工处理）——调用方只需记日志。
        """
        if revert_status not in CLAIMABLE_STATUSES:
            raise ValueError(
                f"revert_status={revert_status} 不是可恢复状态 {CLAIMABLE_STATUSES}："
                "只能回到认领前的建议/已调整/待指派状态"
            )
        assert_table_allowed(TABLE, writable=True)
        async with self._session_factory() as session:
            result = await session.execute(
                text(_RELEASE_CLAIM_SQL),
                {
                    "ordering_status": ORDERING_STATUS,
                    "revert_status": revert_status,
                    "vm_id": vm_id,
                    "plan_date": str(plan_date),
                    "by": by[:64],
                    "now": datetime.now(),
                },
            )
            await session.commit()
            released = int(result.rowcount or 0) == 1
        logger.info(
            "建单占位释放 vm_id=%s plan_date=%s revert=%s released=%s by=%s",
            vm_id,
            plan_date,
            revert_status,
            released,
            by,
        )
        return released


_PAUSE_SELECT_SQL = """
select paused, reason, paused_by, paused_time, resumed_by, resumed_time
  from agent_restock_pause
 where scope = :scope
   and del_flag = '0'
 limit 1
"""

_PAUSE_UPSERT_SQL = """
insert into agent_restock_pause
  (scope, paused, reason, paused_by, paused_time, resumed_by, resumed_time,
   create_by, create_time, update_by, update_time, del_flag)
values
  (:scope, :paused, :reason, :paused_by, :paused_time, :resumed_by, :resumed_time,
   :by, :now, :by, :now, '0')
on duplicate key update
  paused = values(paused),
  reason = values(reason),
  paused_by = coalesce(values(paused_by), paused_by),
  paused_time = coalesce(values(paused_time), paused_time),
  resumed_by = coalesce(values(resumed_by), resumed_by),
  resumed_time = coalesce(values(resumed_time), resumed_time),
  del_flag = '0',
  update_by = values(update_by),
  update_time = values(update_time)
"""


class PauseState(BaseModel):
    """自动分析开关状态（原型 V2 的「⏸ 暂停自动分析 / 恢复自动分析」）。"""

    paused: bool = False
    reason: str | None = None
    paused_by: str | None = None
    paused_time: datetime | None = None
    resumed_by: str | None = None
    resumed_time: datetime | None = None


class SqlPauseStore:
    """`agent_restock_pause` 单行表的读写（1-7）。

    为什么单行 + UPSERT 而不是插历史行：历史留痕在 `agent_decision_log`（那才是留痕表），
    这张表只回答一个问题「现在的开关是什么状态」。多插行会让“读当前状态”变成
    “按时间倒序取第一条”，每个调用方都要写对排序 —— 一个必然有人写错的地方。
    """

    def __init__(self, session_factory: Any | None = None) -> None:
        self._session_factory = session_factory or get_sessionmaker()

    async def get(self, *, scope: str = PAUSE_SCOPE_GLOBAL) -> PauseState:
        """读开关；**没有行 = 未暂停**（首次部署不该因为缺数据就把自动分析停掉）。"""
        assert_table_allowed(PAUSE_TABLE)
        async with self._session_factory() as session:
            result = await session.execute(text(_PAUSE_SELECT_SQL), {"scope": scope})
            row = result.mappings().first()
        if not row:
            return PauseState()
        data = dict(row)
        return PauseState(
            paused=bool(data.get("paused")),
            reason=data.get("reason"),
            paused_by=data.get("paused_by"),
            paused_time=data.get("paused_time"),
            resumed_by=data.get("resumed_by"),
            resumed_time=data.get("resumed_time"),
        )

    async def set_paused(
        self, *, paused: bool, by: str, reason: str, scope: str = PAUSE_SCOPE_GLOBAL
    ) -> PauseState:
        """暂停/恢复（幂等：重复暂停只更新原因与时间，不会插第二行）。

        用 `coalesce(values(x), x)` 保留另一侧的操作记录：恢复时不清掉“谁暂停的”，
        暂停时也不清掉“上次谁恢复的”——审计要能看到完整的开关历史（AGENTS §6.3）。
        """
        assert_table_allowed(PAUSE_TABLE, writable=True)
        now = datetime.now()
        params = {
            "scope": scope,
            "paused": 1 if paused else 0,
            "reason": reason,
            "by": by,
            "now": now,
            "paused_by": by if paused else None,
            "paused_time": now if paused else None,
            "resumed_by": None if paused else by,
            "resumed_time": None if paused else now,
        }
        async with self._session_factory() as session:
            await session.execute(text(_PAUSE_UPSERT_SQL), params)
            await session.commit()
        logger.info("自动分析开关已更新 paused=%s by=%s reason=%s", paused, by, reason)
        return await self.get(scope=scope)
