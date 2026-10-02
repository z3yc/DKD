"""用户话语里的时间窗口 → 工具参数（排期 FIX-28）。

## 为什么需要这层确定性规则

实测（2026-10-01，真库 + 真 LLM）：用户说「那它近五年有哪些异常？」，
模型调用 `list_device_anomalies` **没有传 `days`**，工具用默认 90 天 → 返回 0 条；
模型接着把这个 0 条说成「近五年 0 条」（FIX-27 的变形）。
也就是说：**取数错了，措辞把它包装成了一个看起来正常的结论**。
只在提示词里写“注意用户提到的时间范围”不稳（实测多次失效），所以做成前置规则。

规则的位置在本轮**进入图之前**（`app/api/chat.py`）：从用户原话解析出“他说的窗口是多少天”，
用 ContextVar 交给工具层（与 `logging_conf.current_request_id` 同一机制）。
工具一旦拿到用户的明确窗口就以它为准，并把**模型原本请求的值**一起写进返回值：

    "window": {"days": 1825, ...}, "windowSource": "userTimeHint",
    "windowOverriddenFromDays": 90

两个值都留痕——既不改写“模型说了什么”，也不让一个错误的窗口静默生效；
审计与验收都能看到“实际生效的是哪个窗口、它来自谁”。

## 刻意不做的事（每一条都是为了防止“猜”）

- **不猜含糊表述**：「最近」「一直以来」「历史上有异常吗」都给不出天数 → 返回 `None`，工具用默认值。
- **一句话里出现两个不同窗口就不覆盖**（「近五年有哪些，再看看最近 7 天」）：
  谁优先没有正确答案，宁可退回默认值让模型自己选，也不凭猜覆盖。
- **不解析模型的话术**：与 2-3 的焦点规则同一原则，只认**用户原话**。
- **不跨轮沿用**：窗口是“这次提问的一部分”，上一轮的大窗口不该污染下一轮
  （需要跨轮记住的是**设备焦点**，见 `chat_graph.resolve_focus`）。
- **不覆盖 `get_sales_summary`**（上限本来就是 30 天，见 `assistant_tools`）：
  那是“最近卖得怎么样”的短窗工具，套 5 年没有意义；扩大覆盖面对调用方是隐式破坏。
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import NamedTuple

# 与工具层的窗口上限一致（10 年）
MAX_HINT_DAYS = 3650
_DAYS_PER_YEAR = 365
_DAYS_PER_MONTH = 30
_DAYS_PER_WEEK = 7
_HALF_YEAR_DAYS = _DAYS_PER_YEAR // 2

# 来源标记（进工具返回值 → 进 prompt/留痕，用于回答“这个窗口是谁定的”）
SOURCE_MODEL = "model"
SOURCE_USER_HINT = "userTimeHint"

_CN_DIGITS: dict[str, int] = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

# 数字既可以是阿拉伯数字，也可以是中文数字（「近五年」是本项目的真实用例）
_NUM = r"(?:\d{1,4}|[零一二两三四五六七八九十]{1,3})"
_QUANTITY_RE = re.compile(rf"(?P<n>{_NUM})\s*(?P<u>年|个?月|个?星期|周|礼拜|天|日)")
_YEAR_HALF_RE = re.compile(rf"(?P<n>{_NUM})\s*年半")

# 没有数字、但意思明确的表述（“半年”“上周”“去年”）。含糊的（最近/一直以来/历史上）**刻意不收**。
_RELATIVE_DAYS: dict[str, int] = {
    "昨天": 1,
    "前日": 1,
    "前一天": 1,
    "上周": _DAYS_PER_WEEK,
    "本周": _DAYS_PER_WEEK,
    "这周": _DAYS_PER_WEEK,
    "近一周": _DAYS_PER_WEEK,
    "一周": _DAYS_PER_WEEK,
    "一星期": _DAYS_PER_WEEK,
    "这星期": _DAYS_PER_WEEK,
    "上个月": _DAYS_PER_MONTH,
    "这个月": _DAYS_PER_MONTH,
    "本月": _DAYS_PER_MONTH,
    "上月": _DAYS_PER_MONTH,
    "近一个月": _DAYS_PER_MONTH,
    "一个月": _DAYS_PER_MONTH,
    "去年": _DAYS_PER_YEAR,
    "上一年": _DAYS_PER_YEAR,
    "前一年": _DAYS_PER_YEAR,
    "近几年": 3 * _DAYS_PER_YEAR,
    "近年来": 3 * _DAYS_PER_YEAR,
    "半年": _HALF_YEAR_DAYS,
}
# 长的优先（否则「近一周」会被「一周」抢先匹配掉，尽管结果相同，规则要可读）
_RELATIVE_RE = re.compile("|".join(sorted(_RELATIVE_DAYS, key=len, reverse=True)))

_window_hint_days: ContextVar[int | None] = ContextVar("dkd_window_hint_days", default=None)


class WindowResolution(NamedTuple):
    """一次窗口判定的结果（谁定、最终是多少、是否覆盖了模型给的值）。"""

    days: int
    source: str
    overridden_from: int | None

    def as_payload_fields(self, *, prefix: str = "") -> dict[str, object]:
        """转成工具返回值里的字段（`windowSource` / `windowOverriddenFromDays`）。

        为什么要回写进工具返回值：模型靠它知道“生效的不是我传的那个窗口”，
        审计靠它回答“这个结论是按哪个窗口查的”，验收靠它做**确定性判定**
        （模型传了什么不算数，实际生效的窗口才算数）。

        @param prefix 多窗口工具用它在前面加区分词（`case` → `caseWindowSource`）
        """
        source_key = f"{prefix}WindowSource" if prefix else "windowSource"
        override_key = f"{prefix}WindowOverriddenFromDays" if prefix else "windowOverriddenFromDays"
        fields: dict[str, object] = {source_key: self.source}
        if self.overridden_from is not None:
            fields[override_key] = self.overridden_from
        return fields


def _cn_to_int(text: str) -> int | None:
    """数字 → 整数（阿拉伯数字直读；中文数字支持 一 ~ 九十九：「五年」「十二个月」「二十天」）。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        if tail and tail not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    return _CN_DIGITS.get(text)


