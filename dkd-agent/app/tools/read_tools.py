"""只读业务数据工具层（Phase 1 / 任务 1-1 与 1-2）。

定位：Agent 的“感知层”。只做两件事——**把业务事实读出来、把口径写清楚**；
一切写操作都不在这里（写必须回调 Java REST，AGENTS §1 约束 1）。

四条硬约束（每条都有代价换来的理由）：
1. **取表必须先过 `assert_table_allowed`**：越权查询在 SQL 到 MySQL 之前就被拦，
   而不是等 MySQL 报 1142（错误信息更清晰、也不给注入留机会）；
2. **时间过滤只用范围比较** `create_time >= :start AND create_time < :end`：
   禁用 `date_format(create_time,...)` 这类“函数包裹索引列”的写法——本仓库 `OrderMapper.xml:44`
   就是这么写的，在 30 天聚合这种量级下会退化成全表扫描（排期 1-2 明确点名）；
3. **按 vm 分批**：单条 SQL 的 `IN` 列表不能无限长（解析/计划开销随元素数增长），
   分批还能让超时熔断的爆炸半径限制在一批内；
4. **限时 + 限行**：单主库无只读从库（`application-druid.yml` `slave.enabled=false`），
   分析查询必须能用 `read_query_timeout_s` 熔断、`read_row_limit` 兜底（禁止无 LIMIT 的全表扫描）。

口径证据（事实性结论必须带 file:line，AGENTS §9.3）：
- 销量口径 = `tb_order.status = 2`（出货成功）。依据：`ReportServiceImpl.java:28` 定义
  `ORDER_STATUS_SUCCESS = 2` 并用于营收/订单数/榜单统计；`VmSystemConstant.java:93` 定义
  `ORDER_STATUS_VENDOUT_SUCCESS = 2`。
- 货道容量以 `tb_channel.max_capacity` 为准、库存以 `tb_inventory.current_stock` 为准。
  依据：`tb_inventory.max_stock` 与 `tb_channel.max_capacity` 在本库**实测 3/3 行不一致**（见
  `docs/ddl/business_tables_survey.md`），必须显式选一边并把不一致暴露出来，而不是二选一后装作没看见。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from app.config import get_settings
from app.db import assert_table_allowed

logger = logging.getLogger("dkd.agent.tools.read")

# 运行中的设备（vm_status=1，见 VmSystemConstant / DkdContants.VM_STATUS_RUNNING）
VM_STATUS_RUNNING = 1
# 补货工单类型（DkdContants.TASK_TYPE_SUPPLY = 2）
TASK_TYPE_SUPPLY = 2
# 接单人必须是「运营人员」角色（DkdContants.ROLE_CODE_BUSINESS = "1002"）。
# 依据：前端补货工单页 `dkd-vue/src/views/manage/task/business.vue` 调的正是
# `businessList` → `EmpController.businessList`
# （region_id + ROLE_CODE_BUSINESS + EMP_STATUS_NORMAL），与 Java 侧建单校验同源；
# 取 1003（运维/维修）会被 dkd-app 的作业队列发错人。
ASSIGNEE_ROLE_CODE = "1002"
# 员工启用状态（DkdContants.EMP_STATUS_NORMAL = 1）
EMP_STATUS_NORMAL = 1
# 工单状态：1-待接单（创建） 2-进行中（DkdContants.TASK_STATUS_CREATE/PROGRESS）
TASK_STATUS_INFLIGHT = (1, 2)


class ToolError(RuntimeError):
    """工具层通用异常（可读原因，供编排层兜底为结果消息，不抛穿 LangGraph 循环）。"""


class QueryTimeoutError(ToolError):
    """单条查询超过 `read_query_timeout_s` —— 熔断，避免拖垮与业务共用的单主库。"""


class MachineProfile(BaseModel):
    """设备 + 点位 + 区域档案（1-1 的地基）。"""

    vm_id: int
    inner_code: str
    addr: str | None = None
    vm_status: int
    node_id: int | None = None
    node_name: str | None = None
    region_id: int | None = None
    region_name: str | None = None


class Assignee(BaseModel):
    """可选接单人（`tb_emp` 里的同区域运营人员，1-8 的分配输入）。

    为什么不叫 Employee：这只是“可派人”的瘦投影（emp_id/姓名/区域），
    而 `tb_emp` 还有手机号等敏感字段——读工具**不把用不上的个人信息带进内存**，
    日志与留痕也就不会顺手把它们写出去（AGENTS §7.8 脱敏）。
    """

    emp_id: int
    user_name: str
    region_id: int


class ChannelStockItem(BaseModel):
    """货道 + 库存（1-1 的核心输出）。

    为什么同时给 `inventory_sku_id` 与 `channel_sku_id`：两表在本库实测不一致，
    读工具**如实返回两侧取值**，由上游（基线引擎/LLM 校准）决定以谁为准并留痕，
    而不是在工具层悄悄做一次“归一化”。
    """

    vm_id: int
    inner_code: str
    channel_id: int
    channel_code: str
    inventory_sku_id: int | None = None
    channel_sku_id: int | None = None
    current_stock: int
    min_stock: int | None = None
    is_alert: bool = False
    channel_max_capacity: int | None = None
    inventory_max_stock: int | None = None
    last_supply_time: datetime | None = None
    last_sell_time: datetime | None = None

    @property
    def capacity(self) -> int:
        """可用于 clamp 的货道容量：优先货道档案（见模块 docstring 的口径证据）。"""
        return int(self.channel_max_capacity or self.inventory_max_stock or 0)

    @property
    def data_consistent(self) -> bool:
        """两侧档案是否一致（不一致要为数据质量报警，而不是静默选边）。"""
        sku_ok = (
            self.inventory_sku_id is None
            or self.channel_sku_id is None
            or (self.inventory_sku_id == self.channel_sku_id)
        )
        cap_ok = (
            self.channel_max_capacity is None
            or self.inventory_max_stock is None
            or self.channel_max_capacity == self.inventory_max_stock
        )
        return sku_ok and cap_ok


class ChannelSales(BaseModel):
    """单货道的窗口内销量（1-2 输出，供基线引擎算日均去使用）。"""

    inner_code: str
    channel_code: str
    sku_id: int | None = None
    order_count: int = 0
    qty: int = 0
    amount: int = 0
    last_order_at: datetime | None = None


class ChannelDailySales(BaseModel):
    """单货道的**按日**销量（1-4 输入）。

    为什么 1-2 的窗口聚合不够：分位销量必须先有“每天卖了多少”的时间序列
    （窗口总量只能算均值，算不出分位，也看不出波动）。
    窗口内**没有订单的日期不会出现在结果里**，补齐成 0 由基线引擎负责
    （工具层只管把事实读出来，不做业务口径的补全）。
    """

    inner_code: str
    channel_code: str
    sale_date: date
    order_count: int = 0
    qty: int = 0


class InflightTask(BaseModel):
    """在途工单（补货去重/在途数量必须看它，否则会重复建单）。"""

    task_id: int
    task_code: str | None = None
    inner_code: str
    task_status: int
    product_type_id: int | None = None
    user_id: int | None = None
    create_time: datetime | None = None
    channels: list[str] = Field(default_factory=list)


def _normalize_inner_codes(inner_codes: Sequence[str] | None) -> list[str]:
    """去重 + 去空白，保持顺序（上游传错时不静默当空处理）。"""
    if not inner_codes:
        return []
    seen: dict[str, None] = {}
    for code in inner_codes:
        text_code = (code or "").strip()
        if text_code:
            seen.setdefault(text_code, None)
    return list(seen.keys())


def _batched(items: Sequence[str], size: int) -> list[list[str]]:
    step = max(1, size)
    return [list(items[i : i + step]) for i in range(0, len(items), step)]


async def _fetch_all(
    session: Any,
    sql: str,
    params: dict[str, Any],
    *,
    tables: Sequence[str],
    expanding: Sequence[str] = (),
) -> list[Any]:
    """执行只读查询：白名单校验 + 超时熔断 + 行数上限。

    `session` 只要求实现 `execute(text, params)`（SQLAlchemy AsyncSession 的 `execute` 签名），
    便于单测用假会话断言 SQL 形状而不连库。
    """
    for table in tables:
        assert_table_allowed(table)
    settings = get_settings()
    statement = text(sql)
    if expanding:
        # text() 不会自动展开 IN 参数；expanding 可安全地产生多个绑定参数。
        statement = statement.bindparams(*(bindparam(name, expanding=True) for name in expanding))
    try:
        result = await asyncio.wait_for(
            session.execute(statement, params), timeout=settings.read_query_timeout_s
        )
    except TimeoutError as exc:  # asyncio.TimeoutError 在 3.11 即内建 TimeoutError
        logger.warning(
            "只读查询超时熔断（%.1fs）tables=%s", settings.read_query_timeout_s, ",".join(tables)
        )
        raise QueryTimeoutError(
            f"查询超时（>{settings.read_query_timeout_s}s）已熔断，请缩小时间窗口或减少设备数"
        ) from exc
    rows = result.mappings().all()
    if len(rows) > settings.read_row_limit:
        logger.warning("只读查询命中行数 %s 超过上限 %s", len(rows), settings.read_row_limit)
        raise ToolError(f"查询结果超过行数上限 {settings.read_row_limit}，请缩小窗口或分批调用")
    return list(rows)


async def get_machine_profile(session: Any, inner_code: str) -> MachineProfile | None:
    """按设备编号取设备档案（含点位与区域名）。"""
    code = (inner_code or "").strip()
    if not code:
        raise ToolError("innerCode 不能为空")
    rows = await _fetch_all(
        session,
        """
        select v.id as vm_id, v.inner_code, v.addr, v.vm_status,
               v.node_id, n.node_name, v.region_id, r.region_name
        from tb_vending_machine v
        left join tb_node n on n.id = v.node_id
        left join tb_region r on r.id = v.region_id
        where v.inner_code = :inner_code
        limit 1
        """,
        {"inner_code": code},
        tables=("tb_vending_machine", "tb_node", "tb_region"),
    )
    if not rows:
        return None
    return MachineProfile(**dict(rows[0]))


async def list_operating_machines(
    session: Any, *, region_id: int | None = None, limit: int | None = None
) -> list[MachineProfile]:
    """列出运行中的设备（1-2/1-4 的输入集合）；`region_id` 可选，用于按区域分批分析。"""
    settings = get_settings()
    params: dict[str, Any] = {
        "vm_status": VM_STATUS_RUNNING,
        "limit": limit or settings.read_row_limit,
    }
    region_clause = ""
    if region_id is not None:
        region_clause = "and v.region_id = :region_id"
        params["region_id"] = region_id
    rows = await _fetch_all(
        session,
        f"""
        select v.id as vm_id, v.inner_code, v.addr, v.vm_status,
               v.node_id, n.node_name, v.region_id, r.region_name
        from tb_vending_machine v
        left join tb_node n on n.id = v.node_id
        left join tb_region r on r.id = v.region_id
        where v.vm_status = :vm_status {region_clause}
        order by v.id
        limit :limit
        """,
        params,
        tables=("tb_vending_machine", "tb_node", "tb_region"),
    )
    return [MachineProfile(**dict(row)) for row in rows]


async def list_machine_profiles(
    session: Any, *, inner_codes: Sequence[str], limit: int | None = None
) -> list[MachineProfile]:
    """按设备编号批量取设备档案（1-8 接单人分配需要 `region_id`）。

    与 `list_operating_machines` 的区别：这里**不筛 `vm_status`**——
    区域映射是档案事实，不该因为设备此刻不在运营状态就查不到区域
    （否则会把“该设备不能建单”误报成“没人可派”）。
    """
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    settings = get_settings()
    profiles: list[MachineProfile] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select v.id as vm_id, v.inner_code, v.addr, v.vm_status,
                   v.node_id, n.node_name, v.region_id, r.region_name
            from tb_vending_machine v
            left join tb_node n on n.id = v.node_id
            left join tb_region r on r.id = v.region_id
            where v.inner_code in :inner_codes
            order by v.id
            limit :limit
            """,
            {"inner_codes": tuple(batch), "limit": limit or settings.read_row_limit},
            tables=("tb_vending_machine", "tb_node", "tb_region"),
            expanding=("inner_codes",),
        )
        profiles.extend(MachineProfile(**dict(row)) for row in rows)
    return profiles


