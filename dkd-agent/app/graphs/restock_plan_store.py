"""补货计划的持久化（`agent_restock_plan`，Phase 1 / 任务 1-6）。

为什么计划要落库而不是只存在 checkpoint 里：checkpoint 是**会话**语义（一张图跑到哪一步），
`agent_restock_plan` 是**业务**语义（今天该给哪台设备补什么、谁确认的、建了哪张工单）。
两者生命周期不同：会话可以重开，计划要能被工作台按日期查询、被 3-6 复盘统计。

三条来自 DDL 注释（`docs/ddl/agent_tables.sql:127-132`）的硬约束，这里逐条落实：

1. **幂等 upsert**：唯一键 `(vm_id, plan_date)`；06:00 定时任务重跑不会产生第二份计划；
2. **已建单/已复盘的计划不得被重跑覆盖**：`ON DUPLICATE KEY UPDATE` 里 items/total 只在
   原 status ∈ (1,2) 时更新（用 MySQL 的 `IF(status IN (1,2), ...)`，未限定列名即“当前行旧值”）；
3. **不物理删除**：本模块只有 INSERT/UPDATE（AGENTS §7.4 软删），账号本身也没有 DELETE 权限。
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
    if isinstance(raw, (str, bytes)):
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
