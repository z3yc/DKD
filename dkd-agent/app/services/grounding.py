"""工具结果的「口径帧」与回复数字的**出处核对**（FIX-27 的检测面）。

## 这个模块解决什么问题

实测（2026-10-01，真库 + 真 LLM）：工具用 90 天窗口查到 0 条异常，
模型却在回复里写成「近五年（1825 天，2021-10-04 起）0 条」——**用户会据此得出错误结论**。
审计里虽然记了「调了哪些工具」，但用户看不到；而“模型会不会写照实”是不可控的。

所以这里做两件**确定性**的事（都不解析模型意图，只做机械核对）：

1. `collect_calibers()` —— 从本轮的工具返回值里抽出**权威口径**：
   哪个工具、窗口多少天、窗口起止、各类计数是多少。它随 `done` 帧发给前端并写进留痕，
   于是「这个回答是按哪个窗口、查到了几条」有一个**不依赖模型措辞**的答案；
2. `check_reply_numbers()` —— 把回复里出现的数字与**已有出处**逐个核对，把**没有出处的数字**
   挑出来（`ungrounded`）。它不是“判模型说谎”，而是“把无法溯源的数字标出来”，
   让前端提示“以口径帧为准”。

   已有出处的范围（**会话级，不是单轮**）：
   - ✅ 工具返回值与工具参数（本轮与之前各轮都算：回答里引用“上一轮 90 天窗口内 0 条”是正当的）；
   - ✅ 用户原话里的数字（用户自己写的“近 7 天”被如实引用不算编造）；
   - ❌ **助手自己之前说过的话**——否则模型说错一次，那个错数字就成了“有出处”，
     下一轮再引用就被洗白了（循环论证）。

## 为什么用“数字必须有出处”而不是“解析模型的结论”

后者要理解语义（“五年都没异常”算不算错？），既容易误判也没法回归。
前者是机械的：**本轮里没有出现过 1825 这个数，回复里却写了 1825，那就标出来**——
这正是实测那个失败的可判定形态。宁可漏报（模型用中文数字写“五年”时不生效），不可误报
（误报会让提示条变成噪声，最终没人看）。

## 已知的取舍（写下来，避免以后当成 bug 修）

- **只认阿拉伯数字**：回复里写“五年”不做折算。这是**故意**的——中文数字是用户的说法，
  折算后再核对会引入一整类“看起来像错、其实没错”的误报。
- **时间单位会折算成天再核对**（`5 年` → 1825、`3 个月` → 90、`2 周` → 14），
  否则模型如实写“近 5 年”会被误判成“1825 不是出处”。
- **元数据的键不参与核对**（`windowOverriddenFromDays` / `clampedFromDays` / `mustSay` /
  `caliber` / `hint` 等）：它们是给模型看的说明文字，不是“查到的数据”。
  尤其 `windowOverriddenFromDays` 是**被覆盖掉的那个错值**，若算作出处，
  正好会把“模型把 90 天说成 5 年”这类错误洗白。
- **不判“对错”，只判“有无出处”**：核对结果只用于提示与留痕，**不修改模型的原话**
  （改写用户看到的内容比说错更糟：用户会以为模型没说错）。
"""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

from langchain_core.messages import ToolMessage

# 口径帧最多带几条（一页卡片就够看，帧越大越容易在网关链路上被拖慢）
_MAX_CALIBERS = 8
# 未出处数字最多列几个（提示条是给人扫读的）
_MAX_UNGROUNDED = 8
# 计数类字段的识别规则（工具返回值里 `xxxCount` 与这两个例外）
_COUNT_SUFFIX = "Count"
_COUNT_EXTRA = ("totalCases", "usableCaseCount")

