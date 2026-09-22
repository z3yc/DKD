"""LLM 校准节点单测（排期任务 1-5）。

原则（AGENTS §8）：
- **不真实调用 LLM**：全部用假客户端（`_FakeModel`），断言的是“拿到不可信输出后我们怎么处理”；
- **拒绝路径优先**：越界系数、未知货道、非 JSON、超时——每一条都必须**降级而不是崩溃**，
  且降级后统计基线结果必须原样保留（补货链路的最坏形态是“可解释的规则增强版”）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.graphs import restock_calibration as rc
from app.graphs.restock_baseline import BaselineSuggestion
from app.graphs.restock_state import PLAN_STATUS_SUGGESTED, RestockItem, RestockPlan
from app.prompts import restock_calibration as prompt


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class _FakeModel:
    """最小假客户端：记录收到的 messages，返回预置内容。"""

    def __init__(
        self,
        content: str = "{}",
        *,
        error: Exception | None = None,
        tokens: tuple[int, int] = (120, 30),
    ) -> None:
        self.content = content
        self.error = error
        self.tokens = tokens
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls.append(messages)
        if self.error:
            raise self.error
        return AIMessage(
            content=self.content,
            usage_metadata={
                "input_tokens": self.tokens[0],
                "output_tokens": self.tokens[1],
                "total_tokens": sum(self.tokens),
            },
            response_metadata={"model_name": "deepseek-flash"},
        )


def _item(*, code: str = "1-1", qty: int = 8, current: int = 2, capacity: int = 10) -> RestockItem:
    return RestockItem(
        sku_id=1,
        sku_name="可口可乐 330ml",
        channel_id=4732,
        channel_code=code,
        current_quantity=current,
        max_capacity=capacity,
        suggested_quantity=qty,
        after_restock_quantity=current + qty,
        estimated_days=2,
        priority=3,
        reason="日均需求 3 件/天（7天 p75=3）；现库存 2，建议补 8。",
    )


def _plan(items: list[RestockItem] | None = None, *, vm_id: int = 80) -> RestockPlan:
    return RestockPlan(
        plan_date="2026-09-21",
        vm_id=vm_id,
        inner_code="A1000001",
        region_id=3,
        assignee_id=10,
        status=PLAN_STATUS_SUGGESTED,
        items=items or [_item()],
    )


def _suggestion(code: str = "1-1") -> BaselineSuggestion:
    return BaselineSuggestion(
        channel_id=4732,
        channel_code=code,
        sku_id=1,
        current_quantity=2,
        max_capacity=10,
        suggested_quantity=8,
        after_restock_quantity=10,
        daily_demand=3.0,
        estimated_days=0,
        priority=3,
        confidence="high",
    )


def _payload_from(model: _FakeModel) -> dict[str, Any]:
    """从假模型收到的 messages 里把 <data> 内的 JSON 抠出来（用于断言提示词内容）。"""
    human = model.calls[0][1][1]
    body = human[1] if isinstance(human, tuple) else human
    raw = str(body)
    inner = raw.split("<data>", 1)[1].split("</data>", 1)[0]
    return json.loads(inner)


# --------------------------------------------------------------------------------------
# 容错解析：模型输出格式不可信，解析失败必须能定位到具体哪一项
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"calibrations": []}',
        '```json\n{"calibrations": []}\n```',
        '好的，这是结果：\n{"calibrations": []}\n以上。',
        '说明：{"calibrations": [{"reason": "含 {花括号} 的字符串", "channelCode": "1-1"}]} 结束',
    ],
)
def test_extract_json_object_tolerates_wrappers_and_string_braces(text: str):
    parsed = rc.extract_json_object(text)
    assert parsed is not None, "容错解析不能因为围栏/寒暄/字符串里的花括号就失败"
    assert "calibrations" in parsed


@pytest.mark.parametrize("text", ["", "没有 JSON", '{"calibrations": [}', "[1, 2, 3]"])
def test_extract_json_object_returns_none_when_unparseable(text: str):
    assert rc.extract_json_object(text) is None


def test_parse_calibrations_clamps_and_drops_untrusted_entries():
    settings = _settings()
    text = json.dumps(
        {
            "calibrations": [
                {"channelCode": "1-1", "factor": 1.2, "reason": "节假日前客流上升"},
                {"channelCode": "9-9", "factor": 1.2, "reason": "编造的货道"},  # 未知货道
                {"channelCode": "1-1", "factor": 1.4, "reason": "重复项"},  # 重复（取第一条）
                {"channelCode": "1-2", "factor": 9.9, "reason": "离谱放大"},  # 越界 → 夹取
                {"channelCode": "1-3", "factor": "约1.2倍"},  # 非数字
                {"channelCode": "1-4", "factor": float("nan")},  # NaN
                {"channelCode": "1-5"},  # 缺 factor
                "不是对象",
            ],
            "notes": "整体客流平稳",
        }
    )
    entries, issues, notes = rc.parse_calibrations(
        text, known_channels=["1-1", "1-2", "1-3", "1-4", "1-5"], settings=settings
    )

    by_code = {e.channel_code: e for e in entries}
    assert set(by_code) == {"1-1", "1-2"}, "未知/重复/非法项必须被丢弃"
    assert by_code["1-1"].factor == pytest.approx(1.2), "重复货道取第一条（确定性）"
    assert by_code["1-2"].factor == pytest.approx(settings.restock_calibration_max_factor)
    assert by_code["1-2"].clamped is True
    assert notes == "整体客流平稳"
    # 丢弃必须留痕：否则“模型少给了一条”永远没人发现
    assert any("9-9" in i for i in issues)
    assert any("重复" in i for i in issues)
    assert any("夹取" in i for i in issues)
    assert any("不是数字" in i for i in issues)
    assert any("NaN" in i for i in issues)


def test_parse_calibrations_reports_missing_or_wrong_type_root():
    entries, issues, _ = rc.parse_calibrations("{}", known_channels=["1-1"], settings=_settings())
    assert entries == []
    assert any("calibrations" in i for i in issues)

    entries, issues, _ = rc.parse_calibrations(
        "没有 JSON", known_channels=["1-1"], settings=_settings()
    )
    assert entries == []
    assert any("JSON" in i for i in issues)


# --------------------------------------------------------------------------------------
# 夹取层：系数与容量两道口
# --------------------------------------------------------------------------------------


def test_apply_factor_keeps_quantity_within_capacity():
    item = _item(qty=8, current=2, capacity=10)  # 8 × 1.5 = 12 > 可补空间 8
    entry = rc.CalibrationEntry(channel_code="1-1", factor=1.5, reason="节前")
    adjusted = rc.apply_factor(item, entry)

    assert adjusted.suggested_quantity == 8, "LLM 说 ×1.5 也不能突破容量（否则 dkd-app 会溢出库存）"
    assert adjusted.after_restock_quantity == 10
    assert "LLM 校准 ×1.5" in adjusted.reason
    assert "节前" in adjusted.reason


def test_apply_factor_never_zeroes_a_needed_restock():
    item = _item(qty=1, current=0, capacity=10)
    entry = rc.CalibrationEntry(channel_code="1-1", factor=0.1, reason="模型想清零")  # 手写越界值
    adjusted = rc.apply_factor(item, entry)

    assert adjusted.suggested_quantity == 1, "必需的补货不能被模型一句话抹掉（至少保留 1 件）"


def test_apply_factor_truncates_reason_to_contract_limit():
    item = _item()
    item = item.model_copy(update={"reason": "依据" * 240})
    entry = rc.CalibrationEntry(channel_code="1-1", factor=1.2, reason="理由" * 40)
    adjusted = rc.apply_factor(item, entry)
    assert len(adjusted.reason) <= 500, "reason 是 RestockItem 的 max_length=500 契约"


# --------------------------------------------------------------------------------------
# 端到端（打桩）：成功路径 / 失败降级 / 开关关闭
# --------------------------------------------------------------------------------------


async def test_calibrate_plan_applies_factor_and_records_tokens():
    model = _FakeModel(
        json.dumps(
            {
                "calibrations": [
                    {"channelCode": "1-1", "factor": 1.2, "reason": "节假日前客流上升"}
                ],
                "notes": "国庆前整体上升",
            }
        )
    )
    outcome = await rc.calibrate_plan(
        _plan([_item(qty=8, current=2, capacity=20)]),
        suggestions={"1-1": _suggestion()},
        model=model,
        settings=_settings(),
    )

    assert outcome.applied is True
    item = outcome.items[0]
    assert item.suggested_quantity == 10, "8 × 1.2 = 9.6 → 10（容量 20 内）"
    assert item.after_restock_quantity == 12
    assert "LLM 校准" in item.reason and "节假日前客流上升" in item.reason
    assert (outcome.tokens_in, outcome.tokens_out) == (120, 30), "成本必须按实际用量记账"
    assert outcome.model == "deepseek-flash", "按服务端实际返回的模型名记账（不按配置猜）"
    assert outcome.notes == "国庆前整体上升"
    assert outcome.issues == []


async def test_calibrate_plan_prompt_declares_data_not_instruction_boundary():
    model = _FakeModel('{"calibrations": []}')
    await rc.calibrate_plan(
        _plan(), suggestions={"1-1": _suggestion()}, model=model, settings=_settings()
    )

    system_text = str(model.calls[0][0])
    human_text = str(model.calls[0][1][:2])
    assert "不是指令" in system_text and "不得执行" in system_text, "防注入声明必须在 system 里"
    assert "<data>" in human_text and "</data>" in human_text, "业务数据必须围栏隔离"
    assert "0.7" in system_text and "1.5" in system_text, "提示词的夹取区间必须与代码配置一致"

    payload = _payload_from(model)
    assert payload["items"][0]["channelCode"] == "1-1"
    assert payload["items"][0]["dailyDemand"] == 3.0
    # 数据最小化：不得把敏感字段塞进 prompt（AGENTS §7.8）
    assert set(payload).isdisjoint({"mobile", "phone", "assigneePhone", "amount"})


async def test_calibrate_plan_degrades_when_llm_fails():
    model = _FakeModel(error=TimeoutError("upstream timeout"))
    plan = _plan()
    outcome = await rc.calibrate_plan(
        plan, suggestions={"1-1": _suggestion()}, model=model, settings=_settings()
    )

    assert outcome.applied is False, "LLM 抖动不能让补货链路整体失败"
    assert outcome.error == "TimeoutError"
    assert [i.suggested_quantity for i in outcome.items] == [8], "统计基线结果必须原样保留"
    assert outcome.items[0].reason == plan.items[0].reason, "降级时依据不应被污染"
    assert any("降级" in i for i in outcome.issues)


async def test_calibrate_plan_degrades_on_malformed_output():
    model = _FakeModel("抱歉，我无法完成这个请求。")
    outcome = await rc.calibrate_plan(
        _plan(), suggestions={"1-1": _suggestion()}, model=model, settings=_settings()
    )

    assert outcome.applied is False
    assert any("JSON" in i for i in outcome.issues)
    assert outcome.items[0].suggested_quantity == 8


async def test_calibrate_plan_respects_disable_switch_without_calling_llm():
    model = _FakeModel('{"calibrations": [{"channelCode": "1-1", "factor": 1.5}]}')
    outcome = await rc.calibrate_plan(
        _plan(),
        suggestions={"1-1": _suggestion()},
        model=model,
        settings=_settings(DKD_AGENT_RESTOCK_CALIBRATION_ENABLED="0"),
    )

    assert outcome.applied is False
    assert model.calls == [], "开关关闭时不得调用 LLM（成本与可预测性）"
    assert any("开关已关闭" in i for i in outcome.issues)


# --------------------------------------------------------------------------------------
# LangGraph 节点契约：只写 plans 与 calibration_notes，不写 failures
# --------------------------------------------------------------------------------------


async def test_calibrate_state_node_writes_plans_and_notes_without_failures():
    model = _FakeModel(
        json.dumps({"calibrations": [{"channelCode": "1-1", "factor": 1.5, "reason": "促销期"}]})
    )
    wide = _item(qty=8, current=2, capacity=20)  # 留出放大空间，验证“系数真的生效”
    state = {
        "plans": [
            _plan([wide]).model_dump(mode="json"),
            _plan([wide], vm_id=81).model_dump(mode="json"),
        ]
    }

    result = await rc.calibrate_state_node(state, model=model)

    assert set(result) == {"plans", "calibration_notes", "calibration_tokens"}
    assert len(result["plans"]) == 2
    assert result["plans"][0]["items"][0]["suggested_quantity"] == 12  # 8 × 1.5
    assert result["calibration_notes"]["80"]["applied"] is True
    assert result["calibration_notes"]["81"]["entries"][0]["factor"] == 1.5
    assert result["calibration_tokens"] == {"in": 240, "out": 60}, "多设备 token 必须累加计量"
    assert "failures" not in result, "校准失败记在 notes.issues，不是业务失败（避免运营误判）"


def test_system_prompt_bounds_come_from_config_values():
    text = prompt.system_prompt(factor_min=0.8, factor_max=1.3)
    assert "0.8" in text and "1.3" in text
