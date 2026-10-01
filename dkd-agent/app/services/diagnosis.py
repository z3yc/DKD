"""诊断结论的结构化组装（排期任务 2-4，**只基于证据**）。

## 这个模块要解决的问题

2-4 的验收标准是「输出结构化 JSON，前端可渲染置信度条」。做这件事有两个陷阱：

1. **“置信度”没有依据就会变成装饰**：如果置信度是模型凭感觉给的，它既不可复核也不可回归，
   前端画出来的进度条只是在给读者一种“很科学”的错觉。
2. **没有知识库就不该产出“病因”**：本平台的维修工单只有备注字段（FIX-22 实测：
   5 条维修单描述是 `111`/`后台工作人员取消`），**没有任何可检索的故障原因/处置知识**。
   在这种数据上给出“病因排序”，本质是编造领域知识——比“没有结论”危险得多。

所以本模块只做三件**可复核**的事：

- `evidence[]`：**每条都带来源表与口径**，能回库对账；
- `unknowns[]`：把“平台观测不到的东西”显式列出（并说明**为什么**不能下结论，而不是含糊留白）；
- `recommendedActions[]`：只指向**平台里真实存在的入口**（工单/退款/留痕），不做技术诊断；
- `confidence`：**由证据强度按固定规则算出**（见 `_score_confidence`），规则在本文件里，
  可单测可回归；`confidenceBasis` 说明它源自哪几条证据。


## 明确不做的事（写下来，避免以后有人“顺手补上”）

- **不产出 `candidateCauses`（病因排序）**：没有知识源。等 FIX-22 落地（维修工单有结构化的
  现象/原因/处置，或厂商故障码表）之后，再在这里按证据匹配案例；
  届时“置信度”才有资格表示“该病因有多可能”。
- **不产出处置步骤**：同理。当前只能指向平台入口。
- **不改任何数据**：本模块与它的调用方都是只读的（AGENTS §7.3 / §7.9）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.tools.read_tools import (
    ChannelStockItem,
    HistoryCaseSearch,
    InflightTask,
    InventoryAnomaly,
    MachineProfile,
    OrderAnomaly,
)

# 置信度档位（与前端进度条的映射：high=80% / medium=55% / low=30% / none=0%）
# ⚠️ 这里的百分比只是**展示刻度**，不是概率——所以必须让前端同时显示 `confidenceBasis`，
# 否则“55%”会被读成“有 55% 的概率是这个原因”（而我们根本没给原因）。
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

# 证据等级 → 严重度（订单异常里“已扣款但出货失败”比“卡单”更急：钱已经收了）
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_INFO = "info"


class DiagnosisEvidence(BaseModel):
    """一条**可复核**的证据（不是推断，也不是结论）。"""

    kind: str
    severity: str
    summary: str
    source: str
    detail: dict[str, Any] = Field(default_factory=dict)


class DiagnosisConclusion(BaseModel):
    """诊断结论（结构化）。缺少 `candidateCauses` 是**刻意的**，见模块 docstring。

    ⚠️ 字段名用 snake_case + `serialization_alias`：**代码里读 snake_case，出口 JSON 是 camelCase**
    （与前端/DDL 的既有约定一致）。为什么不直接写 camelCase 字段名：那样会触发 ruff 的 N815
    （类作用域混用命名风格），而关掉规则会让这条约束在别处也失效——用别名既满足两边，也不牺牲检查。
    出口务必 `model_dump(by_alias=True)`，否则前端拿到的是 snake_case。
    """

    inner_code: str = Field(serialization_alias="innerCode")
    device: dict[str, Any] = Field(default_factory=dict)
    window: dict[str, Any] = Field(default_factory=dict)
    evidence: list[DiagnosisEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(
        default_factory=list, serialization_alias="recommendedActions"
    )
    confidence: str = CONFIDENCE_NONE
    confidence_basis: str = Field(default="", serialization_alias="confidenceBasis")
    cannot_conclude: bool = Field(default=True, serialization_alias="cannotConclude")
    caliber: str = ""
    data_freshness: dict[str, str] = Field(
        default_factory=dict, serialization_alias="dataFreshness"
    )


def _order_evidence(anomalies: list[OrderAnomaly]) -> list[DiagnosisEvidence]:
    """订单异常 → 证据（一条异常一条证据，带订单号便于回库核对）。"""
    out: list[DiagnosisEvidence] = []
    for item in anomalies:
        # 「已扣款但出货失败」比「卡单」严重：前者钱已经收了，后者还可能只是没出货
        severity = SEVERITY_HIGH if item.kind == "vend_failed" else SEVERITY_MEDIUM
        out.append(
            DiagnosisEvidence(
                kind=f"order_anomaly:{item.kind}",
                severity=severity,
                summary=item.reason,
                source="tb_order（口径：status=1 超过阀值未出货；status=3 且 pay_status∈{1,2}）",
                detail={
                    "orderId": item.order_id,
                    "orderNo": item.order_no,
                    "channelCode": item.channel_code,
                    "status": item.status,
                    "payStatus": item.pay_status,
                    "ageMinutes": item.age_minutes,
                },
            )
        )
    return out


def _inventory_evidence(anomalies: list[InventoryAnomaly]) -> list[DiagnosisEvidence]:
    return [
        DiagnosisEvidence(
            kind=f"inventory_anomaly:{item.kind}",
            severity=SEVERITY_MEDIUM if item.kind == "deduct_without_order" else SEVERITY_INFO,
            summary=item.reason,
            source="tb_inventory_log（口径：无单扣减 / 前后库存不自洽 / 无说明扣减）",
            detail={
                "logId": item.log_id,
                "changeType": item.change_type,
                "changeAmount": item.change_amount,
                "orderId": item.order_id,
            },
        )
        for item in anomalies
    ]


def _history_evidence(history: HistoryCaseSearch | None) -> list[DiagnosisEvidence]:
    """历史维修单 → 证据。

    ⚠️ `phenomenon_usable=False` 的记录只能作为“这台机器修过”的**线索**，
    绝不能写成“历史同类故障”——本函数把这一点写进 `summary` 与 `severity`，
    而不是指望下游（模型）记得住。
    """
    if history is None or not history.cases:
        return []
    usable = history.usable_case_count
    return [
        DiagnosisEvidence(
            kind="history_cases",
            severity=SEVERITY_INFO,
            summary=(
                f"窗口内检索到 {history.total_cases} 条历史维修工单，"
                f"其中**可作为故障现象使用的 {usable} 条**"
                + (
                    ""
                    if usable
                    else "（即：不能据此判断历史上是不是同类故障，只能说明这台机器修过）"
                )
            ),
            source="tb_task（product_type_id=3 维修工单；规则库检索，非语义检索）",
            detail={
                "totalCases": history.total_cases,
                "usableCaseCount": usable,
                "matchedByCounts": history.matched_by_counts,
                "phenomena": [c.phenomenon for c in history.cases[:5]],
                "noEvidenceHint": history.no_evidence_hint,
            },
        )
    ]


def _inflight_evidence(inflight: list[InflightTask] | None) -> list[DiagnosisEvidence]:
    if not inflight:
        return []
    return [
        DiagnosisEvidence(
            kind="inflight_work_orders",
            severity=SEVERITY_INFO,
            summary=(
                f"该设备有 {len(inflight)} 张在途补货工单（状态 1-待接单 / 2-进行中）："
                "新建补货工单会被 Java 侧防重拒绝，接单人可能正在处理"
            ),
            source="tb_task + tb_task_details（product_type_id=2，状态 1/2）",
            detail={
                "taskIds": [t.task_id for t in inflight],
                "taskCodes": [t.task_code for t in inflight],
                "statuses": [t.task_status for t in inflight],
            },
        )
    ]


def _score_confidence(
    order_anomalies: list[OrderAnomaly], inventory_anomalies: list[InventoryAnomaly]
) -> tuple[str, str]:
    """**置信度 = 证据强度的确定性函数**（不是概率，也不含模型判断）。

    规则（改这里就等于改口径，必须同步单测）：

    | 条件 | 档位 | 理由 |
    | --- | --- | --- |
    | 有 `vend_failed`（已扣款未出货） | high | 钱已收货未出，最需立刻处置，且证据是订单状态本身 |
    | ≥2 条订单异常或 ≥1 条库存异常 | high | 多条独立异常互相印证 |
    | 恰好 1 条订单异常 | medium | 单条异常可能是偶发/正在处理 |
    | 0 条异常 | none | **平台观测范围内没发现问题 ≠ 设备正常**（见 `cannotConclude`） |
    """
    kinds = {a.kind for a in order_anomalies}
    severe = "vend_failed" in kinds
    if severe or len(order_anomalies) >= 2 or inventory_anomalies:
        why = []
        if severe:
            why.append("存在「已扣款但出货失败」的订单")
        if len(order_anomalies) >= 2:
            why.append(f"存在 {len(order_anomalies)} 条订单异常")
        if inventory_anomalies:
            why.append(f"存在 {len(inventory_anomalies)} 条库存异常")
        return CONFIDENCE_HIGH, "；".join(why) + "——这些是**订单/库存状态本身**的事实，不是推断"
    if len(order_anomalies) == 1:
        return CONFIDENCE_MEDIUM, "存在 1 条订单异常（单条，可能是偶发或正在处理中）"
    return CONFIDENCE_NONE, "平台可观测的范围内没有检出异常（这不代表设备正常）"


def compose_diagnosis(
    *,
    profile: MachineProfile,
    order_anomalies: list[OrderAnomaly] | None = None,
    inventory_anomalies: list[InventoryAnomaly] | None = None,
    history: HistoryCaseSearch | None = None,
    inflight: list[InflightTask] | None = None,
    stock: list[ChannelStockItem] | None = None,
    window: dict[str, Any] | None = None,
) -> DiagnosisConclusion:
    """把**已经取到的**事实组装成结构化结论（纯函数：不连库、不看时钟）。

    为什么强调纯函数：2-4 的输出要能被单测逐条断言（“哪几条证据 → 哪个置信度”），
    如果它自己去查库，测试就必须造一整套 DB 桩，口径也就无法回归。
    取数由调用方（工具层）负责——那里已经有白名单/分批/超时三道护栏。
    """
    orders = list(order_anomalies or [])
    inventory = list(inventory_anomalies or [])
    evidence: list[DiagnosisEvidence] = [
        *_order_evidence(orders),
        *_inventory_evidence(inventory),
        *_inflight_evidence(inflight),
        *_history_evidence(history),
    ]
    confidence, basis = _score_confidence(orders, inventory)

    unknowns: list[str] = []
    # 1) 库存异常维度是否真的“没问题”？本机 tb_inventory_log 是空表 → 只能说“没采集到”。
    if not inventory:
        unknowns.append(
            "库存变动异常：本平台该类日志（tb_inventory_log）**当前没有数据**，"
            "因此“未发现库存异常”只代表**没有采集到**，不代表库存没问题（见 FIX-22 / dataWarning）"
        )
    # 2) 历史案例是否可用（FIX-22）——把“没有历史依据”写进未知项，而不是省略
    if history is None or history.usable_case_count == 0:
        unknowns.append(
            "历史同类故障：维修工单目前只有备注字段，无可用的现象/原因/处置"
            "（实测大量为 `111`/流程话术），**无法判断历史上是否出现过同类故障**"
        )
    # 3) 设备自身状态与货道容量口径（FIX-5）
    inconsistent = [s for s in (stock or []) if not s.data_consistent]
    if inconsistent:
        unknowns.append(
            f"货道容量口径：{len(inconsistent)} 个货道的「库存表 max_stock」"
            "与「货道档案 max_capacity」不一致"
            "（FIX-5 待业务拍板），据此计算的“缺口/是否需要补货”可能偏大或偏小"
        )

    actions: list[str] = []
    if any(a.kind == "vend_failed" for a in orders):
        actions.append(
            "先核对「已扣款但出货失败」的订单是否已退款（订单号见 evidence.detail.orderNo）"
        )
    if any(a.kind == "paid_not_vended" for a in orders):
        actions.append("检查订单是否卡在支付完成未出货（可能需要现场确认出货机构/网络回传）")
    if inventory:
        actions.append("核对库存日志与实物库存（存在无单扣减或不自洽记录）")
    if inflight:
        actions.append("先处理/关闭在途工单，避免重复派单")
    actions.append("如需现场处置：在「设备管理 → 工单管理」新建维修工单（助手不能代建）")
    actions.append("处置结论与现场现象请回填到工单备注，便于将来形成可检索的故障案例库（FIX-22）")

    # 只有“能指出现象”的证据才算能下结论；仅历史/在途线索不算
    cannot = not (orders or inventory)

    return DiagnosisConclusion(
        inner_code=profile.inner_code,
        device={
            "vmId": profile.vm_id,
            "addr": profile.addr,
            "nodeName": profile.node_name,
            "regionName": profile.region_name,
            "vmStatus": profile.vm_status,
            "channelCount": len(stock or []),
            "stockConsistent": not inconsistent,
        },
        window=window or {},
        evidence=evidence,
        unknowns=unknowns,
        recommended_actions=actions,
        confidence=confidence,
        confidence_basis=basis,
        cannot_conclude=cannot,
        caliber=(
            "结论只由**平台已采集的数据**得出：订单异常（tb_order 状态）、"
            "库存异常（tb_inventory_log）、"
            "在途工单、历史维修单。**本平台没有结构化的故障原因/处置知识库**（FIX-22），"
            "因此本结论**不包含病因推断**，只包含证据、未知项与可执行的平台动作；"
            "`confidence` 由证据强度按固定规则算出，不是概率。"
        ),
        data_freshness={
            "orderAnomalies": f"{len(orders)} 条",
            "inventoryAnomalies": f"{len(inventory)} 条（该类日志可能未采集）",
            "historyCases": (
                f"{history.total_cases if history else 0} 条"
                f"（可用 {history.usable_case_count if history else 0} 条）"
            ),
            "inflightTasks": f"{len(inflight or [])} 条",
            "stockChannels": f"{len(stock or [])} 个货道",
        },
    )


def _now_iso() -> str:
    """便于工具层在 `window` 里标注“这次查的是哪个时刻”的口径（不参与组装逻辑）。"""
    return datetime.now().isoformat(timespec="seconds")


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_NONE",
    "DiagnosisConclusion",
    "DiagnosisEvidence",
    "compose_diagnosis",
]
