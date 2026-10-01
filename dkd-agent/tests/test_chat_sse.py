"""SSE 全链路冒烟：帧格式、身份透传、参数校验拒绝路径。

说明：0-10 之后 /agent/chat 依赖 lifespan 装配的图与 checkpointer，
因此必须用带 lifespan 的 client 夹具（tests/conftest.py）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

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


# --------------------------------------------------------------------------------------
# 基础设施异常必须变成 error 帧，不能抛穿 SSE 生成器（3-9 演练撞出来的缺陷，2026-10-01）
# --------------------------------------------------------------------------------------


class _ExplodingGraph:
    """astream 一进入就抛异常，模拟 checkpointer/DB 在编排中途失效。

    为什么用「async def + yield」而不是普通函数返回生成器：只有函数体里出现 yield，
    它才是 async generator；调用时不会立刻执行，异常在第一次 __anext__ 时抛出——
    这正是真实场景（图已经开始跑、中途连不上库）。
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def astream(self, *_args: object, **_kwargs: object) -> AsyncIterator[dict]:
        raise self._error
        yield {}  # 让本函数成为 async generator（异常在首次 __anext__ 抛出）


def _post_chat(client: TestClient, conversation_id: str) -> object:
    return client.post(
        "/agent/chat",
        json={"message": "基础设施异常场景", "conversation_id": conversation_id, "scene": 1},
        headers={"X-Agent-User": "7"},
    )


def test_infra_error_becomes_error_frame_and_stream_stays_readable(client: TestClient):
    """通用基础设施异常 → error 帧（不是断流）。

    缺陷原貌：此前只抓 LlmError，psycopg.OperationalError 会抛穿 SSE 生成器，
    客户端拿到 httpx.ReadError（流直接断），**既没有 error 帧也没有任何提示**，
    前端只能表现为“气泡突然不动了”。违反 AGENTS §6.3 的编排层兜底原则。
    """
    client.app.state.chat_graph = _ExplodingGraph(RuntimeError("boom"))
    resp = _post_chat(client, "c-infra-1")

    assert resp.status_code == 200, "SSE 已经开始，仍应是 200（而不是 500 断流）"
    events = _frames(resp.text)
    assert events[0][0] == "meta"
    kinds = [e for e, _ in events]
    assert "error" in kinds, f"必须给出 error 帧，实际帧：{kinds}"

    err = next(d for e, d in events if e == "error")
    assert err["retryable"] is True, "本轮失败可重试（会话未保存）"
    assert "请求号" in err["msg"], "给用户一个可用于对日志的请求号"
    assert err["code"] == 500, f"非存储类异常应为 500，实际 {err['code']}"


def test_storage_error_is_503_and_message_points_at_session_store(client: TestClient):
    """存储类异常（psycopg OperationalError）→ 503 + 指向“会话存储”的文案。

    为什么要在**文案**上区分（而行为不区分）：用户不需要知道是哪个组件坏了，
    但“会话存储暂时不可用”与“服务内部错误”对用户的下一步动作完全不同
    （前者稍后重试即可，后者可能要报障）。
    """
    import psycopg

    client.app.state.chat_graph = _ExplodingGraph(
        psycopg.OperationalError(
            "consuming input failed: server closed the connection unexpectedly"
        )
    )
    resp = _post_chat(client, "c-infra-2")

    events = _frames(resp.text)
    err = next(d for e, d in events if e == "error")
    assert err["code"] == 503, f"存储类异常应为 503，实际 {err['code']}"
    assert "会话存储" in err["msg"], f"文案应指向会话存储：{err['msg']}"


def test_corrupted_sqlite_error_is_also_classified_as_storage(client: TestClient):
    """SQLite 文件损坏（`sqlite3.DatabaseError: database disk image is malformed`）也必须归存储类。

    为什么补这一条（2026-10-01，3-9 的 sqlite-corrupt 演练撞出来）：
    原分类只看 `Operational/Interface/DBAPI/SQLAlchemy`，而**损坏的 SQLite 文件**抛出的是
    `sqlite3.DatabaseError`（类型名 `DatabaseError`）——它会落到“服务内部错误（500）”，
    文案让人以为要去查编排逻辑，而真正该做的是恢复/替换会话库文件。

    这正是 FIX-24 那条修完之后**还剩下的一半**：异常不再抛穿 SSE 了（行为对了），
    但**分类**漏了一类（诊断信息还错）。两者都属于“读代码看不出来”的类型。
    """
    import sqlite3

    from app.api.chat import _is_storage_error

    assert _is_storage_error(sqlite3.DatabaseError("database disk image is malformed"))

    client.app.state.chat_graph = _ExplodingGraph(
        sqlite3.DatabaseError("database disk image is malformed")
    )
    resp = _post_chat(client, "c-infra-3")
    err = next(d for e, d in _frames(resp.text) if e == "error")
    assert err["code"] == 503, f"SQLite 损坏应归存储类（503），实际 {err['code']}"
    assert "会话存储" in err["msg"], f"文案应指向会话存储：{err['msg']}"


def test_business_error_is_not_misclassified_as_storage():
    """反向断言：业务/编程错误**不得**被说成“会话存储不可用”（否则运维会去查错东西）。"""
    from app.api.chat import _is_storage_error

    assert not _is_storage_error(RuntimeError("boom"))
    assert not _is_storage_error(ValueError("bad input"))
