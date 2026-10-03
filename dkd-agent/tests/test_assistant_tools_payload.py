"""助手工具**载荷字段**的回归测试（F-6，2026-10-02 真环境浏览器验收发现）。

## 守的是什么

`search_device_history` 的载荷原先**漏了 `createTime`**（`read_tools.HistoryCase.created_time`
一直有值，库里也真有 `2023-03-24 10:41:10`，只是没往模型那边传）。后果有两条，第二条更贵：

1. 运营问“这台以前修过吗”，助手只能答“修过”，答不了“什么时候修过”——而工单时间恰恰是
   运营判断“这是老毛病还是新问题”的关键；
2. **模型只能从 `taskCode` 前缀（`202303240005`）推年份**，于是写出“2023 年 3 月有过维修记录”。
   这句话其实是对的，但载荷里没有 `2023` 这个数，`grounding.check_reply_numbers` 判定它
   “没有出处”，前端当场弹出提示条。该模块写明的底线是「**宁可漏报，不可误报**——误报会让
   提示条变成噪声，最终没人看」。所以根因在**载荷缺字段**，不是模型编造。

本用例把两条都钉住：载荷必须带时间；且带上之后，那句原本被误标的话必须变成有出处。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from app.services.grounding import build_evidence, check_reply_numbers
from app.tools import assistant_tools as at
from app.tools.read_tools import HistoryCase, HistoryCaseSearch

CREATED_AT = datetime(2023, 3, 24, 10, 41, 10)

# 窗口固定成 2020~2026 之间且**不含 2023** 的一段：
# 为什么必须固定：工具默认窗口是按 `date.today()` 倒推的，而倒推到某一天时窗口起点
# 自己就带 "2023"（例如 1825 天窗口在某年会得到 start="2023-10-04"）——那时即使删掉
# `createTime`，数字 "2023" 仍然能从窗口里找到出处，复现就失效了（写这条用定时踩到过一次）。
WINDOW_START = datetime(2020, 1, 1, 0, 0, 0)
WINDOW_END = datetime(2026, 1, 1, 0, 0, 0)


class _FakeSession:
    """只为让 `async with sessionmaker() as session` 能通过（本用例不真连库）。"""

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _install_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    case = HistoryCase(
        task_id=527,
        task_code="202303240001",
        inner_code="A1000001",
        task_status=4,
        task_status_label="已完成",
        phenomenon="111",
        phenomenon_usable=False,
        matched_by="same_device",
        confidence="low",
        confidence_basis="测试样本",
        created_time=CREATED_AT,
    )
    result = HistoryCaseSearch(
        inner_code="A1000001",
        cases=[case],
        total_cases=1,
        usable_case_count=0,
        no_evidence_hint="没有一条可用作故障现象",
    )

    async def _fake_search(session: Any, **kwargs: Any) -> HistoryCaseSearch:
        return result

    monkeypatch.setattr(at, "get_sessionmaker", lambda: lambda: _FakeSession())
    monkeypatch.setattr(at.read_tools, "search_history_cases", _fake_search)
    monkeypatch.setattr(
        at.read_tools, "default_case_window", lambda **kwargs: (WINDOW_START, WINDOW_END)
    )


async def test_history_payload_carries_create_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """载荷必须带工单时间（F-6）：既是运营要的答案，也是数字出处的来源。"""
    _install_stubs(monkeypatch)

    payload = json.loads(await at._search_device_history("A1000001", days=1825))

    cases = payload["cases"]
    assert cases, "桩数据必须命中一条案例（否则本用例什么都没验）"
    assert "createTime" in cases[0], (
        "历史案例载荷缺 createTime：模型只能从 taskCode 前缀推年份，"
        "会写成没出处的数字并触发口径误报（F-6）"
    )
    assert "2023-03-24" in str(cases[0]["createTime"])


async def test_missing_create_time_would_trigger_false_grounding_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """把因果钉死：**载荷带时间 → 那句话有出处**；**去掉时间 → 同一句话被判成无出处**。

    只断言“字段存在”是不够的——真正要防的是它再次缺失时**没人发现**。
    这里用同一句话在两种载荷下跑一遍 grounding，证明这个字段就是那句提示的开关。
    """
    _install_stubs(monkeypatch)
    reply = "这台机器在 2023 年 3 月有过维修记录。"
    good_payload = await at._search_device_history("A1000001", days=1825)

    def _report(payload_text: str):  # noqa: ANN202
        messages = [
            ToolMessage(content=payload_text, name="search_device_history", tool_call_id="c1")
        ]
        return check_reply_numbers(reply, evidence=build_evidence(messages))

    assert _report(good_payload).grounded, (
        "载荷已带 createTime，回复里的『2023 年』应当有出处——否则提示条会变成噪声"
    )

    # 复现有缺陷的载荷：删掉 createTime（等价于修复前的形状）
    broken = json.loads(good_payload)
    for item in broken["cases"]:
        item.pop("createTime", None)
    assert not _report(json.dumps(broken, ensure_ascii=False)).grounded, (
        "去掉 createTime 后仍被判为有出处 → 说明本用例没有真正复现 F-6，断言失去意义"
    )
