"""SSE 全链路冒烟：帧格式、身份透传、参数校验拒绝路径。"""

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(ln[7:] for ln in lines if ln.startswith("event: "))
        data = json.loads(next(ln[6:] for ln in lines if ln.startswith("data: ")))
        out.append((event, data))
    return out


def test_chat_streams_echo_with_meta_first():
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


def test_chat_without_user_header_is_allowed_for_readonly_scene():
    """echo 属只读演示场景：无用户头不拒绝（写操作才必须 require_login）。"""
    resp = client.post("/agent/chat", json={"message": "hi"})
    assert resp.status_code == 200
    assert _frames(resp.text)[0][1]["user"] == "system"


def test_chat_rejects_empty_message():
    resp = client.post("/agent/chat", json={"message": ""})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_chat_rejects_invalid_scene():
    resp = client.post("/agent/chat", json={"message": "hi", "scene": 99})
    assert resp.status_code == 422
