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
# 接单人角色（与 Java 侧建单校验、前端选人来源同源）：
#   补货工单 → 运营人员 1002（businessList）；维修/投放/撤机 → 维修人员 1003（operationList）。
# 为什么必须分开：Java 侧 2026-10-28 起按工单类型校验角色，派错角色会在回调建单时被拒
# （「该员工角色不能执行此类工单」），且 dkd-app 的作业队列也是按角色分的。
ASSIGNEE_ROLE_CODE = "1002"
ASSIGNEE_ROLE_CODE_REPAIR = "1003"
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
    session: Any, *, region_ids: Sequence[int], role_code: str = ASSIGNEE_ROLE_CODE
) -> dict[int, list[Assignee]]:
    """按区域批量取**可选接单人**（`region_id` → 启用中的指定角色人员列表）。

    为什么一次取多个区域而不是“一个设备一次查询”：06:00 分析要跨区域处理上百台设备，
    逐台查人就是典型的 N+1（AGENTS §10 列表接口循环内查库是重点检查项）。

    过滤条件三件套（缺一不可，与 `EmpController.businessList/operationList` 逐条对齐）：
      - `region_id`：`TaskServiceImpl` 强校验 `emp.regionId == vm.regionId`；
      - `role_code`：补货=1002(运营)、维修/投放/撤机=1003(维修)——默认 1002 保持补货旧行为；
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
                "role_code": role_code,
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

    ## 为什么必须过滤 `channel_code is not null`（2026-10-01 修，真库实测）

    本函数是**按货道**聚合，而 `channel_code` 为空的行**无法归因到任何货道**。
    不过滤会同时造成两个后果：

    1. **工具直接崩**：分组结果里会出现 `channel_code=None`，而 `ChannelSales.channel_code`
       是必填 `str` → Pydantic 校验失败报错。这不是“边界情况”，本机开发库 7 张
       出货成功订单里就有 2 张 `channel_code` 为空（占 29%）——1-4 基线引擎与
       06:00 定时任务遇到只含这类订单的设备会直接挂掉。
    2. **语义错误**：就算放开模型允许 None，一个没有货道的“货道聚合行”
       到了基线引擎也无处安放（映射不到货道）。

    ## 但过滤掉不等于可以假装它们不存在

    被过滤的订单是**真实销量**，且是数据质量信号（下单时没回填货道）。所以：
      • 对账/巡检必须能把它们算出来——`docs/scripts/verify-1-12-reconcile.py`
        把「Java 全局口径 = Agent 可归因口径 + 不可归因口径」做成**守恒断言**，
        而不是让它们静静消失；
      • 真实库中这类订单的占比应由业务侧决定要不要修数据（现已登记观察）。
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
              and o.channel_code is not null
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

    同样过滤 `channel_code is not null`：理由与 `aggregate_channel_sales` 完全一致
    （不可归因到货道的行既会撞 Pydantic 必填校验、又无法进基线引擎），
    两个函数的过滤条件必须保持一致——否则“总量对得上、逐日对不上”会成为新的排查黑洞。
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
              and o.channel_code is not null
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


# ----------------------------------------------------------------------------------
# 任务 2-1：异常检测（订单异常 / 库存异常变动）——只读，不改任何东西
# ----------------------------------------------------------------------------------
#
# ## 为什么异常检测要放在 read_tools（而不是新开一个模块）
#
# 它和 1-1/1-2 是同一类东西：**“把业务事实读出来、把口径写清楚”**。并且必须复用同一套护栏
# （表白名单 / 范围比较 / 分批 / 超时），否则就会像 `OrderMapper.xml:44` 那样在分析查询里
# 把索引列包进函数（AGENTS §10 点名）。少一个取数通道 = 少一处口径漂移。
#
# ## 口径来源（AGENTS §9.3：不猜）
#
# `tb_order` 列注释（本机 `information_schema` 实测）：
#   `status`: 0-待支付 / 1-支付完成 / 2-出货成功 / 3-出货失败 / 4-已取消
#   `pay_status`: 0-未支付 / 1-支付完成 / 2-退款中 / 3-退款完成
# `tb_inventory_log` 列注释（本机实测，14 列）：
#   `change_type`: 1销售 2补货 3损耗 4调整 5盘点；`change_amount` 正数增加/负数减少；
#   `order_id`: 关联订单ID（**销售类型时**才有）；另有 before_stock/after_stock/remark。
#
# ⚠️ 本机 `tb_inventory_log` 是**空表（0 行）**，所以库存异常的“识别正确性”只能靠**构造样本**
# 验证（排期 2-1 的验收标准也正是“构造异常样本验证”），真库上只能验证“SQL 能跑通”（返回 0 行）。
# 这一点在单测与验收脚本里都要写明，不能因为“跑过没报错”就说库存异常检测已验证。

# 订单状态（`tb_order.status`，口径同上）
ORDER_STATUS_PAID = 1
ORDER_STATUS_VEND_FAILED = 3
# 支付状态（`tb_order.pay_status`）：1-支付完成 2-退款中（都属于“钱还在平台”）
PAY_STATUS_PAID = 1
PAY_STATUS_REFUNDING = 2

# 库存变动类型（`tb_inventory_log.change_type`）
INVENTORY_CHANGE_SALE = 1
INVENTORY_CHANGE_LOSS = 3
INVENTORY_CHANGE_ADJUST = 4

# “支付完成但迟迟未出货”的时效阀值（分钟）。
# 为什么需要阀值而不是“status=1 就算异常”：**status=1 是正常的中转态**
# （从支付成功到出货完成必经过它，通常几秒），没有阀值会把正在出货的订单全报成异常。
DEFAULT_PAID_STALE_MINUTES = 10

# 维修工单的 `product_type_id`。权威来源两处交叉验证：
#   · `DkdContants.java`：`TASK_TYPE_REPAIR = 3l`；
#   · `tb_task_type`（本机实测）：1=投放 / 2=补货 / **3=维修** / 4=撤机。
# ⚠️ 这里刻意不写 1：`product_type_id=1` 是**投放工单**——2-7 抽样脚本初版就是把 1 当成了维修，
# 于是把 8 条投放工单当成“故障案例”（详见 `docs/scripts/sample-2-7-case-library.py` docstring）。
# 教训：字段名看起来直白不等于语义直白，枚举必须去代码/字典表里核（AGENTS §9.3 只读不猜）。
REPAIR_PRODUCT_TYPE_ID = 3
# 已完成（`DkdContants.TASK_STATUS_FINISH = 4L`）：只有完成的工单才代表“确实处理过”
TASK_STATUS_FINISH = 4

# 2-2 的规则检索：描述能不能当“故障现象”用的启发式阀值
MIN_PHENOMENON_CHARS = 8
# 已知的非现象话术（流程备注/口头禅/占位符）。
# ⚠️ 局限性必须写明：靠词表维护、一定会漏；真正的解法是 **FIX-22**（补结构化的现象/原因/处置）。
# 在那之前宁可把可疑的判成“不可用”（宁漏不编）：把“111”当故障现象讲给运维听，比说“没案例”更危险。
NON_PHENOMENON_PHRASES = (
    "后台工作人员取消",
    "工作人员取消",
    "取消",
    "加急",
    "投放",
    "撤机",
    "测试",
    "不想干了",
)

# 规则检索的三级降级（顺序即优先级，不可打乱）
CASE_MATCH_SAME_DEVICE = "same_device"
CASE_MATCH_SAME_MODEL = "same_model"
CASE_MATCH_SAME_REGION = "same_region"
_CASE_MATCH_RANK = {
    CASE_MATCH_SAME_DEVICE: 0,
    CASE_MATCH_SAME_MODEL: 1,
    CASE_MATCH_SAME_REGION: 2,
}

# 历史案例的默认检索窗口（1095 天 = 3 年）。
# 为什么给个较大的默认值：维修工单很稀疏（本机 5 条全部落在 2023-03），窗口太短会得出“没有历史”
# 的假结论；但也不能无界——单主库（无从库）上任何分析查询都要有时间窗口 + 上限（AGENTS §10）。
DEFAULT_CASE_WINDOW_DAYS = 1095


class OrderAnomaly(BaseModel):
    """订单异常（2-1）：支付成功但未出货的两类形态。

    | kind | 含义 | 判定 |
    | --- | --- | --- |
    | `paid_not_vended` | 支付完成但迟迟未出货 | `status=1` 且早于阀值 |
    | `vend_failed` | 已扣款但出货失败（钱还在平台/退款中） | `status=3` 且 `pay_status in (1,2)` |

    为什么 `status=3` 还要看 `pay_status`：`pay_status=3`（已退款）意味着已经处理完，
    再报成异常只会制造噪音——而**告警一旦噪音化就没人看了**（AGENTS §6.4 的同一条精神）。
    """

    kind: str
    order_id: int
    order_no: str | None = None
    inner_code: str | None = None
    channel_code: str | None = None
    status: int
    pay_status: int | None = None
    amount: int | None = None
    create_time: datetime | None = None
    age_minutes: int | None = None
    reason: str


class InventoryAnomaly(BaseModel):
    """库存异常变动（2-1）：从 `tb_inventory_log` 里找出“账对不上”的行。

    | kind | 含义 | 判定 |
    | --- | --- | --- |
    | `deduct_without_order` | **无单扣减** | `change_type=1`、扣减、`order_id` 未命中订单 |
    | `ledger_mismatch` | 记录自相矛盾：`before + change ≠ after` | 三列均非空且等式不成立 |
    | `unexplained_deduction` | 损耗/调整扣减未填备注 | `change_type in (3,4)`、扣减、备注为空 |

    “无单扣减”为什么要 `left join tb_order` 而不是只看 `order_id is null`：
    `order_id` 列注释说“销售类型时”才有，但**填了一个不存在的 ID**（脏数据/跨库导入）同样危险，
    而且从库存日志这一侧根本分不出来，所以用左连接直接把“命中不到”也归入同一类。
    """

    kind: str
    log_id: int
    vm_id: int
    inner_code: str | None = None
    channel_id: int | None = None
    sku_id: int | None = None
    change_type: int | None = None
    change_amount: int | None = None
    before_stock: int | None = None
    after_stock: int | None = None
    order_id: int | None = None
    operator_name: str | None = None
    create_time: datetime | None = None
    reason: str


def _order_anomaly_reason(kind: str, row: dict[str, Any]) -> str:
    """把判定结果写成给运营/诊断看的人话（理由要与阈值一起给出，否则无法复核）。"""
    if kind == "vend_failed":
        return (
            f"订单 {row.get('order_no')} 已支付但出货失败"
            f"（status=3, pay_status={row.get('pay_status')}），需确认是否已退款或需现场处理"
        )
    return (
        f"订单 {row.get('order_no')} 支付完成已 {row.get('age_minutes')} 分钟仍未出货（status=1），"
        f"超过 {DEFAULT_PAID_STALE_MINUTES} 分钟阀值，疑似卡单"
    )


async def list_order_anomalies(
    session: Any,
    *,
    inner_codes: Sequence[str],
    start: datetime,
    end: datetime,
    stale_minutes: int = DEFAULT_PAID_STALE_MINUTES,
    limit: int | None = None,
) -> list[OrderAnomaly]:
    """按设备 + 时间窗扫描订单异常（2-1 的读工具）。

    - 时间过滤用**范围比较**（`create_time >= :start and < :end`，半开区间）——与 1-2 一致，
      禁用 `date_format()`（会把 `idx_order_inner_code_create_time` 的 `create_time` 部分废掉）；
    - `stale_minutes` 是“多久没出货算卡单”的阀值，**可配**：不同机型/网络环境出货耗时不同，
      写死会让告警要么漏报要么噪音化；
    - 只读：不做任何写操作，也不会“自动补单”（处置仍走人工/工作台）。
    """
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    if end <= start:
        raise ToolError("end 必须晚于 start（半开区间 [start, end)）")
    if stale_minutes < 0:
        raise ToolError("stale_minutes 不能为负")
    settings = get_settings()
    found: list[OrderAnomaly] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select o.id as order_id, o.order_no, o.inner_code, o.channel_code,
                   o.status, o.pay_status, o.amount, o.create_time,
                   timestampdiff(minute, o.create_time, now()) as age_minutes,
                   case when o.status = :vend_failed then 'vend_failed'
                        else 'paid_not_vended' end as kind
            from tb_order o
            where o.inner_code in :inner_codes
              and o.create_time >= :start
              and o.create_time < :end
              and (
                    (o.status = :vend_failed
                     and coalesce(o.pay_status, 0) in (:pay_paid, :pay_refunding))
                 or (o.status = :paid
                     and o.create_time <= :stale_before)
                  )
            order by o.create_time desc
            limit :limit
            """,
            {
                "inner_codes": tuple(batch),
                "start": start,
                "end": end,
                "paid": ORDER_STATUS_PAID,
                "vend_failed": ORDER_STATUS_VEND_FAILED,
                "pay_paid": PAY_STATUS_PAID,
                "pay_refunding": PAY_STATUS_REFUNDING,
                "stale_before": end - timedelta(minutes=stale_minutes)
                if end < datetime.now()
                else datetime.now() - timedelta(minutes=stale_minutes),
                "limit": limit or settings.read_row_limit,
            },
            tables=("tb_order",),
            expanding=("inner_codes",),
        )
        for row in rows:
            data = dict(row)
            kind = str(data.pop("kind"))
            found.append(
                OrderAnomaly(
                    kind=kind,
                    reason=_order_anomaly_reason(kind, data),
                    **data,
                )
            )
    return found


