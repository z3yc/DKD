"""M1 验收相关：真 LLM 分支下的 meta 标注与断线续传（节点一律打桩，不打外部 API）。

背景（2026-09-21 M1 验收发现）：`meta.mode` 原先硬编码为 `"echo"`，即使 `DKD_AGENT_USE_LLM=1`
走了真 LLM，帧里仍写 echo —— 审计与成本归因会把真 LLM 调用误判为链路回显。
本文件锁定修复后的契约：echo 分支写 `mode=echo`，LLM 分支写 `mode=llm` + 服务端实际模型名。
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.graphs.chat_graph as chat_graph


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        event = next((ln[7:] for ln in lines if ln.startswith("event: ")), "")
        data = json.loads(next((ln[6:] for ln in lines if ln.startswith("data: ")), "{}"))
        if event:
            out.append((event, data))
    return out


def _meta(text: str) -> dict:
    return next(d for e, d in _frames(text) if e == "meta")


def _done(text: str) -> dict:
    """done 帧：token 级流式下，服务端实际模型名与 history_len 在这里才确定。"""
    return next(d for e, d in _frames(text) if e == "done")


def _stub_model(
    monkeypatch, *, content: str, model_name: str, tokens_in: int, tokens_out: int
) -> None:
    """把 LLM 节点依赖的模型客户端换成桩（不产生任何外部调用）。"""

    class _StubChatModel:
        async def ainvoke(self, _messages):  # noqa: ANN001, ANN202 —— 只需满足节点用到的调用面
            return AIMessage(
                content=content,
                # total_tokens 必填：本版本 langchain 的 usage_metadata 校验要求三项齐全
                usage_metadata={
                    "input_tokens": tokens_in,
                    "output_tokens": tokens_out,
                    "total_tokens": tokens_in + tokens_out,
                },
                response_metadata={"model_name": model_name},
            )

    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: _StubChatModel())


def test_llm_branch_meta_marks_mode_llm_and_actual_model(client_with_llm: TestClient, monkeypatch):
    _stub_model(
        monkeypatch,
        content="建议给 A1 货道补货 6 瓶。",
        model_name="deepseek-flash",
        tokens_in=17,
        tokens_out=13,
    )

    resp = client_with_llm.post("/agent/chat", json={"message": "给出补货建议", "scene": 2})

    assert resp.status_code == 200
    meta = _meta(resp.text)
    assert meta["mode"] == "llm", "走了 LLM 节点就不能再标 echo（审计与成本归因依赖它）"
    assert meta["model"] is None, "生成开始前拿不到服务端实际模型，宁缺不填配置里的假值"
    done = _done(resp.text)
    assert done["model"] == "deepseek-flash", (
        "模型名必须取服务端实际返回（实测 deepseek-chat 会路由到 flash）"
    )
    assert done["history_len"] == 2, "history_len 同样由 done 帧补全"
    deltas = "".join(d["text"] for e, d in _frames(resp.text) if e == "delta")
    assert deltas == "建议给 A1 货道补货 6 瓶。"


def test_resume_keeps_mode_and_model_from_original_turn(client_with_llm: TestClient, monkeypatch):
    _stub_model(
        monkeypatch, content="续传内容", model_name="deepseek-flash", tokens_in=5, tokens_out=4
    )

    first = client_with_llm.post(
        "/agent/chat", json={"message": "abc", "conversation_id": "conv-m1"}
    )
    meta_first = _meta(first.text)
    assert meta_first["mode"] == "llm"
    total_frames = sum(1 for e, _ in _frames(first.text) if e == "delta")
    assert total_frames >= 1

    # 客户端只收到第 1 帧后断线，重连时带 Last-Event-ID（Python 侧只补发未收帧）
    time.sleep(0.05)
    resumed = client_with_llm.post(
        "/agent/chat",
        json={"message": "ignored", "conversation_id": "conv-m1"},
        headers={"Last-Event-ID": "1"},
    )

    meta_resumed = _meta(resumed.text)
    assert meta_resumed["resumed"] is True
    assert meta_resumed["mode"] == "llm", "续传路径不重跑图，但模型名必须从 checkpoint 读回"
    assert meta_resumed["model"] == "deepseek-flash"
    resent = [d["text"] for e, d in _frames(resumed.text) if e == "delta"]
    assert len(resent) == total_frames - 1, "已收到的第 1 帧不应重发"


def test_echo_branch_still_marks_mode_echo(client: TestClient):
    resp = client.post("/agent/chat", json={"message": "hello", "conversation_id": "conv-echo"})
    meta = _meta(resp.text)
    assert meta["mode"] == "echo"
    assert _done(resp.text)["model"] == "echo", "echo 节点的模型名就是 echo（与 LLM 分支区分开）"


def _deltas_with_id(text: str) -> list[tuple[int, str]]:
    """取 (事件id, 文本) 序列，用于断言“token 级多帧 + id 自增”。"""
    out: list[tuple[int, str]] = []
    current_id = 0
    event = ""
    for block in text.strip().split("\n\n"):
        data = {}
        for line in block.splitlines():
            if line.startswith("id: "):
                current_id = int(line[4:])
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event == "delta":
            out.append((current_id, data.get("text", "")))
    return out


def test_llm_tokens_arrive_as_multiple_delta_frames(client_with_llm: TestClient, monkeypatch):
    """token 级流式回归：LLM 的分片回调必须逐块下发，而不是整段一帧。

    这条用例是 M1 缺口（帧级 → token 级）的防回归锚点：如果哪天有人把 astream 改回 ainvoke，
    帧数会退化成 1，用例立刻失败。
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    fake = GenericFakeChatModel(
        messages=iter([AIMessage(content="建议给 A1 货道补货 六 瓶 可乐 以备 周末 高峰")])
    )
    monkeypatch.setattr(chat_graph, "build_chat_model", lambda *a, **kw: fake)

    resp = client_with_llm.post("/agent/chat", json={"message": "补货建议", "scene": 2})

    assert resp.status_code == 200
    deltas = _deltas_with_id(resp.text)
    assert len(deltas) > 1, f"必须是多帧 token 级下发，实际只有 {len(deltas)} 帧"
    assert [i for i, _ in deltas] == list(range(1, len(deltas) + 1)), (
        "帧 id 必须自增（断点续传锚点）"
    )
    assert "".join(text for _, text in deltas) == "建议给 A1 货道补货 六 瓶 可乐 以备 周末 高峰"
    assert _done(resp.text)["finish_reason"] == "llm"