async def list_region_assignees(
    session: Any, *, region_ids: Sequence[int]
) -> dict[int, list[Assignee]]:
    """按区域批量取**可选接单人**（`region_id` → 启用中的运营人员列表）。

    为什么一次取多个区域而不是“一个设备一次查询”：06:00 分析要跨区域处理上百台设备，
    逐台查人就是典型的 N+1（AGENTS §10 列表接口循环内查库是重点检查项）。

    过滤条件三件套（缺一不可，与 `EmpController.businessList` 逐条对齐）：
      - `region_id`：`TaskServiceImpl` 强校验 `emp.regionId == vm.regionId`；
      - `role_code = 1002`：只有运营人员该接补货工单；
      - `status = 1`：停用账号不能派单。

    `order by region_id, id` 是**确定性保证**：负载相同时候选顺序稳定，分配结果可复现（可测试）。
    完全无匹配的区域**不出现在返回字典里**，由调用方决定降级为「待指派」，而不是静默选一个错的人。
    """
    codes = [int(r) for r in region_ids if r is not None]
    if not codes:
        return {}
    settings = get_settings()
    grouped: dict[int, list[Assignee]] = {}
    # 区域数很少（本机 4 个），但机器上可能上百个→同样分批，避免 IN 列表无上限
    deduped = list(dict.fromkeys(codes))
    step = max(1, settings.read_batch_size)
    for start in range(0, len(deduped), step):
        batch = deduped[start : start + step]
        rows = await _fetch_all(
            session,
            """
            select id as emp_id, user_name, region_id
            from tb_emp
            where region_id in :region_ids
              and role_code = :role_code
              and status = :status
            order by region_id, id
            limit :limit
            """,
            {
                "region_ids": tuple(batch),
                "role_code": ASSIGNEE_ROLE_CODE,
                "status": EMP_STATUS_NORMAL,
                "limit": settings.read_row_limit,
            },
            tables=("tb_emp",),
            expanding=("region_ids",),
        )
        for row in rows:
            assignee = Assignee(**dict(row))
            grouped.setdefault(assignee.region_id, []).append(assignee)
    return grouped


