"""F-10 审核官单测：只读工具 + 四分类结论。

原则（AGENTS §8）：
- **不连 MySQL**：`get_task_detail` 用假会话断言 SQL 形状与入参；
- **不调 LLM**：四分类是纯逻辑，不存在模型调用；
- 拒绝路径优先：非维修单/未完成/缺提案/无论据的"已验证"都必须被拒。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services.fault_review import (
    CATEGORY_EVIDENCE_INSUFFICIENT,
    CATEGORY_FIX_VERIFIED,
    CATEGORY_NOT_FIXED,
    CATEGORY_WRONG_DISPATCH,
    REVIEW_CATEGORIES,
    assess_repair,
)
from app.tools import review_tools
from app.tools.read_tools import OrderAnomaly, ToolError
from app.tools.review_tools import (
    RepairEffectiveness,
    RepairTaskDetail,
    RepairTaskItem,
    check_repair_effective,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    """记录 SQL 与参数；按调用序返回预置行（不连库）。"""

    def __init__(self, rows_by_call: list[list[dict[str, Any]]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows_by_call = rows_by_call or []

    async def execute(self, stmt: Any, params: dict[str, Any]) -> FakeResult:
        self.calls.append((str(stmt), dict(params)))
        index = len(self.calls) - 1
        rows = self._rows_by_call[index] if index < len(self._rows_by_call) else []
        return FakeResult(rows)

    @property
    def sql_text(self) -> str:
        return " ".join(sql for sql, _ in self.calls).lower()


def _task_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "task_id": 101,
        "task_code": "T20261028001",
        "task_status": 4,
        "product_type_id": 3,
        "inner_code": "A1000001",
        "user_id": 7,
        "user_name": "孙权",
        "addr": "北京",
        "create_time": datetime(2026, 10, 27, 10, 0, 0),
        "update_time": datetime(2026, 10, 28, 9, 0, 0),
        "desc": "维修：3 号货道反复卡货",
    }
    row.update(overrides)
    return row


def _detail_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "details_id": 1001,
        "channel_code": "3-1",
        "expect_capacity": 8,
        "sku_id": 2,
        "sku_name": "可乐",
        "sku_image": None,
    }
    row.update(overrides)
    return row


def _task(**overrides: Any) -> RepairTaskDetail:
    payload: dict[str, Any] = {
        "task_id": 101,
        "task_code": "T20261028001",
        "task_status": 4,
        "product_type_id": 3,
        "inner_code": "A1000001",
        "user_id": 7,
        "user_name": "孙权",
        "details": [RepairTaskItem(details_id=1001, channel_code="3-1", expect_capacity=8)],
    }
    payload.update(overrides)
    return RepairTaskDetail.model_validate(payload)


def _anomaly(order_id: int, order_no: str) -> OrderAnomaly:
    return OrderAnomaly(
        kind="vend_failed",
        order_id=order_id,
        order_no=order_no,
        status=3,
        reason="已扣款但出货失败",
    )


def _proposal(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "assigneeId": 7,
        "assigneeName": "孙权",
        "assigneeBasis": {"regionId": 1, "candidateCount": 2, "inflightCount": 1},
        "priority": 2,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------------------
# get_task_detail：SQL 形状 / 解析 / 拒绝路径
# --------------------------------------------------------------------------------------


async def test_get_task_detail_reads_task_and_details_with_limit():
    session = FakeSession(rows_by_call=[[_task_row()], [_detail_row()]])

    detail = await review_tools.get_task_detail(session, task_id=101)

    assert detail is not None
    assert detail.task_code == "T20261028001"
    assert detail.product_type_id == review_tools.TASK_TYPE_REPAIR
    assert [item.channel_code for item in detail.details] == ["3-1"]

    task_sql, task_params = session.calls[0]
    assert "from tb_task" in task_sql.lower()
    assert "limit 1" in task_sql.lower()
    assert "join" not in task_sql.lower(), "按主键读单表，不做跨表 join（避免 collation 坑）"
    assert task_params["task_id"] == 101

    detail_sql, detail_params = session.calls[1]
    assert "from tb_task_details" in detail_sql.lower()
    assert "limit :limit" in detail_sql.lower()
    assert detail_params["task_id"] == 101
    assert detail_params["limit"] > 0


async def test_get_task_detail_returns_none_when_task_missing():
    session = FakeSession(rows_by_call=[[]])

    assert await review_tools.get_task_detail(session, task_id=999) is None
    assert len(session.calls) == 1, "工单不存在时不再查明细（省一次查询）"


async def test_get_task_detail_rejects_invalid_task_id():
    session = FakeSession()
    with pytest.raises(ToolError, match="正整数"):
        await review_tools.get_task_detail(session, task_id=0)


async def test_get_task_detail_uses_range_friendly_query_only():
    session = FakeSession(rows_by_call=[[_task_row()], []])
    await review_tools.get_task_detail(session, task_id=101)
    assert "date_format(" not in session.sql_text, "禁止函数包裹索引列"


# --------------------------------------------------------------------------------------
# check_repair_effective：fixed / not_fixed / no_data
# --------------------------------------------------------------------------------------


def test_check_repair_effective_fixed_when_anomalies_disappear():
    result = check_repair_effective(
        repair_time=datetime(2026, 10, 28, 9, 0, 0),
        before_anomalies=[_anomaly(9001, "O9001")],
        after_anomalies=[],
    )

    assert result.status == "fixed"
    assert result.before_count == 1
    assert result.after_count == 0
    assert result.evidence[0]["type"] == "anomaly_cleared"


def test_check_repair_effective_not_fixed_names_anomaly_evidence():
    result = check_repair_effective(
        repair_time=datetime(2026, 10, 28, 9, 0, 0),
        before_anomalies=[_anomaly(9001, "O9001")],
        after_anomalies=[_anomaly(9002, "O9002")],
    )

    assert result.status == "not_fixed"
    assert result.evidence[0]["ref"] == 9002, "未修复必须点名具体订单（可回溯）"
    assert "O9002" in result.evidence[0]["detail"]


def test_check_repair_effective_no_data_when_both_windows_empty():
    result = check_repair_effective(
        repair_time=datetime(2026, 10, 28, 9, 0, 0),
        before_anomalies=[],
        after_anomalies=[],
    )

    assert result.status == "no_data"
    assert "无法证明" in result.note


def test_check_repair_effective_no_data_without_repair_time():
    result = check_repair_effective(
        repair_time=None,
        before_anomalies=[_anomaly(9001, "O9001")],
        after_anomalies=[],
    )

    assert result.status == "no_data"
    assert "完成时间" in result.note


# --------------------------------------------------------------------------------------
# assess_repair：四分类各一条（F-10 验收要求）
# --------------------------------------------------------------------------------------


def test_assess_repair_fix_verified():
    conclusion = assess_repair(
        task=_task(),
        proposal=_proposal(),
        effectiveness=RepairEffectiveness(
            status="fixed",
            before_count=1,
            after_count=0,
            evidence=[{"type": "anomaly_cleared", "ref": 9001, "detail": "O9001 修复后未再出现"}],
        ),
    )

    assert conclusion.category == CATEGORY_FIX_VERIFIED
    assert conclusion.evidence, "已验证也要带证据（工单 + 修复前异常）"
    assert conclusion.required_data == []
    assert conclusion.expected_assignee_id == 7
    assert conclusion.actual_assignee_id == 7


def test_assess_repair_not_fixed_must_name_evidence():
    conclusion = assess_repair(
        task=_task(),
        proposal=_proposal(),
        effectiveness=RepairEffectiveness(
            status="not_fixed",
            before_count=1,
            after_count=1,
            evidence=[{"type": "order_anomaly", "ref": 9002, "detail": "O9002：已扣款但出货失败"}],
        ),
    )

    assert conclusion.category == CATEGORY_NOT_FIXED
    assert conclusion.evidence[0].ref == 9002
    assert "9002" in conclusion.summary


def test_assess_repair_wrong_dispatch_names_basis():
    conclusion = assess_repair(
        task=_task(user_id=8, user_name="柯涵"),
        proposal=_proposal(),
        effectiveness=RepairEffectiveness(status="fixed", before_count=1, after_count=0),
    )

    assert conclusion.category == CATEGORY_WRONG_DISPATCH
    assert conclusion.expected_assignee_id == 7
    assert conclusion.actual_assignee_id == 8
    assert "assigneeBasis" in conclusion.evidence[0].detail
    assert "不一致" in conclusion.summary


def test_assess_repair_evidence_insufficient_when_task_not_finished():
    conclusion = assess_repair(
        task=_task(task_status=2),
        proposal=_proposal(),
        effectiveness=RepairEffectiveness(status="no_data", before_count=0, after_count=0),
    )

    assert conclusion.category == CATEGORY_EVIDENCE_INSUFFICIENT
    assert conclusion.required_data, "证据不足必须写清要补什么数据"
    assert "进行中" in conclusion.summary


def test_assess_repair_evidence_insufficient_when_proposal_missing():
    conclusion = assess_repair(
        task=_task(),
        proposal=None,
        effectiveness=RepairEffectiveness(status="fixed", before_count=1, after_count=0),
    )

    assert conclusion.category == CATEGORY_EVIDENCE_INSUFFICIENT
    assert any("派单提案" in item for item in conclusion.required_data)


def test_assess_repair_forbids_fix_verified_without_assignee_basis():
    conclusion = assess_repair(
        task=_task(),
        proposal={"assigneeId": 7},
        effectiveness=RepairEffectiveness(status="fixed", before_count=1, after_count=0),
    )

    assert conclusion.category == CATEGORY_EVIDENCE_INSUFFICIENT, "没有选人依据不得宣称已验证"


def test_assess_repair_evidence_insufficient_without_repair_window_data():
    conclusion = assess_repair(
        task=_task(),
        proposal=_proposal(),
        effectiveness=RepairEffectiveness(
            status="no_data", before_count=0, after_count=0, note="修复前后窗口均无订单异常"
        ),
    )

    assert conclusion.category == CATEGORY_EVIDENCE_INSUFFICIENT
    assert any("窗口" in item for item in conclusion.required_data)


def test_review_categories_are_exactly_four():
    assert {
        "fix_verified",
        "not_fixed",
        "wrong_dispatch",
        "evidence_insufficient",
    } == REVIEW_CATEGORIES