async def list_inventory_anomalies(
    session: Any,
    *,
    inner_codes: Sequence[str],
    start: datetime,
    end: datetime,
    limit: int | None = None,
) -> list[InventoryAnomaly]:
    """按设备 + 时间窗扫描库存变动异常（2-1 的读工具）。

    三种形态（判定条件见 `InventoryAnomaly` docstring）：无单扣减 / 记录自相矛盾 / 无说明扣减。

    ⚠️ 真库现状：本机 `tb_inventory_log` **0 行**，所以本函数的正确性靠**构造样本**验证
    （`tests/test_read_tools.py`）与真库可执行性（返回空）共同覆盖——不能声称“真库验过”。
    """
    codes = _normalize_inner_codes(inner_codes)
    if not codes:
        return []
    if end <= start:
        raise ToolError("end 必须晚于 start（半开区间 [start, end)）")
    settings = get_settings()
    found: list[InventoryAnomaly] = []
    for batch in _batched(codes, settings.read_batch_size):
        rows = await _fetch_all(
            session,
            """
            select l.id as log_id, l.vm_id, v.inner_code, l.channel_id, l.sku_id,
                   l.change_type, l.change_amount, l.before_stock, l.after_stock,
                   l.order_id, l.operator_name, l.create_time,
                   case
                     when l.before_stock is not null and l.change_amount is not null
                          and l.after_stock is not null
                          and l.before_stock + l.change_amount <> l.after_stock
                       then 'ledger_mismatch'
                     when l.change_type = :sale and l.change_amount < 0 and o.id is null
                       then 'deduct_without_order'
                     else 'unexplained_deduction'
                   end as kind
            from tb_inventory_log l
            join tb_vending_machine v on v.id = l.vm_id
            left join tb_order o on o.id = l.order_id
            where v.inner_code in :inner_codes
              and l.create_time >= :start
              and l.create_time < :end
              and (
                    (l.before_stock is not null and l.change_amount is not null
                     and l.after_stock is not null
                     and l.before_stock + l.change_amount <> l.after_stock)
                 or (l.change_type = :sale and l.change_amount < 0 and o.id is null)
                 or (l.change_type in (:loss, :adjust) and l.change_amount < 0
                     and (l.remark is null or trim(l.remark) = ''))
                  )
            order by l.create_time desc
            limit :limit
            """,
            {
                "inner_codes": tuple(batch),
                "start": start,
                "end": end,
                "sale": INVENTORY_CHANGE_SALE,
                "loss": INVENTORY_CHANGE_LOSS,
                "adjust": INVENTORY_CHANGE_ADJUST,
                "limit": limit or settings.read_row_limit,
            },
            tables=("tb_inventory_log", "tb_vending_machine", "tb_order"),
            expanding=("inner_codes",),
        )
        for row in rows:
            data = dict(row)
            kind = str(data.pop("kind"))
            found.append(
                InventoryAnomaly(kind=kind, reason=_inventory_anomaly_reason(kind, data), **data)
            )
    return found


