"""SSE 全链路冒烟：帧格式、身份透传、参数校验拒绝路径。

说明：0-10 之后 /agent/chat 依赖 lifespan 装配的图与 checkpointer，
因此必须用带 lifespan 的 client 夹具（tests/conftest.py）。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        event = next((ln[7:] for ln in lines if ln.startswith("event: ")), "")
        data = json.loads(next((ln[6:] for ln in lines if ln.startswith("data: ")), "{}"))
        if event:
            out.append((event, data))
    return out


def test_chat_streams_echo_with_meta_first(client: TestClient):
    resp = client.post(
        "/agent/chat",
        json={"message": "hello", "conversation_id": "c-1", "scene": 2},
        headers={"X-Agent-User": "7", "X-Agent-Username": "tester", "X-Agent-Region": "3"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _frames(resp.text)
    assert events[0][0] == "meta"
    assert events[0][1]["conversation_id"] == "c-1"
    assert events[0][1]["user"] == "tester"
    assert events[0][1]["mode"] == "echo"
    assert events[-1][0] == "done"
    assert "".join(d["text"] for e, d in events if e == "delta") == "[echo] hello"


def test_chat_generates_conversation_id_when_missing(client: TestClient):
    resp = client.post("/agent/chat", json={"message": "hi"})
    meta = next(d for e, d in _frames(resp.text) if e == "meta")
    assert meta["conversation_id"].startswith("conv-")
    assert meta["user"] == "system"  # 只读演示场景允许无用户头


def test_chat_rejects_empty_message(client: TestClient):
    resp = client.post("/agent/chat", json={"message": ""})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_chat_rejects_invalid_scene(client: TestClient):
    resp = client.post("/agent/chat", json={"message": "hi", "scene": 99})
    assert resp.status_code == 422
