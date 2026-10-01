"""诊断结论组装的单测（排期任务 2-4）。

## 这里守的是什么

2-4 的输出会被前端画成“置信度条”，所以它必须满足两条**可回归**的性质：

1. **置信度是证据的确定性函数**（不是模型感觉、也不是随机）：
   同类证据必须每次都得到同一档位，且 `confidenceBasis` 能说清是哪几条证据产生的。
2. **没有知识库就不产出病因**：本模块刻意**没有** `candidateCauses` 字段。
   这条也要用断言钉住——否则某天有人“顺手加一个更完整”的字段，
   前端就会开始展示编造的原因，而这正是 FIX-22 明确要避免的事。

这些用例全部是**构造样本**（纯函数，不连库），真库行为由
`docs/scripts/verify-assistant-diagnosis.py` 的 [1] 段覆盖。
"""

from __future__ import annotations

from datetime import datetime

from app.services.diagnosis import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    compose_diagnosis,
)
from app.tools.read_tools import (
    ChannelStockItem,
    HistoryCase,
    HistoryCaseSearch,
    InflightTask,
    InventoryAnomaly,
    MachineProfile,
    OrderAnomaly,
)


def _profile() -> MachineProfile:
    return MachineProfile(
        vm_id=80,
        inner_code="A1000001",
        addr="北京市海淀区五道口",
        vm_status=1,
        node_id=2,
        node_name="五道口点位",
        region_id=3,
        region_name="北京市东城区",
    )


def _order_anomaly(kind: str = "paid_not_vended", order_id: int = 901) -> OrderAnomaly:
    return OrderAnomaly(
        kind=kind,
        order_id=order_id,
        order_no=f"NO-{order_id}",
        inner_code="A1000001",
        channel_code="1-1",
        status=1 if kind == "paid_not_vended" else 3,
        pay_status=1,
        amount=500,
        create_time=datetime(2023, 9, 10, 8, 0),
        age_minutes=999,
        reason="订单卡在支付完成未出货",
    )


def _inventory_anomaly() -> InventoryAnomaly:
    return InventoryAnomaly(
        kind="deduct_without_order",
        log_id=77,
        vm_id=80,
        inner_code="A1000001",
        change_type=1,
        change_amount=-1,
        order_id=None,
        reason="无单扣减：销售类型扣了 1 件，但关联不到订单",
    )


def _history(with_usable: bool) -> HistoryCaseSearch:
    case = HistoryCase(
        task_id=527,
        task_code="202303240001",
        inner_code="A1000001",
        task_status=4,
        task_status_label="已完成",
        phenomenon="货道 1-1 付款后不出货" if with_usable else "111",
        phenomenon_usable=with_usable,
        matched_by="same_device",
        confidence="low" if not with_usable else "high",
        confidence_basis="测试样本",
        created_time=datetime(2023, 3, 24, 10, 41),
    )
    return HistoryCaseSearch(
        inner_code="A1000001",
        cases=[case],
        total_cases=1,
        usable_case_count=1 if with_usable else 0,
        no_evidence_hint=None if with_usable else "没有一条可用作故障现象",
    )


def _stock(consistent: bool = True) -> list[ChannelStockItem]:
    return [
        ChannelStockItem(
            vm_id=80,
            inner_code="A1000001",
            channel_id=4732,
            channel_code="1-1",
            inventory_sku_id=1,
            channel_sku_id=1,
            current_stock=0,
            min_stock=1,
            is_alert=1,
            channel_max_capacity=10,
            inventory_max_stock=10 if consistent else 6,
            last_supply_time=None,
            last_sell_time=None,
        )
    ]


# ------------------------------------------------------------------------------------
# 置信度必须由证据强度决定（可回归）
# ------------------------------------------------------------------------------------


def test_vend_failed_is_high_confidence_with_basis():
    """「已扣款但出货失败」→ high，且依据里写明是哪条证据。"""
    result = compose_diagnosis(profile=_profile(), order_anomalies=[_order_anomaly("vend_failed")])
    assert result.confidence == CONFIDENCE_HIGH
    assert "已扣款" in result.confidence_basis
    assert result.cannot_conclude is False
    kinds = {e.kind for e in result.evidence}
    assert "order_anomaly:vend_failed" in kinds
    assert any(e.severity == "high" for e in result.evidence), "已扣款未出货应为 high 严重度"


def test_single_paid_not_vended_is_medium_confidence():
    """单条卡单 → medium（单条可能是偶发或正在处理）。"""
    result = compose_diagnosis(
        profile=_profile(), order_anomalies=[_order_anomaly("paid_not_vended")]
    )
    assert result.confidence == CONFIDENCE_MEDIUM
    assert "单条" in result.confidence_basis