def _inventory_anomaly_reason(kind: str, row: dict[str, Any]) -> str:
    """库存异常的人话理由（把**数字**带上，否则无法人工复核）。"""
    if kind == "ledger_mismatch":
        return (
            f"库存记录自相矛盾：变动前 {row.get('before_stock')} + 变动 {row.get('change_amount')} "
            f"≠ 变动后 {row.get('after_stock')}（日志 id={row.get('log_id')}）"
        )
    if kind == "deduct_without_order":
        return (
            f"无单扣减：销售类型扣了 {row.get('change_amount')} 件，但关联不到订单"
            f"（order_id={row.get('order_id')}，日志 id={row.get('log_id')}）"
        )
    return (
        f"无说明扣减：损耗/调整类扣了 {row.get('change_amount')} 件且未填备注"
        f"（操作人={row.get('operator_name') or '未记录'}，日志 id={row.get('log_id')}）"
    )


# ----------------------------------------------------------------------------------
# 任务 2-2：历史维修工单检索 + 相似案例匹配（**规则库版**，不建向量库）
# ----------------------------------------------------------------------------------
#
# ## 为什么是规则库而不是语义检索（这是排期的预案，不是简化）
#
# 排期 2-7 的完成标准写着「先做 0.5d 数据质量抽样，不达标则改口径或**降级为规则库**」，
# 抽样结果（`docs/scripts/sample-2-7-case-library.py`，已修正枚举后重跑）：
#   维修工单 **5 条**（<50）、平均描述长度 **4.0 字**（<15）、**无原因/处置/故障码字段**。
# 实际内容：`111` ×4 + `后台工作人员取消` ×1——**没有一条是故障现象**。
# 在这种数据上做语义检索，只会得到“看起来很像”的不可验证结果（比没有检索更危险）。
#
# ## 规则库的“相似”怎么定义（本文件实现的）
#
# 三级降级（顺序即优先级）：
#   ① `same_device` —— 该设备自己的历史维修单（最强证据）
#   ② `same_model`  —— 同型号（`tb_vending_machine.vm_type_id`）其它设备的维修单
#   ③ `same_region` —— 同区域其它设备的维修单（最弱，只作环境参考）
# 并且把该设备**近期异常特征**（2-1 的订单/库存异常）一并返回——“按设备 + 型号 + 近期异常特征”
# 里的第三项在这里落地：异常本身不能搜出案例，但它决定了“要不要拿历史案例说事”。
#
# ## 最关键的诚实点：phenomenon_usable
#
# 每条案例都带 `phenomenon_usable`（该描述能不能当故障现象用）。
# 本机实测 **可用案例 = 0**。于是调用方（2-3）必须走保守话术：
#   「查到 N 条历史维修单，但都是流程备注/占位文本，不足以支撑诊断结论」。
# 不标这个字段，模型会把 `111` 当成“同类故障现象”讲出来——那是**编造证据**。