async def list_channel_stock(
    session: Any, *, inner_codes: Sequence[str], limit: int | None = None
) -> list[ChannelStockItem]:
    """按设备编号批量取“货道 + 库存”（1-1 主查询，内部按 vm 分批）。"""
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    settings = get_settings()
    items: list[ChannelStockItem] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select i.vm_id, v.inner_code, i.channel_id, c.channel_code,
                   i.sku_id as inventory_sku_id, c.sku_id as channel_sku_id,
                   i.current_stock, i.min_stock, i.is_alert,
                   c.max_capacity as channel_max_capacity,
                   i.max_stock as inventory_max_stock,
                   i.last_supply_time, i.last_sell_time
            from tb_inventory i
            join tb_channel c on c.id = i.channel_id
            join tb_vending_machine v on v.id = i.vm_id
            where v.inner_code in :inner_codes
            order by i.vm_id, c.channel_code
            limit :limit
            """,
            {
                "inner_codes": tuple(batch),
                "limit": limit or settings.read_row_limit,
            },
            tables=("tb_inventory", "tb_channel", "tb_vending_machine"),
            expanding=("inner_codes",),
        )
        items.extend(ChannelStockItem(**dict(row)) for row in rows)
    return items


async def aggregate_channel_sales(
    session: Any,
    *,
    inner_codes: Sequence[str],
    start: datetime,
    end: datetime,
    limit: int | None = None,
) -> list[ChannelSales]:
    """窗口内按货道聚合销量（1-2 主查询）。

    - 时间过滤用**范围比较**（`create_time >= :start and < :end`，半开区间，避免边界重复计数）；
    - 按 vm 分批执行，单批超时即熔断；
    - 只统计 `tb_order.status = sales_order_status`（默认 2=出货成功，口径见模块 docstring）。
    """
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    if end <= start:
        raise ToolError("end 必须晚于 start（半开区间 [start, end)）")
    settings = get_settings()
    sales: list[ChannelSales] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select o.inner_code, o.channel_code, max(o.sku_id) as sku_id,
                   count(*) as order_count, count(*) as qty,
                   coalesce(sum(o.amount), 0) as amount,
                   max(o.create_time) as last_order_at
            from tb_order o
            where o.inner_code in :inner_codes
              and o.create_time >= :start
              and o.create_time < :end
              and o.status = :status
            group by o.inner_code, o.channel_code
            order by o.inner_code, o.channel_code
            limit :limit
            """,
            {
                "inner_codes": tuple(batch),
                "start": start,
                "end": end,
                "status": settings.sales_order_status,
                "limit": limit or settings.read_row_limit,
            },
            tables=("tb_order",),
            expanding=("inner_codes",),
        )
        sales.extend(ChannelSales(**dict(row)) for row in rows)
    return sales


