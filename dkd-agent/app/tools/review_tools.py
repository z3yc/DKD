"""审核官只读工具（F-10 / M4）。

## 为什么单独一个模块，而不是塞进 read_tools

`read_tools` 是"诊断/派单"共用的取数层，本模块只服务**审核官（工单完成后复核）**：
- `get_task_detail`：按 `task_id` 读 `tb_task` + `tb_task_details`（两张表都在只读白名单内）；
- `check_repair_effective`：**纯函数**，复用 `list_order_anomalies` 已经取回的异常行，
  不新查库（计划 F-10 明确要求）。

独立成模块还有两个好处：
1. 审核官是**工单完成后的独立触发**（D8：页面点「复核」），与 M3 已验收的 `fault_graph`
   解耦——不需要改动已上线的诊断/派单图；
2. 工具边界清晰，将来接工具注册表（F-12）时按分组启用/禁用，不用在 read_tools 里加开关。

## 纪律

- 只读：本模块**没有任何写语句**；白名单校验、超时熔断、行数上限全部复用
  `read_tools._fetch_all`（唯一实现，不复制一份）；
- 时间过滤一律范围比较，禁止 `date_format()` 包列（AGENTS §10）；
- 查询都带 `limit`，且按主键/外键等值过滤，不做全表扫描。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.tools.read_tools import OrderAnomaly, ToolError, _fetch_all

# 工单类型/状态（与 DkdContants 一致：3=维修，4=完成）
TASK_TYPE_REPAIR = 3
TASK_STATUS_CANCEL = 3
TASK_STATUS_FINISH = 4


class RepairTaskItem(BaseModel):
    """补货/维修工单的一条明细（维修单通常没有明细，字段保持通用）。"""

    details_id: int
    channel_code: str | None = None
    expect_capacity: int | None = None
    sku_id: int | None = None
    sku_name: str | None = None
    sku_image: str | None = None


class RepairTaskDetail(BaseModel):
    """审核官需要的工单事实（`tb_task` + 明细）。

    为什么把状态/执行人/类型都带上：四分类结论全部依赖它们——
    工单未完成时不能判"修好"；执行人与派单提案不一致时才可能是"派错人"；
    非维修工单直接判"证据不足"（不能拿补货单复核维修效果）。
    """

    task_id: int
    task_code: str | None = None
    task_status: int
    product_type_id: int | None = None
    inner_code: str | None = None
    user_id: int | None = None
    user_name: str | None = None
    addr: str | None = None
    create_time: datetime | None = None
    update_time: datetime | None = None
    desc: str | None = None
    details: list[RepairTaskItem] = Field(default_factory=list)


class RepairEffectiveness(BaseModel):
    """修复效果判定（纯函数结果）。

    `status` 三态：
    - `fixed`：修复前有异常、修复后窗口内**没有**异常；
    - `not_fixed`：修复后窗口内仍有异常（必须带证据）；
    - `no_data`：缺少可比对的数据（两窗皆空 / 缺工单完成时间），不得下"修好"的结论。
    """

    status: str
    before_count: int
    after_count: int
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


async def get_task_detail(
    session: Any, *, task_id: int, limit: int | None = None
) -> RepairTaskDetail | None:
    """按 `task_id` 读工单主表 + 明细（只读）。

    @param session SQLAlchemy AsyncSession（单测注入假会话，不连库）
    @param task_id 工单主键（`tb_task.task_id`）
    @param limit 明细行数上限；缺省用 `read_row_limit`（避免异常工单把明细全量拉回）
    @return 工单详情；工单不存在返回 None（由上层判"证据不足"，不抛异常）
    @raises ToolError task_id 非法
    """
    if task_id is None or int(task_id) < 1:
        raise ToolError("taskId 必须是正整数")
    settings = get_settings()
    row_limit = limit if limit is not None else settings.read_row_limit

    task_rows = await _fetch_all(
        session,
        """
        select t.task_id, t.task_code, t.task_status, t.product_type_id,
               t.inner_code, t.user_id, t.user_name, t.addr,
               t.create_time, t.update_time, t.desc
          from tb_task t
         where t.task_id = :task_id
         limit 1
        """,
        {"task_id": int(task_id)},
        tables=("tb_task",),
    )
    if not task_rows:
        return None
    detail = RepairTaskDetail.model_validate(dict(task_rows[0]))

    item_rows = await _fetch_all(
        session,
        """
        select d.details_id, d.channel_code, d.expect_capacity,
               d.sku_id, d.sku_name, d.sku_image
          from tb_task_details d
         where d.task_id = :task_id
         order by d.details_id
         limit :limit
        """,
        {"task_id": int(task_id), "limit": int(row_limit)},
        tables=("tb_task_details",),
    )
    detail.details = [RepairTaskItem.model_validate(dict(row)) for row in item_rows]
    return detail


def check_repair_effective(
    *,
    repair_time: datetime | None,
    before_anomalies: list[OrderAnomaly],
    after_anomalies: list[OrderAnomaly],
) -> RepairEffectiveness:
    """判定维修是否生效（纯函数，不查库）。

    判定规则（必须能从证据反推，不接受"看起来好了"）：
    - 缺工单完成时间 → `no_data`（没有"修复时刻"就无法切窗口）；
    - 修复后窗口**仍有**异常 → `not_fixed`，异常行本身就是证据（订单号可点名）；
    - 修复前有异常、修复后没有 → `fixed`；
    - 两个窗口都没有异常 → `no_data`（设备本来就没单，不能证明修好了）。

    @param repair_time 工单完成时间（`tb_task.update_time` 或回调时间）
    @param before_anomalies 修复前窗口内的订单异常（由 `list_order_anomalies` 取回）
    @param after_anomalies 修复后窗口内的订单异常（同上）
    @return 修复效果（含证据行，最多 5 条用于展示，计数为全量）
    """
    before_count = len(before_anomalies)
    after_count = len(after_anomalies)
    if repair_time is None:
        return RepairEffectiveness(
            status="no_data",
            before_count=before_count,
            after_count=after_count,
            note="缺少工单完成时间，无法切分修复前后窗口",
        )
    if after_count > 0:
        evidence = [
            {
                "type": "order_anomaly",
                "ref": anomaly.order_id,
                "detail": f"{anomaly.order_no or anomaly.order_id}：{anomaly.reason}",
            }
            for anomaly in after_anomalies[:5]
        ]
        return RepairEffectiveness(
            status="not_fixed",
            before_count=before_count,
            after_count=after_count,
            evidence=evidence,
            note=f"修复后窗口仍有 {after_count} 条订单异常",
        )
    if before_count > 0:
        return RepairEffectiveness(
            status="fixed",
            before_count=before_count,
            after_count=after_count,
            evidence=[
                {
                    "type": "anomaly_cleared",
                    "ref": anomaly.order_id,
                    "detail": f"{anomaly.order_no or anomaly.order_id}：修复后未再出现",
                }
                for anomaly in before_anomalies[:5]
            ],
            note=f"修复前 {before_count} 条异常，修复后窗口内未再出现",
        )
    return RepairEffectiveness(
        status="no_data",
        before_count=0,
        after_count=0,
        note="修复前后窗口均无订单异常，无法证明维修生效（可能该设备本就没有销售）",
    )


__all__ = [
    "TASK_STATUS_CANCEL",
    "TASK_STATUS_FINISH",
    "TASK_TYPE_REPAIR",
    "RepairEffectiveness",
    "RepairTaskDetail",
    "RepairTaskItem",
    "check_repair_effective",
    "get_task_detail",
]