def phenomenon_usable(text: str | None) -> bool:
    """描述能不能当“故障现象”用（2-2 的核心诚实点）。

    判定（全部满足才算可用）：
      ① 去除空白后长度 ≥ `MIN_PHENOMENON_CHARS`；
      ② 不是纯数字/纯符号（如 `111`）；
      ③ 不在 `NON_PHENOMENON_PHRASES`（流程备注/口头禅）。

    ⚠️ 这是**启发式**，不是判准：词表一定会漏，也无法判断“描述看着像现象但其实是别的问题”。
    真实解法是 FIX-22（维修工单补结构化的现象/原因/处置）——这里只保证**不把明显不是现象的当现象**。
    """
    if text is None:
        return False
    value = str(text).strip()
    if len(value) < MIN_PHENOMENON_CHARS:
        return False
    if value.replace(".", "").replace("-", "").isdigit():
        return False
    return not any(phrase in value for phrase in NON_PHENOMENON_PHRASES)


class HistoryCase(BaseModel):
    """一条历史维修单（2-2 的检索结果项）。

    `phenomenon` 是**原文**（不脱敏/不改写），`phenomenon_usable` 才是判定；
    两者必须一起看：把原文删掉就无法人工复核，把判定删掉模型就会当真。
    """

    task_id: int
    task_code: str
    inner_code: str
    task_status: int
    task_status_label: str
    phenomenon: str | None = None
    phenomenon_usable: bool = False
    matched_by: str
    confidence: str
    confidence_basis: str
    operator_name: str | None = None
    region_id: int | None = None
    region_name: str | None = None
    addr: str | None = None
    vm_type_id: int | None = None
    vm_type_name: str | None = None
    created_time: datetime | None = None
    finished_time: datetime | None = None


