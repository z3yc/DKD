"""知识库引用强制校验（排期 3-6 / 5-3 的落地件）。

## 为什么需要“回答层”的校验，而不是只靠提示词

`search_knowledge_base` 返回了可核验的片段（docCode/score/excerpt），提示词也要求引用；
但**提示词可被绕过**（模型改写、工具结果被拼接、用户诱导），而“看起来专业但没依据”的回答
是知识库接入后最危险的失败形态。因此这里在**回答落库/展示前**做一次确定性校验：

1. 本轮没有调用知识库工具 → 不改写（保持原行为）；
2. 调用了但 `hits` 为空 → **直接回退**为“未检索到可用依据，不能下结论”；
3. 有命中但回答里没有任何 `[KB:<docCode>]` 引用 → **回退为仅列证据**（附前 3 条片段）；
4. 有命中且至少一个引用标记 → 放行（引用内容是否完全对齐由 5-3 的评测继续收紧）。

## 边界

- 纯函数，不读库、不调 LLM（可单测、可在任意节点复用）；
- 只认 `[KB:` 前缀标记；不猜测“模型是不是引用了”——无法可信判定；
- 回退文本里**只列工具返回的片段**，不新增任何未检索到的内容。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger("dkd.agent.services.kb_citation")

KB_TOOL_NAME = "search_knowledge_base"
CITATION_PREFIX = "[KB:"

#: 回退时最多展示的片段数（多了会变成“把检索结果全贴给用户”，同样不专业）
MAX_FALLBACK_HITS = 3


def extract_kb_payload(messages: Iterable[Any]) -> dict[str, Any] | None:
    """从本轮消息里取最近一次知识库工具结果（没有/解析失败 → None）。

    @param messages LangGraph state 的消息序列（含 ToolMessage）
    @return 工具返回的 JSON 对象；不是 dict 或解析失败返回 None
    """
    for msg in reversed(list(messages or [])):
        if getattr(msg, "name", None) != KB_TOOL_NAME:
            continue
        content = getattr(msg, "content", "")
        text = content if isinstance(content, str) else str(content)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            logger.warning("知识库工具结果不是合法 JSON，跳过引用校验")
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _format_hits(hits: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for hit in hits[:MAX_FALLBACK_HITS]:
        doc = str(hit.get("docCode") or "?")
        title = str(hit.get("title") or "")
        excerpt = str(hit.get("excerpt") or "").strip().replace("\n", " ")
        try:
            score = f"{float(hit.get('score') or 0.0):.2f}"
        except (TypeError, ValueError):
            score = "?"
        lines.append(f"- [KB:{doc}] {title}（相似度 {score}）：{excerpt}")
    return "\n".join(lines)


def enforce_citation(reply: str, messages: Iterable[Any]) -> tuple[str, bool]:
    """知识库引用强制校验。

    @param reply 模型本轮回答
    @param messages 本轮 state 消息（用于找知识库工具结果）
    @return (最终回复, 是否发生回退)；未调用知识库工具时原样返回
    """
    payload = extract_kb_payload(messages)
    if payload is None:
        return reply, False
    if payload.get("error"):
        # 工具明确报错：让模型的原话保留也没用（它可能已经编了依据），统一回退
        return (
            "知识库检索失败，无法给出有依据的结论。请稍后重试，或改用诊断/工单页面核对。",
            True,
        )

    hits = [hit for hit in (payload.get("hits") or []) if isinstance(hit, dict)]
    if not hits:
        reason = str(payload.get("fallbackReason") or "no_hit")
        return (
            f"知识库未检索到可用依据（{reason}），不能据此下结论；"
            "可以补充文档语料，或把问题缩小到具体的故障码/现象后再试。",
            True,
        )

    if CITATION_PREFIX in reply:
        return reply, False

    fallback = (
        "（原回答未标注知识库引用，已按「仅列证据」回退；以下片段来自知识库检索结果）\n"
        + _format_hits(hits)
    )
    logger.warning("知识库回答未标注引用，已回退为仅列证据：hits=%d", len(hits))
    return fallback, True


__all__ = ["CITATION_PREFIX", "KB_TOOL_NAME", "enforce_citation", "extract_kb_payload"]