def _unit_days(unit: str) -> int:
    if unit.endswith("年"):
        return _DAYS_PER_YEAR
    if unit.endswith("月"):
        return _DAYS_PER_MONTH
    if unit.endswith(("星期", "周", "礼拜")):
        return _DAYS_PER_WEEK
    return 1  # 天 / 日


def _clamp(days: int, maximum: int) -> int:
    if days <= 0:
        return 1
    return min(days, maximum)


def parse_window_days(text: str) -> int | None:
    """从用户原话解析出窗口天数；**解析不出或有歧义时返回 None**（不猜）。

    口径（改这里就等于改行为，必须同步单测）：
      · `N 年 / N 个月 / N 周 / N 天`（含中文数字）→ 按 365 / 30 / 7 / 1 折算；
      · `N 年半` → N*365 + 182；`半年` → 182；
      · `昨天`=1、`上周`=7、`上个月`=30、`去年`=365、`近几年`=1095（3 年）；
      · 结果夹到 `[1, MAX_HINT_DAYS]`；
      · 出现**两个不同**的窗口 → None（歧义不猜）。
    """
    if not text:
        return None
    found: set[int] = set()

    def _record(days: int | None) -> str:
        # 匹配到的片段从待扫描文本里删掉，避免后面的规则重复命中同一段
        if days:
            found.add(_clamp(days, MAX_HINT_DAYS))
        return " "

    remainder = _YEAR_HALF_RE.sub(
        lambda m: _record((_cn_to_int(m.group("n")) or 0) * _DAYS_PER_YEAR + _HALF_YEAR_DAYS), text
    )
    remainder = _RELATIVE_RE.sub(lambda m: _record(_RELATIVE_DAYS.get(m.group(0), 0)), remainder)
    remainder = _QUANTITY_RE.sub(
        lambda m: _record((_cn_to_int(m.group("n")) or 0) * _unit_days(m.group("u"))), remainder
    )
    if len(found) != 1:
        return None
    return next(iter(found))


def set_window_hint_days(days: int | None) -> object:
    """本轮请求的窗口（进图之前调用；返回 token 供 `reset_window_hint_days`）。"""
    return _window_hint_days.set(days)


def reset_window_hint_days(token: object) -> None:
    """请求结束复位（异常路径也必须复位，否则会污染同进程的后续请求）。

    ⚠️ `ContextVar.reset` 传的是 `set` 返回的 Token；这里用 `object` 是为了不把
    `contextvars.Token` 泛型细节漏给调用方（ruff 的 ANN 规则要求标注）。
    """
    _window_hint_days.reset(token)  # type: ignore[arg-type]


def current_window_hint_days() -> int | None:
    """当前请求的窗口（工具层与提示词层都读它）。"""
    return _window_hint_days.get()


def resolve_window_days(requested: int, *, maximum: int) -> WindowResolution:
    """工具层统一入口：模型请求的窗口 vs 用户说的窗口。

    @param requested 模型请求的天数（工具签名里的默认值也算“模型请求”，因为模型没传参）
    @param maximum   该工具自己的上限（异常 3650 / 历史 2000，各工具不同）
    @return (`days` 实际生效, `source` 来源, `overridden_from` 被覆盖的模型值或 None)
    """
    model_days = _clamp(int(requested), maximum)
    hint = current_window_hint_days()
    if hint is None:
        return WindowResolution(days=model_days, source=SOURCE_MODEL, overridden_from=None)
    effective = _clamp(hint, maximum)
    return WindowResolution(
        days=effective,
        source=SOURCE_USER_HINT,
        # 相等时不算“覆盖”（否则审计会出现大量无意义的 overridden 字段）
        overridden_from=None if effective == model_days else model_days,
    )


__all__ = [
    "MAX_HINT_DAYS",
    "SOURCE_MODEL",
    "SOURCE_USER_HINT",
    "WindowResolution",
    "current_window_hint_days",
    "parse_window_days",
    "reset_window_hint_days",
    "resolve_window_days",
    "set_window_hint_days",
]