class HistoryCaseSearch(BaseModel):
    """一次历史案例检索的完整结果。

    为什么不只返回 `list[HistoryCase]`（这是个刻意选择）：
    **空列表无法区分“没有相似案例”与“根本没查”**。把“查了什么、命中几条、
    可用几条、结论是什么”一起返回，调用方（2-3/2-4）才能在不编造的前提下说话。
    """

    inner_code: str | None = None
    vm_type_id: int | None = None
    window: dict[str, Any] = Field(default_factory=dict)
    cases: list[HistoryCase] = Field(default_factory=list)
    matched_by_counts: dict[str, int] = Field(default_factory=dict)
    total_cases: int = 0
    usable_case_count: int = 0
    recent_order_anomalies: int = 0
    recent_inventory_anomalies: int = 0
    caliber: str = ""
    no_evidence_hint: str | None = None


_TASK_STATUS_LABELS = {1: "待接单", 2: "进行中", 3: "已取消", 4: "已完成"}


def default_case_window(
    *, days: int = DEFAULT_CASE_WINDOW_DAYS, today: date | None = None
) -> tuple[datetime, datetime]:
    """历史案例检索的默认窗口（半开区间；与 `default_sales_window` 同一套边界算法）。"""
    return default_sales_window(days=days, today=today)


