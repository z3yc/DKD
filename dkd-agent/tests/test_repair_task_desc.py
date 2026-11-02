"""维修工单备注组装的单元测试（2-5）。

备注是运营在移动端**唯一**能看到的诊断上下文，所以这批用例锁三件事：
1. 该给的必须有：设备号、置信度与依据、证据条目（档位/类型/摘要）、关联订单号、平台动作；
2. 不该有的必须没有：**不得出现病因推断**（平台没有知识库，写进去就是编的）；
3. 体量有界：证据条数、单条摘要、整体长度都有上限（备注列不是报告）。
"""

from __future__ import annotations

from app.services.repair_task_desc import (
    MAX_DESC_CHARS,
    MAX_EVIDENCE_ITEMS,
    compose_repair_task_desc,
)


def _diagnosis() -> dict:
    return {
        "innerCode": "A1000001",
        "confidence": "high",
        "confidenceBasis": "存在 2 条订单异常",
        "evidence": [
            {
                "kind": "order_anomaly:paid_not_vended",
                "severity": "high",
                "summary": "支付成功但 160 分钟未出货",
                "detail": {"orderNo": "DKD202611020001"},
            },
            {
                "kind": "inventory_anomaly:deduct_without_order",
                "severity": "medium",
                "summary": "库存无单扣减",
                "detail": {"orderNo": "DKD202611020002"},
            },
        ],
        "recommendedActions": ["在「设备管理 → 工单管理」新建维修工单"],
    }


def test_desc_contains_confidence_evidence_and_orders() -> None:
    desc = compose_repair_task_desc(_diagnosis())
    assert "A1000001" in desc
    assert "置信度：high（存在 2 条订单异常）" in desc
    assert "order_anomaly:paid_not_vended" in desc
    assert "支付成功但 160 分钟未出货" in desc
    assert "关联订单：DKD202611020001、DKD202611020002" in desc
    assert "平台动作：在「设备管理 → 工单管理」新建维修工单" in desc


def test_desc_never_invents_cause() -> None:
    desc = compose_repair_task_desc(_diagnosis())
    assert "不含病因推断" in desc
    # 结论里**没有**病因字段，任何“可能原因/处置步骤”的措辞都不该出现
    for forbidden in ("可能原因", "故障原因：", "处置步骤："):
        assert forbidden not in desc


def test_desc_is_bounded_in_items_and_length() -> None:
    diagnosis = _diagnosis()
    diagnosis["evidence"] = [
        {
            "kind": f"order_anomaly:k{i}",
            "severity": "medium",
            "summary": "很长的证据摘要" * 30,
            "detail": {"orderNo": f"NO-{i}"},
        }
        for i in range(MAX_EVIDENCE_ITEMS + 5)
    ]
    desc = compose_repair_task_desc(diagnosis)
    assert len(desc) <= MAX_DESC_CHARS
    assert desc.count("- [medium]") == MAX_EVIDENCE_ITEMS
    assert "其余 5 条见决策审计页" in desc


def test_desc_tolerates_missing_fields() -> None:
    assert compose_repair_task_desc({}) == (
        "【智能诊断建议】\n说明：本结论不含病因推断（平台无结构化故障知识库），请现场核实后处置。"
    )
    # 证据没有 detail 时不虚构“关联订单”
    desc = compose_repair_task_desc({"innerCode": "A1", "evidence": [{"kind": "x"}]})
    assert "关联订单" not in desc