# 不参与数字核对的元数据键（说明性文字 / 被覆盖掉的错值）
_META_KEYS = frozenset(
    {
        "mustSay",
        "hint",
        "caliber",
        "dataWarning",
        "noEvidenceHint",
        "windowSource",
        "windowOverriddenFromDays",
        "windowClampedFromDays",
        "caseWindowSource",
        "caseWindowOverriddenFromDays",
        "matchReason",
    }
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 回复里的“数量/时间”声明：数字 + 单位（单位表即口径表，改这里等于改判定）
_UNIT_DAYS: dict[str, int] = {"年": 365, "个月": 30, "月": 30, "周": 7, "星期": 7, "天": 1, "日": 1}
_CLAIM_RE = re.compile(
    r"(?P<ord>第\s*)?(?P<num>\d{1,6}(?:\.\d+)?)\s*"
    r"(?P<unit>年|个月|月|周|星期|天|日|条|台|个|单|笔|元)"
)
# 数字声明的**限定词**：`前 5 条` 这类是“截断说明”而不是“数据声明”，不参与核对。
# 为什么必须排除（2026-10-02 实测）：工具返回 `orderAnomaliesTruncatedFrom=10` 时，
# 模型会写“明细只列出了前 5 条”（5 = MAX_ANOMALY_ITEMS，**是工具自己的上限**）——
# 把它当“无出处的数字”标出来就是误报，而误报会让提示条变成噪声、最终没人看。
# 只排“前”：`约/大约`（近似值）与`其余`仍要核——它们是在断言一个数，只是不精确。
_RANGE_PREFIXES = ("前",)


def _is_range_prefix(reply: str, start: int) -> bool:
    """紧邻数字左侧的限定词（允许中间有空格）。"""
    index = start - 1
    while index >= 0 and reply[index] in " \t\u3000":
        index -= 1
    return index >= 0 and reply[index] in _RANGE_PREFIXES


class EvidenceIndex(NamedTuple):
    """本轮**已有出处**的数字与日期（工具返回值 + 工具参数 + 用户原话）。"""

    numbers: frozenset[str]
    dates: frozenset[str]


class GroundingReport(NamedTuple):
    """核对结果（`grounded=False` 时前端提示“以口径帧为准”）。"""

    grounded: bool
    ungrounded: list[str]
    checked: int


def _norm(token: str) -> str:
    """数字归一化：`0` / `0.0` / `00` 视为同一个值。"""
    try:
        value = float(token)
    except (TypeError, ValueError):
        return token
    return str(int(value)) if value.is_integer() else str(value)


def _parse_payload(content: Any) -> dict[str, Any] | None:
    text = content if isinstance(content, str) else str(content)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _drop_meta(node: Any) -> Any:
    """递归剔除元数据键（它们是说明文字，不是“查到的数据”）。"""
    if isinstance(node, dict):
        return {k: _drop_meta(v) for k, v in node.items() if k not in _META_KEYS}
    if isinstance(node, list):
        return [_drop_meta(item) for item in node]
    return node


def _collect(text: str, numbers: set[str], dates: set[str]) -> None:
    numbers.update(_norm(token) for token in _NUMBER_RE.findall(text))
    dates.update(_DATE_RE.findall(text))


def collect_calibers(messages: object) -> list[dict[str, Any]]:
    """本轮每个工具调用的**权威口径**（窗口 + 计数），供 done 帧、留痕与验收判定使用。

    只保留**最后一次为限的 N 条**：一页卡片够看，也避免帧体无限增长。
    """
    calibers: list[dict[str, Any]] = []
    for msg in list(messages or []):  # type: ignore[arg-type]
        if not isinstance(msg, ToolMessage):
            continue
        payload = _parse_payload(getattr(msg, "content", ""))
        if payload is None:
            continue
        window = payload.get("window")
        window = window if isinstance(window, dict) else {}
        counts = {
            key: value
            for key, value in payload.items()
            if isinstance(value, int)
            and not isinstance(value, bool)
            and (key.endswith(_COUNT_SUFFIX) or key in _COUNT_EXTRA)
        }
        entry: dict[str, Any] = {
            "tool": str(getattr(msg, "name", "") or ""),
            "windowDays": window.get("days"),
            "windowStart": window.get("start"),
            "windowEnd": window.get("end"),
            "windowSource": payload.get("windowSource"),
            "counts": counts,
        }
        if not entry["windowDays"] and not counts:
            # 既没有窗口也没有计数 → 不是“口径”，不进帧（例如 `find_devices` 之外的纯提示返回）
            continue
        calibers.append(entry)
    return calibers[-_MAX_CALIBERS:]


def build_evidence(messages: object, *, user_message: str = "") -> EvidenceIndex:
    """收集数字/日期的出处（用户原话 + 工具参数 + 工具返回值）。

    @param messages 建议传**整个会话**的消息（引用前几轮的查询结果是正当的）；
                    函数**只读 ToolMessage 的内容与各消息的 tool_calls**，
                    不读助手正文——避免把上一轮自己说错的数字当出处（循环论证）。
    @param user_message 用户原话（用户自己写的数字也是出处）
    """
    numbers: set[str] = set()
    dates: set[str] = set()
    _collect(user_message or "", numbers, dates)
    for msg in list(messages or []):  # type: ignore[arg-type]
        if isinstance(msg, ToolMessage):
            payload = _parse_payload(getattr(msg, "content", ""))
            text = (
                json.dumps(_drop_meta(payload), ensure_ascii=False)
                if payload is not None
                # 不是 JSON（工具回了一段中文/报错）：整段当出处，避免把回显的说明当“编造”
                else str(getattr(msg, "content", ""))
            )
            _collect(text, numbers, dates)
        for call in getattr(msg, "tool_calls", None) or []:
            args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
            _collect(
                json.dumps(args if args is not None else {}, ensure_ascii=False), numbers, dates
            )
    return EvidenceIndex(frozenset(numbers), frozenset(dates))


def _candidates(raw: str, unit: str) -> set[str]:
    """一个声明可能对应的出处值（`5 年` 既可能对应 `5`，也可能对应折算后的 `1825`）。"""
    out = {_norm(raw)}
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return out
    if unit == "年" and 1900 <= value <= 2100:
        # 「2023 年」是**年份**不是时长，不折算（否则会算出一个荒谬的 738395 天）
        return out
    factor = _UNIT_DAYS.get(unit)
    if factor:
        out.add(_norm(str(value * factor)))
    return out


def check_reply_numbers(reply: str, *, evidence: EvidenceIndex) -> GroundingReport:
    """挑出回复里**没有出处**的数字（宁可漏报，不可误报，见模块 docstring）。"""
    if not reply:
        return GroundingReport(grounded=True, ungrounded=[], checked=0)
    ungrounded: list[str] = []
    checked = 0
    seen: set[str] = set()
    for match in _CLAIM_RE.finditer(reply):
        if match.group("ord"):
            # 序数（「第 3 条」）是序号不是数量：把序号当数量核对会稳定产生误报
            continue
        if _is_range_prefix(reply, match.start()):
            # “前 N 条”是截断说明（N 是工具自己的条数上限），不是数据声明
            continue
        checked += 1
        claim = match.group(0).strip()
        raw, unit = match.group("num"), match.group("unit")
        if not (_candidates(raw, unit) & evidence.numbers) and claim not in seen:
            seen.add(claim)
            ungrounded.append(claim)
    for match in _DATE_RE.finditer(reply):
        checked += 1
        claim = match.group(0)
        if claim in evidence.dates or _norm(claim) in evidence.numbers:
            continue
        if claim not in seen:
            seen.add(claim)
            ungrounded.append(claim)
    return GroundingReport(
        grounded=not ungrounded,
        ungrounded=ungrounded[:_MAX_UNGROUNDED],
        checked=checked,
    )


def as_frame(report: GroundingReport) -> dict[str, Any]:
    """`done` 帧里的 `grounding` 字段（字段名 camelCase，与帧内其它字段一致）。"""
    return {
        "grounded": report.grounded,
        "ungroundedNumbers": list(report.ungrounded),
        "checked": report.checked,
    }


__all__ = [
    "EvidenceIndex",
    "GroundingReport",
    "as_frame",
    "build_evidence",
    "check_reply_numbers",
    "collect_calibers",
]