async def search_history_cases(
    session: Any,
    *,
    inner_code: str | None = None,
    vm_type_id: int | None = None,
    region_id: int | None = None,
    start: datetime,
    end: datetime,
    limit: int | None = None,
    with_anomalies: bool = True,
) -> HistoryCaseSearch:
    """按“设备 → 型号 → 区域”三级降级检索历史维修单（2-2）。

    @param inner_code  目标设备；给了就自动取它的型号/区域做降级（无需调用方再查一次）
    @param vm_type_id  显式指定型号（设备级检索时可不传）
    @param region_id   显式指定区域
    @param start/end   时间窗口（**必填**：单主库上分析查询必须有窗口 + 上限）
    @param with_anomalies  是否顺带取该设备近期异常（默认取；关掉只是为了单测不搭桩）

    返回 `HistoryCaseSearch`（不是裸列表）：空列表区分不了“没案例”与“没查”，
    而 2-3/2-4 能不能说“无历史证据”就靠这个区分。
    """
    if end <= start:
        raise ToolError("end 必须晚于 start（半开区间 [start, end)）")
    code = (inner_code or "").strip() or None
    if code is None and vm_type_id is None and region_id is None:
        raise ToolError("至少要给一个检索维度（inner_code / vm_type_id / region_id）")
    settings = get_settings()

    # 1) 给了设备就算它的型号/区域（让调用方只传一个 inner_code 就能拿到三级降级）
    vm_type_name: str | None = None
    if code:
        profile = await get_machine_profile(session, code)
        if profile is None:
            raise ToolError(f"未找到设备 {code}（编码是否正确？）")
        rows = await _fetch_all(
            session,
            """
            select v.vm_type_id, v.region_id, ty.name as vm_type_name
            from tb_vending_machine v
            left join tb_vm_type ty on ty.id = v.vm_type_id
            where v.inner_code = :inner_code
            limit 1
            """,
            {"inner_code": code},
            tables=("tb_vending_machine", "tb_vm_type"),
        )
        if rows:
            data = dict(rows[0])
            vm_type_id = vm_type_id if vm_type_id is not None else data.get("vm_type_id")
            region_id = region_id if region_id is not None else data.get("region_id")
            vm_type_name = data.get("vm_type_name")

    # 2) 同型号的“兄弟设备”名单（参数化取，**不做 inner_code 列对列 join**）
    #
    # ⚠️ 为什么绕一步（2026-10-01 真库实测撞到 1267）：`tb_task.inner_code` 是
    #   utf8mb4_unicode_ci（老表），`tb_vending_machine.inner_code` 是 utf8mb4_0900_ai_ci
    #   （新表），两表 collation 不一致 → 写 `on v.inner_code = t.inner_code` 会报 ERROR 1267。
    #   本机 Java mapper 没有按 inner_code 的列对列 join，所以这个坑此前从未被触发（FIX-25）。
    #   处理：**不把 collation 写死进业务 SQL**（DBA 换 collation 会反过来报错），
    #   改为“取兄弟设备”与“按编号集合取工单”两步，全部走**参数比较**（参数可强制转换）。
    peer_codes: list[str] = []
    if vm_type_id is not None:
        peer_rows = await _fetch_all(
            session,
            """
            select v.inner_code
            from tb_vending_machine v
            where v.vm_type_id = :vm_type_id
            order by v.id
            limit :limit
            """,
            {"vm_type_id": vm_type_id, "limit": settings.read_row_limit},
            tables=("tb_vending_machine",),
        )
        peer_codes = [str(dict(row).get("inner_code") or "") for row in peer_rows]
        peer_codes = [c for c in peer_codes if c]

    case_codes = sorted({*(peer_codes or []), *([code] if code else [])})
    if not case_codes and region_id is None:
        raise ToolError("未解析出任何可检索的设备编号或区域（无法检索）")

    # 3) 三级降级的候选集：**一条 SQL 拿到全部候选**（同设备/同型号的编号集合 OR 同区域），
    #    匹配级别在 Python 里判定——为什么不在 SQL 里算：级别是业务语义，写在 SQL 里难单测，
    #    也容易被后续改动静默改跑。
    rows = await _fetch_all(
        session,
        """
        select t.task_id, t.task_code, t.inner_code, t.task_status, t.user_name,
               t.`desc` as phenomenon, t.create_time, t.update_time, t.region_id, t.addr,
               r.region_name
        from tb_task t
        left join tb_region r on r.id = t.region_id
        where t.product_type_id = :repair_type_id
          and t.create_time >= :start
          and t.create_time < :end
          and (
                (t.inner_code in :case_codes)
             or (:region_id is not null and t.region_id = :region_id)
              )
        order by t.create_time desc
        limit :limit
        """,
        {
            "repair_type_id": REPAIR_PRODUCT_TYPE_ID,
            "start": start,
            "end": end,
            "case_codes": tuple(case_codes or [""]),
            "region_id": region_id,
            "limit": limit or settings.read_row_limit,
        },
        tables=("tb_task", "tb_region"),
        expanding=("case_codes",),
    )

    # 3.1) 命中设备的型号信息（同样用参数比较，避开 collation 问题）
    hit_codes = sorted({str(dict(row).get("inner_code") or "") for row in rows} - {""})
    model_by_code: dict[str, tuple[int | None, str | None]] = {}
    if hit_codes:
        model_rows = await _fetch_all(
            session,
            """
            select v.inner_code, v.vm_type_id, ty.name as vm_type_name
            from tb_vending_machine v
            left join tb_vm_type ty on ty.id = v.vm_type_id
            where v.inner_code in :hit_codes
            """,
            {"hit_codes": tuple(hit_codes)},
            tables=("tb_vending_machine", "tb_vm_type"),
            expanding=("hit_codes",),
        )
        for row in model_rows:
            data = dict(row)
            model_by_code[str(data.get("inner_code"))] = (
                data.get("vm_type_id"),
                data.get("vm_type_name"),
            )

    peer_set = set(peer_codes)
    cases: list[HistoryCase] = []
    for row in rows:
        data = dict(row)
        case_code = str(data.get("inner_code") or "")
        if code and case_code == code:
            matched = CASE_MATCH_SAME_DEVICE
        elif case_code in peer_set:
            matched = CASE_MATCH_SAME_MODEL
        else:
            matched = CASE_MATCH_SAME_REGION
        case_vm_type_id, case_vm_type_name = model_by_code.get(
            case_code, (vm_type_id, vm_type_name)
        )
        usable = phenomenon_usable(data.get("phenomenon"))
        confidence, basis = _case_confidence(
            matched, usable, same_vm_type_name=case_vm_type_name or vm_type_name
        )
        status = int(data.get("task_status") or 0)
        cases.append(
            HistoryCase(
                task_id=int(data["task_id"]),
                task_code=str(data["task_code"]),
                inner_code=case_code,
                task_status=status,
                task_status_label=_TASK_STATUS_LABELS.get(status, f"未知({status})"),
                phenomenon=data.get("phenomenon"),
                phenomenon_usable=usable,
                matched_by=matched,
                confidence=confidence,
                confidence_basis=basis,
                operator_name=data.get("user_name"),
                region_id=data.get("region_id"),
                region_name=data.get("region_name"),
                addr=data.get("addr"),
                vm_type_id=case_vm_type_id,
                vm_type_name=case_vm_type_name,
                created_time=data.get("create_time"),
                finished_time=data.get("update_time") if status == TASK_STATUS_FINISH else None,
            )
        )

    # 3) 稳定排序：先按匹配级别，再按时间新→旧
    cases.sort(
        key=lambda c: (
            _CASE_MATCH_RANK.get(c.matched_by, 9),
            -(c.created_time.timestamp() if c.created_time else 0),
        )
    )

    # 4) 近期异常特征（2-1）：它搜不出案例，但决定了“能不能拿历史案例说事”
    order_anomalies = 0
    inventory_anomalies = 0
    if code and with_anomalies:
        order_anomalies = len(
            await list_order_anomalies(session, inner_codes=[code], start=start, end=end)
        )
        inventory_anomalies = len(
            await list_inventory_anomalies(session, inner_codes=[code], start=start, end=end)
        )

    counts: dict[str, int] = {}
    for case in cases:
        counts[case.matched_by] = counts.get(case.matched_by, 0) + 1
    usable_count = sum(1 for case in cases if case.phenomenon_usable)

    caliber = (
        f"规则库检索（非语义检索）：产品类型={REPAIR_PRODUCT_TYPE_ID}（维修工单），"
        f"时间窗 [{start.isoformat()}, {end.isoformat()})，"
        "三级降级 same_device → same_model → same_region。"
        f"`phenomenon_usable` 为启发式判定（长度≥{MIN_PHENOMENON_CHARS} 且非纯数字且非流程话术）；"
        "历史维修工单当前普遍没有结构化的现象/原因/处置（FIX-22），因此大多为 false。"
    )
    hint: str | None = None
    if not cases:
        hint = (
            "该设备/型号/区域在窗口内没有任何历史维修工单。"
            "这不等于“没出过故障”，也不等于“不需要检查”；请基于现场现象与近期异常判断，不要拿历史案例凑证据。"
        )
    elif usable_count == 0:
        hint = (
            f"窗口内查到 {len(cases)} 条历史维修工单，但**没有一条可用作故障现象**"
            "（描述为流程备注/占位文本），不可据此下诊断结论；只能当“该设备/该型号有过维修记录”这一点用。"
        )

    return HistoryCaseSearch(
        inner_code=code,
        vm_type_id=vm_type_id,
        window={"start": start.isoformat(), "end": end.isoformat()},
        cases=cases,
        matched_by_counts=counts,
        total_cases=len(cases),
        usable_case_count=usable_count,
        recent_order_anomalies=order_anomalies,
        recent_inventory_anomalies=inventory_anomalies,
        caliber=caliber,
        no_evidence_hint=hint,
    )


