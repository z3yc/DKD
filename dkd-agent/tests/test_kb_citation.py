"""知识库引用强制校验（3-6/5-3）的回归测试。

所有用例都是纯函数 + 构造的消息，不连库、不调 LLM。
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, ToolMessage

from app.services.kb_citation import KB_TOOL_NAME, enforce_citation, extract_kb_payload


def _tool_msg(payload: dict, name: str = KB_TOOL_NAME) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False), name=name, tool_call_id="t1"
    )


HITS = [
    {
        "docCode": "manual_x",
        "title": "X 机型手册",
        "score": 0.82,
        "excerpt": "E-777 表示压缩机过载。",
    },
    {"docCode": "sop_y", "title": "冷链 SOP", "score": 0.41, "excerpt": "先断电重启，再检查风扇。"},
]


def test_no_kb_tool_keeps_reply_unchanged() -> None:
    reply, forced = enforce_citation("普通回答", [HumanMessage(content="hi")])
    assert reply == "普通回答"
    assert forced is False


def test_hits_without_citation_falls_back_to_evidence_only() -> None:
    reply, forced = enforce_citation(
        "这个故障码说明压缩机坏了，应该马上换件。", [_tool_msg({"hits": HITS})]
    )
    assert forced is True
    assert "仅列证据" in reply
    assert "[KB:manual_x]" in reply
    assert "压缩机过载" in reply
    # 不得把原回答里没有依据的结论带出来
    assert "应该马上换件" not in reply


def test_hits_with_citation_is_accepted() -> None:
    answer = "E-777 表示压缩机过载 [KB:manual_x]；处置前先断电。"
    reply, forced = enforce_citation(answer, [_tool_msg({"hits": HITS})])
    assert reply == answer
    assert forced is False


def test_empty_hits_falls_back_to_no_coverage() -> None:
    reply, forced = enforce_citation(
        "根据知识库，应该更换主板。", [_tool_msg({"hits": [], "fallbackReason": "low_score"})]
    )
    assert forced is True
    assert "未检索到可用依据" in reply
    assert "low_score" in reply
    assert "更换主板" not in reply


def test_tool_error_falls_back() -> None:
    reply, forced = enforce_citation("答案", [_tool_msg({"error": "检索失败"})])
    assert forced is True
    assert "无法给出有依据的结论" in reply


def test_uses_latest_kb_tool_message() -> None:
    older = {"hits": [], "fallbackReason": "no_hit"}
    newer = {"hits": HITS}
    messages = [_tool_msg(older), HumanMessage(content="再查"), _tool_msg(newer)]
    payload = extract_kb_payload(messages)
    assert payload is not None
    assert payload["hits"]
    reply, forced = enforce_citation("有依据的回答 [KB:manual_x]", messages)
    assert forced is False


def test_malformed_tool_payload_is_skipped() -> None:
    bad = ToolMessage(content="not-json", name=KB_TOOL_NAME, tool_call_id="t1")
    reply, forced = enforce_citation("原回答", [bad])
    assert reply == "原回答"
    assert forced is False
