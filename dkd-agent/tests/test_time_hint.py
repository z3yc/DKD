"""时间窗口前置规则的单测（排期 FIX-28）。

为什么这些用例值钱：规则的**反面**（该不该猜）与正面同样重要——
“最近”“以前”必须解析不出来（返回 None 让工具用默认值），
一句话里两个窗口必须**拒绝覆盖**（谁优先没有正确答案）。
一条误判就会把用户问的 7 天查成 5 年（或反之），而且没有任何报错。
"""

from __future__ import annotations

import pytest

from app.services.time_hint import (
    SOURCE_MODEL,
    SOURCE_USER_HINT,
    current_window_hint_days,
    parse_window_days,
    reset_window_hint_days,
    resolve_window_days,
    set_window_hint_days,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 本项目真实用例：模型跟不住的那个“近五年”
        ("那它近五年有哪些异常？", 1825),
        ("A1000001 近五年有异常吗？", 1825),
        ("近 7 天卖得怎么样？", 7),
        ("A1000001 近 7 天卖得怎么样？", 7),
        ("近三个月有哪些异常", 90),
        ("近 30 天", 30),
        ("昨天有异常吗", 1),
        ("上周这批机器怎么样", 7),
        ("去年修过几次", 365),
        ("近几年有没有类似问题", 1095),
        ("一年半以前的单子", 547),
        ("半年内的维修记录", 182),
        ("近十年", 3650),
        ("近三十年", 3650),  # 超上限夹到 3650
    ],
)
def test_parse_window_days_recognizes(text: str, expected: int) -> None:
    assert parse_window_days(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "五道口那台机器不出货",  # 有中文数字但没有时间单位 → 不猜
        "这台设备以前遇到过同样的问题吗？",  # “以前”含糊 → 不猜
        "最近的补货计划怎么样",  # “最近”含糊 → 不猜
        "A1000001",  # 只有编号
        "帮我建个维修工单",
        "近五年有哪些异常？再看看最近 7 天",  # 两个不同窗口 → 歧义不猜
        "10 月 4 日那天有异常吗",  # 300 天与 4 天两个候选 → 歧义不猜
        "",
    ],
)
def test_parse_window_days_refuses_to_guess(text: str) -> None:
    assert parse_window_days(text) is None


def test_device_code_digits_are_not_a_window() -> None:
    """设备编号里的数字不得被当成天数（否则每句话都会莫名覆盖窗口）。"""
    assert parse_window_days("A1000001 的库存") is None
    assert parse_window_days("看看 20211004 这台") is None


def test_resolve_window_days_prefers_user_and_records_both_values() -> None:
    """用户说的窗口优先，且**两个值都留下**（模型请求值不许被静默替换掉）。"""
    assert resolve_window_days(90, maximum=3650) == (90, SOURCE_MODEL, None)

    token = set_window_hint_days(1825)
    try:
        assert resolve_window_days(90, maximum=3650) == (1825, SOURCE_USER_HINT, 90)
        # 与用户一致时不算“覆盖”（审计里不该出现一堆无意义的 overridden 字段）
        assert resolve_window_days(1825, maximum=3650) == (1825, SOURCE_USER_HINT, None)
        # 各工具自己的上限仍然生效（历史检索上限 2000）
        assert resolve_window_days(90, maximum=2000) == (1825, SOURCE_USER_HINT, 90)
        token2 = set_window_hint_days(3650)
        try:
            assert resolve_window_days(90, maximum=2000) == (2000, SOURCE_USER_HINT, 90)
        finally:
            reset_window_hint_days(token2)
    finally:
        reset_window_hint_days(token)

    assert current_window_hint_days() is None, "复位后不得残留（否则会污染后续请求）"


def test_window_hint_is_none_by_default() -> None:
    """默认必须是没有窗口：拿不到用户时间词时，工具就该用默认窗口。"""
    assert current_window_hint_days() is None
    assert resolve_window_days(90, maximum=3650).source == SOURCE_MODEL


def test_window_resolution_payload_fields() -> None:
    """回写进工具返回值的字段名（前端/验收脚本靠它做确定性判定）。"""
    assert resolve_window_days(90, maximum=3650).as_payload_fields() == {"windowSource": "model"}
    token = set_window_hint_days(1825)
    try:
        fields = resolve_window_days(90, maximum=3650).as_payload_fields()
        assert fields == {"windowSource": "userTimeHint", "windowOverriddenFromDays": 90}
        assert resolve_window_days(90, maximum=3650).as_payload_fields(prefix="case") == {
            "caseWindowSource": "userTimeHint",
            "caseWindowOverriddenFromDays": 90,
        }
    finally:
        reset_window_hint_days(token)
