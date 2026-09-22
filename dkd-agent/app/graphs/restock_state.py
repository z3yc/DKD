"""补货子图的 **state schema（冻结版）** —— 任务 0-13 交付物。

## 为什么这里要单独冻结 schema

LangGraph 的持久化语义是：**最新图代码会立即作用于所有历史 checkpoint**。
因此每次发版都等价于对既有会话状态做一次「向后兼容的 API 变更」。

一旦 state 字段上线，就不能删改，只能新增并给默认值——否则历史会话在中断恢复时会解析失败。

## 冻结规则（评审通过后生效）

1. state **只放可 JSON 序列化的数据**（dict/list/str/int/float/bool/None）。
   - 原因：checkpointer 用 JSON 序列化状态；放自定义 class 需要注册反序列化，
     升级 LangGraph 时极易出现 "cannot deserialize" 类故障。
   - Pydantic 模型只用于「节点内部校验」与「对外契约」，进 state 前先 `.model_dump()`。
2. 字段名不得改名；新增字段必须有默认值（list 用 `Annotated[..., operator.add]` 或显式替换语义）。
3. 追加型集合用 `operator.add` reducer；其余字段为「后写覆盖」。
4. 删除字段需提供「存量会话处理方案」并走兼容性评审（详见冻结稿 §6）。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field, model_validator

# --- 场景与状态码：必须与 docs/ddl/agent_tables.sql 的列注释保持一致 ---
SCENE_RESTOCK = 2

PLAN_STATUS_SUGGESTED = 1
PLAN_STATUS_ADJUSTED = 2
PLAN_STATUS_SKIPPED = 3
PLAN_STATUS_ORDERED = 4
PLAN_STATUS_REVIEWED = 5
PLAN_STATUS_UNASSIGNED = 6

# 工单类型/创建类型：与 DkdContants.TASK_TYPE_SUPPLY / 现有建单调用保持一致
TASK_TYPE_SUPPLY = 2
CREATE_TYPE_SYSTEM = 1

# 状态机合法迁移：键=当前状态，值=允许迁移到的状态集合
ALLOWED_TRANSITIONS: dict[int, frozenset[int]] = {
    PLAN_STATUS_SUGGESTED: frozenset(
        {PLAN_STATUS_ADJUSTED, PLAN_STATUS_SKIPPED, PLAN_STATUS_ORDERED, PLAN_STATUS_UNASSIGNED}
    ),
    PLAN_STATUS_ADJUSTED: frozenset(
        {PLAN_STATUS_SKIPPED, PLAN_STATUS_ORDERED, PLAN_STATUS_UNASSIGNED}
    ),
    PLAN_STATUS_UNASSIGNED: frozenset({PLAN_STATUS_ADJUSTED, PLAN_STATUS_ORDERED}),
    PLAN_STATUS_ORDERED: frozenset({PLAN_STATUS_REVIEWED}),
    # 3-已跳过 → 1-建议：**仅**由显式「恢复建议」动作触发（原型 V2 的按钮），
    # 且必须带原因、只允许当天（跨日改写会污染 3-6 的复盘口径，故由服务层加时钟校验）。
    # 变更记录（2026-09-21，排期 1-7）：0-13 冻结稿原把 3 设为不可逆终态，
    # 与原型 V2 的「恢复建议」交互冲突；此处补齐该迁移，DDL 注释的变更记录同步见
    # docs/ddl/agent_tables.sql（追加说明）+ docs/dkd-agent-restock-state-schema.md。
    PLAN_STATUS_SKIPPED: frozenset({PLAN_STATUS_SUGGESTED}),
    PLAN_STATUS_REVIEWED: frozenset(),  # 终态（复盘后不可再改，否则结论无法固化）
}


def can_transition(current: int, target: int) -> bool:
    """状态机卡口：阻止「已建单/已复盘」被重复确认或回退（幂等的应用层防线）。"""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


class RestockItem(BaseModel):
    """单条货道补货建议。

    字段名对齐现有 `RestockSuggestionDto`（`dkd-manage/domain/dto/RestockSuggestionDto.java`），
    使前端可复用现有补货建议弹窗的渲染逻辑。
    """

    sku_id: int
    sku_name: str | None = None
    channel_id: int
    channel_code: str
    current_quantity: int = Field(ge=0, description="现库存 tb_inventory.current_stock")
    max_capacity: int = Field(gt=0, description="货道容量 tb_inventory.max_stock")
    suggested_quantity: int = Field(ge=0, description="建议补货量（最终写入工单明细）")
    after_restock_quantity: int = Field(ge=0, description="补货后预计库存")
    estimated_days: int = Field(default=0, ge=0, description="预计可支撑天数")
    priority: int = Field(default=1, ge=1, le=4, description="1低 2中 3高 4紧急")
    reason: str = Field(default="", max_length=500, description="建议依据（对齐原型「依据」字段）")

    @model_validator(mode="after")
    def _check_consistency(self) -> RestockItem:
        """范围夹取 + 关键防错（AGENTS §7.6：LLM 输出视为不可信输入）。

        最重要的两条：
        1. 建议量不得超过「容量 - 现库存」，否则补货后库存会溢出；
        2. `suggested_quantity` 必须是相对现库存的**增量**，
           绝不能把 `max_capacity`（货道容量）误填进来——
           tb_task_details.expect_capacity 在 dkd-app 侧会被**直接累加进库存**
           （VendingMachineServiceImpl，详见冻结稿 §4 的语义陷阱）。
        """
        room = self.max_capacity - self.current_quantity
        if room < 0:
            raise ValueError(f"现库存 {self.current_quantity} 超过容量 {self.max_capacity}")
        if self.suggested_quantity > room:
            raise ValueError(
                f"建议补货量 {self.suggested_quantity} 超出可补空间 {room}"
                f"（容量 {self.max_capacity} - 现库存 {self.current_quantity}）"
            )
        expected_after = self.current_quantity + self.suggested_quantity
        if self.after_restock_quantity != expected_after:
            raise ValueError(
                f"after_restock_quantity={self.after_restock_quantity} 与"
                f"现库存+建议量={expected_after} 不一致"
            )
        return self

    def to_task_detail_payload(self) -> dict[str, Any]:
        """映射为 Java `TaskDetailsDto`（建单回调的 details[] 元素）。

        ⚠️ 语义映射（务必按注释理解，不要按字段名字面猜）：
          - `expect_capacity` 在 `TaskDetailsDto` 里的注释是「货道容量」，
            但 dkd-app 侧把它当**补货数量**用（会累加进库存），实体注释也是「补货数量」。
            → 这里必须传 `suggested_quantity`，**绝不能传 max_capacity**。
          - `expect_capacity` 在 Java 侧是 Long，这里给 int 即可（JSON 数字）。
        """
        return {
            "channelCode": self.channel_code,
            "expectCapacity": self.suggested_quantity,  # = 补货数量，不是容量！
            "skuId": self.sku_id,
            "skuName": self.sku_name,
            "skuImage": None,  # 由 Java 侧或后续补充；当前 DTO 允许为空
        }


class RestockPlan(BaseModel):
    """**按设备整单**的补货计划。

    为什么按设备而不是按货道：`TaskServiceImpl.insertTaskDto` 的防重校验是
    「同设备 + 同工单类型 + 进行中」，按货道拆单必然撞「设备有未完成工单」而失败。
    """

    plan_date: str = Field(description="YYYY-MM-DD，对应 agent_restock_plan.plan_date")
    vm_id: int
    inner_code: str
    region_id: int | None = None
    node_id: int | None = None
    status: int = PLAN_STATUS_SUGGESTED
    items: list[RestockItem] = Field(default_factory=list)
    assignee_id: int | None = Field(default=None, description="接单人 tb_emp（必须同区域）")
    assignee_name: str | None = None
    unassigned_reason: str | None = Field(
        default=None, description="无可用接单人时的原因（status=6 待指派）"
    )

    @property
    def sku_count(self) -> int:
        return len(self.items)

    @property
    def total_quantity(self) -> int:
        return sum(i.suggested_quantity for i in self.items)

    def to_task_dto(self, *, assignor_id: int | None = None) -> dict[str, Any]:
        """映射为 Java `TaskDto`（`POST /agent/callback/task` 的请求体）。

        发送端见 `app/tools/task_tools.py`（1-3）。

        前置约束（缺失即不可建单，由调用方保证）：
          - `status` 必须已通过状态机卡口；
          - `assignee_id` 必须存在且与设备区域一致（否则 Java 侧抛
            「员工区域与设备区域不一致」）。
        """
        if self.assignee_id is None:
            raise ValueError("缺少接单人（assignee_id）：请先完成区域匹配，或置为待指派")
        return {
            "createType": CREATE_TYPE_SYSTEM,
            "innerCode": self.inner_code,
            "userId": self.assignee_id,
            "assignorId": assignor_id,
            "productTypeId": TASK_TYPE_SUPPLY,
            "desc": f"智能补货建议（{self.plan_date}，{self.sku_count} 个货道）",
            "details": [i.to_task_detail_payload() for i in self.items],
        }


class RestockState(TypedDict, total=False):
    """补货子图 state（**冻结版**，字段语义见各注释）。

    命名约定：与 `agent_restock_plan` / `RestockSuggestionDto` 保持一致的 snake_case；
    节点间传递**纯 dict**（不用 Pydantic 对象，理由见模块 docstring 冻结规则 1）。
    """

    # ---- 输入 ----
    plan_date: str  # YYYY-MM-DD；由 06:00 定时任务注入
    trigger_type: int  # 1-用户会话 2-定时任务 3-人工干预
    request_id: str

    # ---- 取数中间产物（只读工具）----
    candidate_vm_ids: list[int]  # 待分析设备
    inventory_rows: list[dict[str, Any]]  # tb_inventory 原始行（含 max_capacity 等）
    sales_baseline: dict[str, Any]  # vm_id(str) -> 7/14/30 天分位销量
    pending_restock_tasks: dict[str, Any]  # vm_id(str) -> 在途补货工单
    employee_directory: dict[str, Any]  # region_id(str) -> 可用运维员工

    # ---- 计算与生成结果 ----
    plans: list[dict[str, Any]]  # RestockPlan.model_dump() 列表
    calibration_notes: dict[str, Any]  # vm_id(str) -> LLM 校准结果（节假日/异常波动）

    # ---- 人工干预（interrupt 恢复后写入）----
    adjustments: dict[str, Any]  # vm_id(str) -> {suggested_quantity?, reason, predicted_window?}
    decisions: dict[str, Any]  # vm_id(str) -> "confirm" | "skip" | "adjust"
    reviewed_by: int | None

    # ---- 执行结果（**追加型**，用 operator.add 以免恢复时覆盖历史）----
    created_tasks: Annotated[list[dict[str, Any]], operator.add]
    failures: Annotated[list[dict[str, Any]], operator.add]

    # ---- 复盘 ----
    review_metrics: dict[str, Any]
    summary: dict[str, Any]


def empty_state(*, plan_date: str, trigger_type: int, request_id: str) -> RestockState:
    """构造初始 state（追加型字段必须显式给空列表，避免 reducer 报错）。"""
    return RestockState(
        plan_date=plan_date,
        trigger_type=trigger_type,
        request_id=request_id,
        candidate_vm_ids=[],
        inventory_rows=[],
        sales_baseline={},
        pending_restock_tasks={},
        employee_directory={},
        plans=[],
        calibration_notes={},
        adjustments={},
        decisions={},
        reviewed_by=None,
        created_tasks=[],
        failures=[],
        review_metrics={},
        summary={},
    )


# 冻结字段清单（供契约测试断言，禁止在未评审情况下增删）
FROZEN_FIELDS: frozenset[str] = frozenset(RestockState.__annotations__)
