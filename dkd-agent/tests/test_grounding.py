"""口径帧与数字出处核对的单测（排期 FIX-27）。

这组用例的重点是**误报与漏报的边界**：
- 误报（把对的说成错的）会让提示条变成噪声，最终没人看 → 时间单位必须折算、年份不折算、
  元数据键不参与核对；
- 漏报（把错的漏过去）正是实测踩到的坑 → 「工具查了 90 天、回复写 1825 天」必须被抓到。
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage

from app.services import grounding
from app.services.grounding import (
    EvidenceIndex,
    build_evidence,
    check_reply_numbers,
    collect_calibers,
)


def _tool(name: str, payload: dict) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False), name=name, tool_call_id="c1"
    )


def test_collect_calibers_reads_window_and_counts_from_tool_results() -> None:
    """口径必须来自**工具算的**字段，而不是模型措辞。"""
    msgs = [
        _tool(
            "list_device_anomalies",
            {
                "innerCode": "A1000001",
                "window": {
                    "days": 90,
                    "start": "2026-07-03T00:00:00",
                    "end": "2026-10-01T00:00:00",
                },
                "windowSource": "model",
                "orderAnomalyCount": 0,
                "inventoryAnomalyCount": 0,
                "orderAnomalies": [],
                "caliber": "订单异常 = …",
                "hint": "窗口内没有检测到异常。",
            },
        )
    ]
    calibers = collect_calibers(msgs)
    assert len(calibers) == 1
    entry = calibers[0]
    assert entry["tool"] == "list_device_anomalies"
    assert entry["windowDays"] == 90
    assert entry["windowStart"].startswith("2026-07-03")
    assert entry["counts"] == {"orderAnomalyCount": 0, "inventoryAnomalyCount": 0}


def test_collect_calibers_is_defensive() -> None:
    """非 JSON / 既无窗口又无计数的返回不进帧（帧体要小，且不能是“空口径”）。"""
    assert collect_calibers([]) == []
    assert collect_calibers([ToolMessage(content="不是 JSON", name="x", tool_call_id="c")]) == []
    assert collect_calibers([_tool("find_devices", {"query": "五道口"})]) == []
    # 有计数才算口径（find_devices 的 matchCount 是“候选几台”，也是有用的口径）
    assert collect_calibers([_tool("find_devices", {"matchCount": 8})])[0]["counts"] == {
        "matchCount": 8
    }
    # 布尔不是计数（True 会被 json 当成 1 的坑）
    assert collect_calibers([_tool("find_devices", {"matchCount": True, "xCount": 3})])[0][
        "counts"
    ] == {"xCount": 3}


def test_build_evidence_covers_tools_args_and_user_message() -> None:
    """出处三源：工具返回值 + 工具参数 + 用户原话（用户自己写的数字也是出处）。"""
    tool_msg = _tool(
        "list_device_anomalies",
        {"window": {"days": 90, "start": "2026-07-03"}, "orderAnomalyCount": 0},
    )
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_device_anomalies", "args": {"inner_code": "A1000001"}, "id": "c"}
        ],
    )
    evidence = build_evidence([ai, tool_msg], user_message="近 7 天有异常吗")
    # 数字归一化（`07` → `7`）是故意的：模型写「07 条」也要能对上
    assert {"90", "2026", "7", "3", "0"} <= evidence.numbers
    assert "2026-07-03" in evidence.dates


def test_meta_keys_do_not_launder_wrong_numbers() -> None:
    """**关键用例**：`windowOverriddenFromDays`（被覆盖掉的错值）不得算作出处。

    否则正好会把“模型把 90 天说成 5 年”这一类错误洗白——工具返回里明明写着 90，
    回复里写 1825 却“有出处”。
    """
    tool_msg = _tool(
        "list_device_anomalies",
        {
            "window": {"days": 1825, "start": "2021-10-02"},
            "windowSource": "userTimeHint",
            "windowOverriddenFromDays": 90,
            "windowClampedFromDays": 5000,
        },
    )
    evidence = build_evidence([tool_msg])
    assert "1825" in evidence.numbers
    assert "90" not in evidence.numbers
    assert "5000" not in evidence.numbers


def test_grounding_catches_the_recorded_failure() -> None:
    """实测那个失败：工具用 90 天窗口，回复却写「近五年（1825 天）」+ 一个错的起始日期。"""
    tool_msg = _tool(
        "list_device_anomalies",
        {
            "innerCode": "A1000001",
            "window": {"days": 90, "start": "2021-10-02", "end": "2026-10-01"},
            "orderAnomalyCount": 0,
        },
    )
    evidence = build_evidence([tool_msg], user_message="那它近五年有哪些异常？")
    report = check_reply_numbers("近五年（1825 天，2021-10-04 起）共 0 条异常。", evidence=evidence)
    assert not report.grounded
    assert "1825 天" in report.ungrounded
    assert "2021-10-04" in report.ungrounded
    assert "2021-10-02" not in report.ungrounded


def test_grounding_accepts_duration_phrasings_and_calendar_years() -> None:
    """避免误报：`近 5 年`（=1825）与 `2023 年`（日历年，不是 738395 天）都必须算有出处。"""
    tool_msg = _tool(
        "search_device_history",
        {"window": {"days": 1825, "start": "2021-10-02"}, "totalCases": 5},
    )
    evidence = build_evidence([tool_msg], user_message="近五年修过几次")
    assert check_reply_numbers("近 5 年共 5 条维修单。", evidence=evidence).grounded
    assert check_reply_numbers("窗口从 2021-10-02 开始。", evidence=evidence).grounded

    year_evidence = build_evidence(
        [_tool("search_device_history", {"window": {"days": 1825, "start": "2023-03-01"}})]
    )
    assert check_reply_numbers("本机维修单集中在 2023 年。", evidence=year_evidence).grounded


def test_grounding_skips_chinese_numerals_and_ordinals() -> None:
    """中文数字**故意不核对**（宁可漏报不可误报）；序数「第 1 条」也不是数量声明。"""
    evidence = EvidenceIndex(numbers=frozenset({"0"}), dates=frozenset())
    report = check_reply_numbers("五年内没有异常，第 1 条建议是继续观察。", evidence=evidence)
    assert report.grounded and report.checked == 0


def test_grounding_reports_and_bounds() -> None:
    """无出处的数字要能列出来，且有上限（提示条给人扫读，不刷屏）。"""
    report = check_reply_numbers(
        "有 3 条、7 台、11 个、13 单、17 笔、19 元、23 条、29 台、31 个、37 条共 10 类。",
        evidence=EvidenceIndex(numbers=frozenset(), dates=frozenset()),
    )
    assert not report.grounded
    assert report.checked == 10
    assert len(report.ungrounded) <= grounding._MAX_UNGROUNDED
    frame = grounding.as_frame(report)
    assert frame["grounded"] is False and frame["checked"] == 10


def test_grounding_is_quiet_when_nothing_is_claimed() -> None:
    """没有数字声明时不得报警（否则每条寒暄都要带个提示条）。"""
    report = check_reply_numbers(
        "好的，我明白了。", evidence=EvidenceIndex(frozenset(), frozenset())
    )
    assert report == (True, [], 0)
    assert grounding.as_frame(report) == {"grounded": True, "ungroundedNumbers": [], "checked": 0}


def test_truncation_wording_is_not_a_data_claim() -> None:
    """**实测误报**：工具返回 `orderAnomaliesTruncatedFrom=10` 时模型会写“明细只列出了前 5 条”，
    其中的 5 是工具自己的条数上限（MAX_ANOMALY_ITEMS），不是“查到的数据”。

    为什么必须排掉：误报会让提示条变噪声，而噪声的后果是**真错也没人看**。
    对照：同样是“前”，但数字来自库（`前 10 条` → 10 在返回值里）时本来就不会被标。
    """
    evidence = build_evidence(
        [
            _tool(
                "list_device_anomalies",
                {
                    "window": {"days": 1825, "start": "2021-10-03"},
                    "orderAnomalyCount": 10,
                    "orderAnomaliesTruncatedFrom": 10,
                },
            )
        ]
    )
    report = check_reply_numbers("明细只列出了前 5 条（共 10 条）。", evidence=evidence)
    assert "5 条" not in report.ungrounded
    assert "10 条" not in report.ungrounded
    assert report.checked == 1, "只有“共 10 条”算数据声明"

    # 近似值仍要核（“约 1800 天”没有出处 → 标出来）
    strict = check_reply_numbers("约 1800 天没有异常。", evidence=evidence)
    assert "1800 天" in strict.ungrounded


def test_evidence_spans_the_conversation_but_not_assistant_prose() -> None:
    """**实测误报**：模型引用上一轮查到的数字（“和上一轮 90 天窗口不同，90 天内 0 条”）——
    这必须有出处，否则提示条会稳定地冤枉一次正确的回答。

    同时锁住反面：助手**自己之前说过的话**不能当出处——否则说错一次就自我洗白（循环论证），
    而“模型改写过数字”正是要防的事。
    """
    turn1 = _tool(
        "list_device_anomalies",
        {"window": {"days": 90, "start": "2026-07-04"}, "orderAnomalyCount": 0},
    )
    turn2 = _tool(
        "list_device_anomalies",
        {"window": {"days": 1825, "start": "2021-10-03"}, "orderAnomalyCount": 10},
    )
    earlier_assistant = AIMessage(content="上一轮我查到 777 条。")
    messages = [earlier_assistant, turn1, turn2]

    evidence = build_evidence(messages, user_message="那它近五年有哪些异常？")
    report = check_reply_numbers(
        "近 1825 天有 10 条；和上一轮 90 天窗口不同（90 天内 0 条）。", evidence=evidence
    )
    assert report.grounded, f"应当有出处，却标出了 {report.ungrounded}"

    laundering = check_reply_numbers("上一轮说过 777 条。", evidence=evidence)
    assert "777 条" in laundering.ungrounded, "助手自己的旧话不得当作出处"


def test_grounding_frame_shape_and_bounds_are_stable() -> None:
    """帧字段名与上限稳定（前端/验收脚本都按它读）。"""
    report = check_reply_numbers("0 条。", evidence=EvidenceIndex(frozenset(), frozenset()))
    frame = grounding.as_frame(report)
    assert set(frame) == {"grounded", "ungroundedNumbers", "checked"}
