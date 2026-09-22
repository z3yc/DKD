"""会话持久化与断线重连测试（任务 0-10 验收标准）。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.graphs.chat_graph import build_chat_graph


def _frames(text: str) -> list[tuple[int | None, str, dict]]:
    """解析 SSE：返回 (事件id, 事件名, 数据)。"""
    out: list[tuple[int | None, str, dict]] = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        event_id = None
        event = ""
        data: dict = {}
        for ln in lines:
            if ln.startswith("id: "):
                event_id = int(ln[4:])
            elif ln.startswith("event: "):
                event = ln[7:]
            elif ln.startswith("data: "):
                data = json.loads(ln[6:])
        if event:
            out.append((event_id, event, data))
    return out


def test_state_persists_across_requests(client: TestClient):
    """跨请求恢复：同 conversation_id 第二次请求时 history_len 应累加。

    注：token 级流式（M1 后）下，首轮请求的 meta 帧只能带“开始时已知”的信息，
    history_len 与 model 由 done 帧补全（见 tests/test_chat_llm_mode.py 的契约说明）。
    """
    first = _frames(
        client.post("/agent/chat", json={"message": "第一轮", "conversation_id": "conv-A"}).text
    )
    meta1 = next(d for _, e, d in first if e == "meta")
    done1 = next(d for _, e, d in first if e == "done")
    assert done1["history_len"] == 2  # 用户 + 助手
    assert meta1["resumed"] is False
    assert meta1["conversation_id"] == "conv-A"

    second = _frames(
        client.post("/agent/chat", json={"message": "第二轮", "conversation_id": "conv-A"}).text
    )
    done2 = next(d for _, e, d in second if e == "done")
    assert done2["history_len"] == 4  # 历史累积，证明命中 checkpoint
    assert next(d for _, e, d in second if e == "meta")["conversation_id"] == "conv-A"


def test_conversations_are_isolated(client: TestClient):
    client.post("/agent/chat", json={"message": "A轮", "conversation_id": "conv-iso-1"})
    resp = client.post("/agent/chat", json={"message": "B轮", "conversation_id": "conv-iso-2"})
    assert next(d for _, e, d in _frames(resp.text) if e == "done")["history_len"] == 2


def test_reconnect_resumes_from_last_event_id(client: TestClient):
    """断线重连续接：带 Last-Event-ID 时不重跑图，只补发未收到帧。"""
    body = {"message": "x" * 100, "conversation_id": "conv-resume"}  # 100 字符 → 5 帧
    full = _frames(client.post("/agent/chat", json=body).text)
    deltas = [(i, d) for i, e, d in full if e == "delta"]
    assert len(deltas) == 5
    assert [i for i, _ in deltas] == [1, 2, 3, 4, 5]

    resumed = _frames(client.post("/agent/chat", json=body, headers={"Last-Event-ID": "3"}).text)
    meta = next(d for _, e, d in resumed if e == "meta")
    assert meta["resumed"] is True
    assert meta["history_len"] == 2  # 未重复追加消息（重连不等于新一轮）
    assert [i for i, e, _ in resumed if e == "delta"] == [4, 5]  # 只补发 4、5
    assert next(d for _, e, d in resumed if e == "done")["frames"] == 5


def test_resume_unknown_conversation_returns_404_frame(client: TestClient):
    resp = client.post(
        "/agent/chat",
        json={"message": "hi", "conversation_id": "nope"},
        headers={"Last-Event-ID": "1"},
    )
    frame = _frames(resp.text)[0]
    assert frame[1] == "error"
    assert frame[2]["code"] == 404


def test_completed_resume_returns_no_duplicate_deltas(client: TestClient):
    """已全部收到后重连：不应重发任何 delta（幂等）。"""
    body = {"message": "abcdefghijklmnopqrstuvwxyz", "conversation_id": "conv-done"}
    client.post("/agent/chat", json=body)
    resumed = _frames(client.post("/agent/chat", json=body, headers={"Last-Event-ID": "99"}).text)
    assert [e for _, e, _ in resumed] == ["meta", "done"]


async def test_checkpoint_file_is_reusable_across_store_instances(tmp_path):
    """文件持久化：换一个 store 实例（等价于重启进程）仍能读回历史。"""
    from app.checkpoint import CheckpointStore

    path = tmp_path / "cp.db"
    store1 = CheckpointStore(path)
    saver1 = await store1.open()
    graph1 = build_chat_graph(checkpointer=saver1)
    config = {"configurable": {"thread_id": "thread-1"}}
    await graph1.ainvoke({"messages": [{"role": "user", "content": "持久化验证"}]}, config)
    await store1.close()

    store2 = CheckpointStore(path)
    saver2 = await store2.open()
    graph2 = build_chat_graph(checkpointer=saver2)
    snapshot = await graph2.aget_state(config)
    await store2.close()

    assert path.exists()
    texts = [str(m.content) for m in snapshot.values["messages"]]
    assert any("持久化验证" in t for t in texts)
    assert snapshot.values["reply"].startswith("[echo] ")