def _case_confidence(
    matched_by: str, usable: bool, *, same_vm_type_name: str | None = None
) -> tuple[str, str]:
    """匹配可信度 + **依据**（置信度不能是没依据的数字）。

    为什么可信度上限被 locked 在“现象不可用”上：匹配级别再高，也只是“同一台设备曾经修过”，
    而**修的是什么问题并不知道**（描述不可用）。把这种案例标成 high 等于给了错觉。
    """
    if matched_by == CASE_MATCH_SAME_DEVICE:
        if usable:
            return "high", "同一台设备的历史维修单，且描述可用作故障现象"
        return (
            "low",
            "同一台设备确实有维修记录，但描述不是故障现象（流程备注/占位文本），无法支持诊断结论",
        )
    if matched_by == CASE_MATCH_SAME_MODEL:
        model = same_vm_type_name or "同型号"
        if usable:
            return "medium", f"同型号（{model}）其它设备的历史维修单，描述可用作参考现象"
        return "low", f"仅同型号（{model}）匹配，且描述不可用，只能当“该型号有过维修记录”"
    return "low", "仅同区域匹配（不是同设备也不是同型号），只能作环境参考"


# ----------------------------------------------------------------------------------
# 任务 2-3 的第一块：设备实体消歧（“3 号门那台机器” → inner_code）
# ----------------------------------------------------------------------------------
#
# ## 为什么要单独一个工具，而不是让模型猜
#
# 诊断场景里用户几乎不会报设备编号，而是报“3 号门那台”“洛阳那台饮料机”。
# 若让模型自己“猜一个编号”，它会猜出**看起来很像的假编号**（如 A1000002），
# 后面的库存/异常查询就会错到别的设备上——而我们无法从答案里看出来。
# 所以消歧必须是**真查询**：`inner_code` 精确 → 编号前缀 → 地址/点位模糊匹配，
# 并如实返回多个候选让用户确认。


