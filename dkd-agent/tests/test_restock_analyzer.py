"""定时分析执行器单测（排期任务 1-9 / FIX-10）。

验收标准原文：**① 手动触发成功；② 连续失败告警到运维（Java 侧 1-9 的 AgentTask）；③ 任务重入安全。**

本文件覆盖 Python 侧能独立证明的部分：
- 暂停开关**真的被消费**（FIX-10 说的“开关目前只是状态位”在这里闭环）：暂停时不建图、不读业务库；
- 结果与会话 ID 契约（`threadId = restock-<plan_date>`，Java 侧与 1-6 脚本共用同一约定）；
- 失败如何**如实上报**（502 + 留痕 `result=2` + 原因原文，而不是抛一团栈给调用方）；
- 重入安全（同一日期重复触发的幂等由真实图证明，见
  `tests/test_restock_graph.py::test_repeat_invoke_same_thread_reupserts_without_error`）。

全部用假图/假开关/假留痕写入器：不连 MySQL、不调 LLM（AGENTS §8）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.audit import RESULT_FAIL, RESULT_OK, TRIGGER_SCHEDULE
from app.config import Settings
from app.graphs.restock_plan_store import PAUSE_SCOPE_GLOBAL, PauseState
from app.services.restock_analyzer import (
    STATUS_ANALYZED,
    STATUS_SKIPPED,
    TARGET_TYPE_RUN,
    AnalyzeOutcome,
    RestockAnalyzeError,
    RestockAnalyzer,
)

PLAN_DATE = date(2026, 9, 21)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        # conftest 全局把留痕写库关掉（防止单测误连 MySQL）；本文件都用**假**写入器，
        # 因此显式打开开关，“关闭时不调用写入器”的拒绝路径由专门用例覆盖。
        "DKD_AGENT_AUDIT_ENABLED": "1",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class FakePauseStore:
    """内存开关（与 1-7 的 FakePauseStore 同契约：`get()` 返回 PauseState）。"""

    def __init__(self, *, paused: bool = False, reason: str | None = None) -> None:
        self.state = PauseState(paused=paused, reason=reason, paused_by="7" if paused else None)

    async def get(self) -> PauseState:
        return self.state


class FakeAuditWriter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    async def __call__(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)


class FakeGraph:
    """假补货图：只回放给定结果，并记录被调用次数（验证“暂停时不建图”）。"""

    def __init__(
        self, *, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result or {}
        self.error = error
        self.invocations: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.invocations.append((state, config))
        if self.error is not None:
            raise self.error
        return self.result


def _analyzer(
    *,
    graph: FakeGraph | None = None,
    paused: bool = False,
    reason: str | None = None,
    audit_writer: FakeAuditWriter | None = None,
    settings: Settings | None = None,
    created: list[FakeGraph] | None = None,
) -> tuple[RestockAnalyzer, FakeGraph, FakeAuditWriter]:
    effective_graph = graph or FakeGraph()
    if created is not None:
        created.append(effective_graph)
    recorder = audit_writer or FakeAuditWriter()
    analyzer = RestockAnalyzer(
        graph_factory=lambda: effective_graph,
        pause_store=FakePauseStore(paused=paused, reason=reason),
        settings=settings or _settings(),
        audit_writer=recorder,
        today_provider=lambda: PLAN_DATE,
    )
    return analyzer, effective_graph, recorder


# --------------------------------------------------------------------------------------
# 暂停开关（FIX-10 的消费方）
# --------------------------------------------------------------------------------------


async def test_paused_run_skips_graph_and_reports_reason():
    analyzer, graph, recorder = _analyzer(paused=True, reason="盘点一周")

    outcome = await analyzer.analyze()

    assert outcome.status == STATUS_SKIPPED
    assert outcome.reason == "盘点一周" and outcome.paused_by == "7"
    assert outcome.plan_date == "2026-09-21" and outcome.thread_id == "restock-2026-09-21"
    assert graph.invocations == [], "暂停时绝不能跑图（否则“暂停”只是装饰）"

    entry = recorder.calls[-1]
    assert entry["action"] == "restock.analyze"
    assert entry["trigger_type"] == TRIGGER_SCHEDULE == 2
    assert entry["target_type"] == "restock_pause" and entry["target_id"] == PAUSE_SCOPE_GLOBAL
    assert entry["result"] == RESULT_OK and entry["error_msg"] is None
    assert entry["input_context"]["effect"] == "skipped_paused"


async def test_explicit_plan_date_overrides_today_provider():
    analyzer, graph, _ = _analyzer()
    graph.result = {"plans": [], "summary": {}}

    outcome = await analyzer.analyze(plan_date=date(2026, 10, 1))

    assert outcome.plan_date == "2026-10-01" and outcome.thread_id == "restock-2026-10-01"


# --------------------------------------------------------------------------------------
# 正常运行
# --------------------------------------------------------------------------------------


def _plans_payload() -> dict[str, Any]:
    """图返回的 plans **必须逐字按生产形状**构造（F-7 的教训）。

    旧固件写的是 `{"total_quantity": 12, "items": [...]}`——这个键**生产中根本不存在**：
    冻结 state schema 里 `plans` 是 `RestockPlan.model_dump()` 列表，而 `total_quantity`
    是 Pydantic **@property**，不会出现在 dump 里。于是固件造出了一个假形状、单测全绿，
    而线上 `totalQuantity` 恒报 0（库里有 20 件）。

    本固件现在照抄真实 dump 的字段名（见 `RestockPlan` / `RestockItem` 的 snake_case 字段），
    并额外加一条**防漂移断言**：只要有人再往固件里塞属性名而非字段名，`test_plans_payload_matches_real_dump`
    会当场失败。
    """
    return {
        "plans": [
            {
                "vm_id": 80,
                "inner_code": "A1000001",
                "items": [
                    {"channel_code": "1-1", "suggested_quantity": 7},
                    {"channel_code": "1-2", "suggested_quantity": 5},
                ],
            },
            {
                "vm_id": 86,
                "inner_code": "aim5xu4I",
                "items": [{"channel_code": "2-1", "suggested_quantity": 5}],
            },
        ],
        "calibration_notes": {"80": {"factors": []}},
        "__interrupt__": ("interrupt-payload",),
    }


def test_plans_payload_matches_real_dump():
    """固件里的 plan 键必须与 `RestockPlan.model_dump()` 的字段集合一致（防再次造出假形状）。

    这条断言专治 F-7 那类“固件比生产更宽松”的假绿：手写 dict 永远能自洽，
    只有跟真实模型对账才能发现“这个键生产里没有”。
    """
    from app.graphs.restock_state import RestockPlan

    field_names = set(RestockPlan.model_fields.keys())
    for plan in _plans_payload()["plans"]:
        unknown = set(plan.keys()) - field_names
        assert not unknown, (
            f"固件里出现了 RestockPlan 没有的字段 {unknown}（例如属性 total_quantity）——"
            "这会让单测通过而生产静默报 0"
        )
    assert "total_quantity" not in field_names, (
        "total_quantity 是 @property 而不是字段：读它必须逐货道累加 suggested_quantity"
    )


async def test_analyzed_run_reports_counts_and_audits():
    analyzer, graph, recorder = _analyzer()
    graph.result = _plans_payload()

    outcome = await analyzer.analyze()

    assert outcome.status == STATUS_ANALYZED
    assert (outcome.device_count, outcome.channel_count, outcome.total_quantity) == (2, 3, 17)
    assert outcome.interrupted is True
    # 图必须带上本次触发身份：trigger_type=2（定时任务），thread 与 1-6 脚本同约定
    state, config = graph.invocations[-1]
    assert state["plan_date"] == "2026-09-21" and state["trigger_type"] == TRIGGER_SCHEDULE
    assert config["configurable"]["thread_id"] == "restock-2026-09-21"

    entry = recorder.calls[-1]
    assert entry["target_type"] == TARGET_TYPE_RUN and entry["target_id"] == "2026-09-21"
    assert entry["result"] == RESULT_OK
    ctx = entry["input_context"]
    assert ctx["effect"] == "analyzed" and ctx["deviceCount"] == 2 and ctx["channelCount"] == 3
    assert ctx["totalQuantity"] == 17 and ctx["interrupted"] is True and ctx["calibrated"] is True
    assert entry["user_id"] is None, "定时任务没有操作人，不得伪造一个用户"


async def test_analyzed_outcome_serializes_camel_case():
    """对外 JSON 键必须是 camelCase（Java 侧按 `data.deviceCount` 读）。"""
    payload = AnalyzeOutcome(
        status=STATUS_ANALYZED,
        plan_date="2026-09-21",
        thread_id="restock-2026-09-21",
        device_count=3,
    ).model_dump(mode="json", by_alias=True)
    assert payload["planDate"] == "2026-09-21" and payload["deviceCount"] == 3
    assert "plan_date" not in payload and "device_count" not in payload


# --------------------------------------------------------------------------------------
# 失败：如实上报 + 留痕
# --------------------------------------------------------------------------------------


async def test_graph_failure_maps_to_502_and_audits_reason():
    analyzer, _graph, recorder = _analyzer(graph=FakeGraph(error=TimeoutError("取数超时")))

    with pytest.raises(RestockAnalyzeError) as excinfo:
        await analyzer.analyze()

    assert excinfo.value.status_code == 502, "Python 自身故障要给 Java 一个可重试的信号（≠4xx）"
    assert "TimeoutError" in str(excinfo.value)

    entry = recorder.calls[-1]
    assert entry["result"] == RESULT_FAIL == 2
    assert entry["input_context"]["effect"] == "failed"
    assert "TimeoutError" in entry["error_msg"], "失败原因要能回答「06:00 那次为什么没跑成」"


async def test_long_failure_message_is_truncated_to_column_limit():
    """`agent_decision_log.error_msg` 是 VARCHAR(500)：超长必须在写入前截断。"""
    analyzer, _graph, recorder = _analyzer(graph=FakeGraph(error=RuntimeError("x" * 900)))

    with pytest.raises(RestockAnalyzeError):
        await analyzer.analyze()

    assert len(recorder.calls[-1]["error_msg"]) <= 500


async def test_audit_failure_does_not_hide_the_business_error():
    """留痕坏了也必须把真实失败抛出去（审计旁路不得吞掉业务异常，AGENTS §6.1）。"""
    analyzer, _graph, _recorder = _analyzer(
        graph=FakeGraph(error=RuntimeError("落库失败")),
        audit_writer=FakeAuditWriter(error=ValueError("留痕参数缺陷")),
    )

    with pytest.raises(RestockAnalyzeError):
        await analyzer.analyze()


async def test_audit_disabled_does_not_call_writer():
    analyzer, graph, recorder = _analyzer(
        settings=_settings(DKD_AGENT_AUDIT_ENABLED="0"), reason="x"
    )
    graph.result = _plans_payload()

    await analyzer.analyze()

    assert recorder.calls == []