def test_two_anomalies_cross_validate_to_high_confidence():
    """多条独立异常互相印证 → high（这是“多条”而不是“更严重”）。"""
    result = compose_diagnosis(
        profile=_profile(),
        order_anomalies=[
            _order_anomaly("paid_not_vended", 901),
            _order_anomaly("paid_not_vended", 902),
        ],
    )
    assert result.confidence == CONFIDENCE_HIGH
    assert "2 条订单异常" in result.confidence_basis


def test_inventory_anomaly_alone_raises_confidence():
    result = compose_diagnosis(profile=_profile(), inventory_anomalies=[_inventory_anomaly()])
    assert result.confidence == CONFIDENCE_HIGH
    assert "库存异常" in result.confidence_basis
    assert result.cannot_conclude is False


def test_no_anomaly_is_none_and_explicitly_not_ok():
    """没有异常时：置信度 none，且**必须**明说“没检出 ≠ 没问题”。"""
    result = compose_diagnosis(profile=_profile())
    assert result.confidence == CONFIDENCE_NONE
    assert result.cannot_conclude is True
    assert "不代表设备正常" in result.confidence_basis
    assert any("没有采集到" in u for u in result.unknowns), "库存日志为空必须进未知项"


# ------------------------------------------------------------------------------------
# 未知项与“不编病因”的硬约束
# ------------------------------------------------------------------------------------


def test_history_without_usable_phenomenon_becomes_unknown():
    """历史维修单不可用时，必须进 `unknowns`（而不是被当成“历史同类故障”）。"""
    result = compose_diagnosis(profile=_profile(), history=_history(with_usable=False))
    assert any("无法判断历史上是否出现过同类故障" in u for u in result.unknowns)
    history_evidence = next(e for e in result.evidence if e.kind == "history_cases")
    assert "不能据此判断" in history_evidence.summary
    assert history_evidence.severity == "info", "历史线索不该被标成高严重度"


def test_usable_history_is_still_only_info_not_a_cause():
    """即使历史现象可用，也只是 info 证据——本模块不据此推断病因。"""
    result = compose_diagnosis(profile=_profile(), history=_history(with_usable=True))
    history_evidence = next(e for e in result.evidence if e.kind == "history_cases")
    assert "可作为故障现象使用的 1 条" in history_evidence.summary
    assert history_evidence.severity == "info"


def test_capacity_inconsistency_becomes_unknown():
    """FIX-5 的容量口径不一致必须进未知项（否则“缺口”数字会被当成事实）。"""
    result = compose_diagnosis(profile=_profile(), stock=_stock(consistent=False))
    assert any("max_capacity" in u for u in result.unknowns)
    assert result.device["stockConsistent"] is False


def test_conclusion_has_no_candidate_causes_field():
    """**红线**：结论里不得出现“病因/原因”类字段。没有知识库就没有病因（FIX-22）。

    为什么用反射断言：这个约束的价值全在“以后别人不会顺手加回来”；
    写进注释没用，写进断言才有用。
    """
    fields = set(type(compose_diagnosis(profile=_profile())).model_fields)
    forbidden = {"candidate_causes", "causes", "possible_causes", "root_cause", "diagnosis"}
    assert not (fields & forbidden), f"结论模型出现了病因类字段：{fields & forbidden}"
    assert "evidence" in fields and "unknowns" in fields and "recommended_actions" in fields


def test_actions_only_point_to_real_platform_entries():
    """动作必须只指向平台里真实存在的入口（不产出技术处置步骤）。"""
    result = compose_diagnosis(
        profile=_profile(),
        order_anomalies=[_order_anomaly("vend_failed"), _order_anomaly("paid_not_vended", 902)],
        inventory_anomalies=[_inventory_anomaly()],
        inflight=[
            InflightTask(
                task_id=577,
                task_code="202610010004",
                inner_code="A1000001",
                task_status=1,
                product_type_id=2,
                user_id=2,
                create_time=datetime(2026, 10, 1, 12, 3),
                channels=["1-1"],
            )
        ],
    )
    joined = " | ".join(result.recommended_actions)
    assert "工单管理" in joined, "必须给出人工建单入口"
    assert "退款" in joined, "已扣款未出货要提示核对退款"
    assert "在途工单" in joined
    assert all("重启" not in a and "拆机" not in a for a in result.recommended_actions), (
        "不得输出技术处置步骤（那需要知识库/现场判断，本模块没有依据）"
    )


def test_serialization_uses_camel_case_aliases():
    """出口 JSON 必须是 camelCase（前端契约），而代码里读 snake_case（ruff N815）。"""
    dumped = compose_diagnosis(profile=_profile(), order_anomalies=[_order_anomaly()]).model_dump(
        by_alias=True
    )
    assert "recommendedActions" in dumped and "recommended_actions" not in dumped
    assert "confidenceBasis" in dumped
    assert "cannotConclude" in dumped
    assert "dataFreshness" in dumped
    assert "innerCode" in dumped