class DeviceMatch(BaseModel):
    """一条设备匹配结果（带**为什么匹配上**，便于向用户解释与让其确认）。"""

    inner_code: str
    addr: str | None = None
    node_name: str | None = None
    region_name: str | None = None
    vm_type_name: str | None = None
    vm_status: int | None = None
    match_reason: str
    matched_text: str | None = None


MATCH_INNER_CODE_EXACT = "inner_code_exact"
MATCH_INNER_CODE_PREFIX = "inner_code_prefix"
MATCH_ADDR = "addr"
MATCH_NODE_NAME = "node_name"
_MATCH_RANK = {
    MATCH_INNER_CODE_EXACT: 0,
    MATCH_INNER_CODE_PREFIX: 1,
    MATCH_NODE_NAME: 2,
    MATCH_ADDR: 3,
}


async def find_devices(session: Any, *, query: str, limit: int = 10) -> list[DeviceMatch]:
    """按用户口述定位设备（设备编号 / 地址 / 点位名），返回候选 + 匹配原因。

    为什么一定要返回多个候选：模糊地址天然可能命中多台（“洛阳”有好几台），
    直接取第一条就是“静默选一个”，而选错设备的诊断结论看起来仍然很自信。
    匹配顺序（即优先级）：编号精确 → 编号前缀 → 点位名 → 地址。

    时间窗口？本工具不需要：设备档案是**当前态**（不是流水），不存在“扫描大表”的问题；
    但仍受 `read_row_limit` 封顶（白名单 + 行数上限照旧生效）。
    """
    text_query = (query or "").strip()
    if not text_query:
        raise ToolError("查询关键字不能为空（设备编号 / 地址 / 点位名）")
    settings = get_settings()
    like_query = f"%{text_query}%"
    rows = await _fetch_all(
        session,
        """
        select v.inner_code, v.addr, v.vm_status, n.node_name, r.region_name,
               ty.name as vm_type_name
        from tb_vending_machine v
        left join tb_node n on n.id = v.node_id
        left join tb_region r on r.id = v.region_id
        left join tb_vm_type ty on ty.id = v.vm_type_id
        where v.inner_code = :exact
           or v.inner_code like :prefix
           or n.node_name like :like_query
           or v.addr like :like_query
        limit :limit
        """,
        {
            "exact": text_query,
            "prefix": f"{text_query}%",
            "like_query": like_query,
            "limit": min(limit, settings.read_row_limit),
        },
        tables=("tb_vending_machine", "tb_node", "tb_region", "tb_vm_type"),
    )

    matches: list[DeviceMatch] = []
    for row in rows:
        data = dict(row)
        if data.get("inner_code") == text_query:
            reason, matched = MATCH_INNER_CODE_EXACT, str(data.get("inner_code"))
        elif str(data.get("inner_code") or "").startswith(text_query):
            reason, matched = MATCH_INNER_CODE_PREFIX, str(data.get("inner_code"))
        elif text_query in str(data.get("node_name") or ""):
            reason, matched = MATCH_NODE_NAME, data.get("node_name")
        else:
            reason, matched = MATCH_ADDR, data.get("addr")
        matches.append(
            DeviceMatch(
                inner_code=str(data.get("inner_code") or ""),
                addr=data.get("addr"),
                node_name=data.get("node_name"),
                region_name=data.get("region_name"),
                vm_type_name=data.get("vm_type_name"),
                vm_status=data.get("vm_status"),
                match_reason=reason,
                matched_text=str(matched) if matched else None,
            )
        )
    matches.sort(key=lambda m: _MATCH_RANK.get(m.match_reason, 9))
    return matches


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