async def aggregate_channel_daily_sales(
    session: Any,
    *,
    inner_codes: Sequence[str],
    start: datetime,
    end: datetime,
    limit: int | None = None,
) -> list[ChannelDailySales]:
    """窗口内按「货道 × 自然日」聚合销量（1-4 基线引擎的输入）。

    与 {@link aggregate_channel_sales} 的区别只有一个：多一层 `group by date(...)`，
    换来的是**分位数**（需要逐日样本）而不是只有均值。

    为什么 `group by date(o.create_time)` 不算“函数包裹索引列”的违规写法（排期 1-2 禁令）：
    禁令针对的是 **WHERE 条件**里的函数包裹（`date_format(create_time)=?` 会让索引直接失效，
    `docs/ddl/business_tables_survey.md` §2.2 有量化证据：预估扫描行 2 → 800）。
    这里 WHERE 仍是范围比较（可用 `(inner_code, create_time)` 索引定位），
    `date()` 只作用在**已被索引筛出的行**上用于分组——扫描量不变。

    `group by` 未使用 `date_format` 的原因：`DATE(create_time)` 是同类里最轻的写法
    （不需要把时间格式化成字符串再比）。
    """
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    if end <= start:
        raise ToolError("end 必须晚于 start（半开区间 [start, end)）")
    settings = get_settings()
    daily: list[ChannelDailySales] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select o.inner_code, o.channel_code, date(o.create_time) as sale_date,
                   count(*) as order_count, count(*) as qty
            from tb_order o
            where o.inner_code in :inner_codes
              and o.create_time >= :start
              and o.create_time < :end
              and o.status = :status
            group by o.inner_code, o.channel_code, date(o.create_time)
            order by o.inner_code, o.channel_code, sale_date
            limit :limit
            """,
            {
                "inner_codes": tuple(batch),
                "start": start,
                "end": end,
                "status": settings.sales_order_status,
                "limit": limit or settings.read_row_limit,
            },
            tables=("tb_order",),
            expanding=("inner_codes",),
        )
        daily.extend(ChannelDailySales(**dict(row)) for row in rows)
    return daily


async def list_inflight_tasks(
    session: Any, *, inner_codes: Sequence[str], limit: int | None = None
) -> list[InflightTask]:
    """批量查询在途补货工单（含明细货道号）。

    为什么必须查在途：`TaskServiceImpl.insertTaskDto` 对“同设备已有未完成工单”会直接抛异常，
    Agent 若不知道在途工单就会反复生成会被拒的建议（体验与信任都受损）。
    """
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    settings = get_settings()
    tasks: list[InflightTask] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select t.task_id, t.task_code, t.inner_code, t.task_status,
                   t.product_type_id, t.user_id, t.create_time,
                   d.channel_code
            from tb_task t
            left join tb_task_details d on d.task_id = t.task_id
            where t.inner_code in :inner_codes
              and t.product_type_id = :product_type_id
              and t.task_status in :statuses
            order by t.create_time desc
            limit :limit
            """,
            {
                "inner_codes": tuple(batch),
                "product_type_id": TASK_TYPE_SUPPLY,
                "statuses": tuple(TASK_STATUS_INFLIGHT),
                "limit": limit or settings.read_row_limit,
            },
            tables=("tb_task", "tb_task_details"),
            expanding=("inner_codes", "statuses"),
        )
        merged: dict[int, InflightTask] = {}
        for row in rows:
            data = dict(row)
            channel_code = data.pop("channel_code", None)
            task = merged.get(data["task_id"])
            if task is None:
                task = InflightTask(**data)
                merged[data["task_id"]] = task
            if channel_code:
                task.channels.append(channel_code)
        tasks.extend(merged.values())
    return tasks


def default_sales_window(*, days: int, today: date | None = None) -> tuple[datetime, datetime]:
    """默认分析窗口：[today-days, today) 的零点，半开区间便于拼接与对账。

    为什么要这个工具函数：窗口边界算错（闭区间/含当天与否）会让 Agent 的建议量与 Java 报表
    对不上账，而这正是 1-2 的验收项——把边界算法收敛到一处，避免每个调用方各算一遍。
    """
    if days <= 0:
        raise ToolError("days 必须为正整数")
    base = today or date.today()
    start_date = base - timedelta(days=days)
    return (
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(base, datetime.min.time()),
    )
