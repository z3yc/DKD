"""会话/消息持久化 + 配置版本 + 工具耗时采样测试（排期 4-1 / 4-2）。

纪律（AGENTS §8）：不连真库、不调 LLM——会话头/消息写入用假 session 断言 SQL 与 ORM 对象，
工具耗时用假工具 + ContextVar 断言。覆盖：

- 会话头 upsert：首插字段、重复只累加 `message_count`、title 截断、失败只 WARN；
- 一轮写 user+assistant 两条消息（seq 正确、都带 config_version）；
- config_version 取不到 → 写 NULL + WARN（**不伪造版本号**）；
- 工具执行采样：保留工具元数据/异常语义、记录耗时与结果规模、合并进 tool_calls。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from app.api import chat as chat_api
from app.audit import (
    DecisionLog,
    Message,
    conversation_title,
    record_message,
    touch_conversation,
)
from app.config import get_settings
from app.services import tool_timing


# ====================================================================================
# 假会话 / 会话工厂
# ====================================================================================
class FakeResult:
    rowcount = 1


class FakeSession:
    """记录 add/execute/commit/rollback；可注入写入异常。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.added: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._error = error

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        if self._error is not None:
            raise self._error
        self.calls.append((str(stmt), dict(params or {})))
        return FakeResult()

    async def commit(self) -> None:
        if self._error is not None:
            raise self._error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _install_session(monkeypatch, session: FakeSession) -> None:
    monkeypatch.setattr(chat_api, "get_sessionmaker", lambda: lambda: _SessionContext(session))


def _boom_factory() -> Any:  # pragma: no cover —— 只用于证明“不该建会话”
    raise AssertionError("审计关闭时不得建立数据库会话")


def _payload(message: str = "帮我看看 1 号机库存", conversation_id: str = "c-1") -> Any:
    return chat_api.ChatRequest(message=message, conversation_id=conversation_id, scene=2)


# ====================================================================================
# 1) 会话头 upsert（audit.touch_conversation）
# ====================================================================================
def test_conversation_title_truncates_and_falls_back():
    assert conversation_title("") == "新会话"
    assert conversation_title("   ") == "新会话"
    assert conversation_title("第一句") == "第一句"
    long_title = conversation_title("补" * 80)
    assert len(long_title) == 61 and long_title.endswith("…")


async def test_touch_conversation_inserts_on_first_turn():
    session = FakeSession()

    await touch_conversation(
        session,
        conversation_id="c-1",
        user_id=7,
        user_name="张三",
        scene=2,
        title="帮我看看 1 号机库存",
        config_version=5,
    )

    sql, params = session.calls[0]
    lowered = sql.lower()
    assert "insert into agent_conversation" in lowered
    assert "on duplicate key update" in lowered
    assert params["conversation_id"] == "c-1"
    assert params["user_id"] == 7
    assert params["user_name"] == "张三"
    assert params["scene"] == 2
    assert params["config_version"] == 5
    assert params["title"] == "帮我看看 1 号机库存"
    assert params["delta"] == 2, "一轮 = user + assistant 两条消息"
    assert session.commits == 1

    update_part = lowered.split("on duplicate key update", 1)[1]
    assert "message_count = message_count + :delta" in update_part
    # 会话元数据只在首插写入：后续消息不得覆盖标题/场景/用户/版本
    assert "title" not in update_part
    assert "scene" not in update_part
    assert "config_version" not in update_part
    assert "user_id" not in update_part


async def test_touch_conversation_accumulates_count_without_overwriting_metadata():
    session = FakeSession()
    await touch_conversation(
        session,
        conversation_id="c-1",
        user_id=7,
        user_name="张三",
        scene=3,
        title="第二句",
        config_version=6,
        delta=2,
    )
    sql, params = session.calls[0]
    assert params["delta"] == 2
    # 同一条 upsert 语句即可完成“已有会话只累加”，无需先查后写（避免竞态）
    assert "message_count = message_count + :delta" in sql.lower()


async def test_touch_conversation_skips_without_user_identity(caplog):
    session = FakeSession()
    with caplog.at_level("WARNING", logger="dkd.agent.audit"):
        await touch_conversation(
            session,
            conversation_id="c-1",
            user_id=None,
            user_name=None,
            scene=1,
            title="x",
            config_version=None,
        )
    assert session.calls == []
    assert any("跳过会话头写入" in record.getMessage() for record in caplog.records)


async def test_touch_conversation_degrades_on_db_failure(caplog):
    session = FakeSession(error=OperationalError("insert", {}, Exception("db down")))
    with caplog.at_level("WARNING", logger="dkd.agent.audit"):
        await touch_conversation(
            session,
            conversation_id="c-1",
            user_id=7,
            user_name="张三",
            scene=2,
            title="x",
            config_version=None,
        )
    assert session.rollbacks >= 1
    assert any("会话头写入失败" in record.getMessage() for record in caplog.records)


# ====================================================================================
# 2) 消息投影：config_version 透传
# ====================================================================================
async def test_record_message_persists_config_version():
    session = FakeSession()

    await record_message(
        session,
        conversation_id="c-1",
        seq=4,
        msg_role=2,
        user_id=7,
        content="回复",
        model="deepseek-chat",
        config_version=9,
        tool_calls=[{"name": "a"}],
    )

    message = session.added[0]
    assert isinstance(message, Message)
    assert message.config_version == 9
    assert message.seq == 4 and message.msg_role == 2
    assert message.tool_calls == [{"name": "a"}]
    assert session.commits == 1


async def test_record_message_allows_null_config_version():
    session = FakeSession()
    await record_message(session, conversation_id="c-1", seq=1, msg_role=1, config_version=None)
    message = session.added[0]
    assert message.config_version is None


# ====================================================================================
# 3) 一轮对话写 user + assistant + 会话头（api/chat._record_turn）
# ====================================================================================
@dataclass
class _Outcome:
    """_TurnOutcome 的最小替身（避免为了测字段先去跑一遍图）。"""

    reply: str = "补货建议：…"
    history_len: int = 6
    model: str | None = "deepseek-chat"
    tokens_in: int = 11
    tokens_out: int = 22
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    calibers: list[dict[str, Any]] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)
    config_version: int | None = 3


def _record_settings(*, audit_enabled: bool = True) -> Any:
    return get_settings().model_copy(update={"audit_enabled": audit_enabled})


async def test_record_turn_writes_user_and_assistant_with_config_version(monkeypatch):
    session = FakeSession()
    _install_session(monkeypatch, session)
    outcome = _Outcome(
        tool_calls=[
            {"name": "get_machine_inventory", "args": "{}", "durationMs": 12, "status": "ok"}
        ],
        calibers=[],
        grounding={"grounded": True},
    )

    await chat_api._record_turn(
        settings=_record_settings(),
        payload=_payload(),
        ctx_user_id=7,
        ctx_user_name="张三",
        rid="rid-1",
        thread_id="c-1",
        outcome=outcome,
    )

    assert len(session.added) == 3, "1 条决策留痕 + 2 条消息"
    assert isinstance(session.added[0], DecisionLog)
    user_message, assistant_message = session.added[1], session.added[2]

    assert isinstance(user_message, Message) and user_message.msg_role == 1
    assert user_message.seq == 5, "seq = history_len - 1（用户消息在本轮的位置）"
    assert user_message.content == "帮我看看 1 号机库存"
    assert user_message.config_version == 3

    assert assistant_message.msg_role == 2
    assert assistant_message.seq == 6, "seq = history_len（助手消息位置）"
    assert assistant_message.content == "补货建议：…"
    assert assistant_message.config_version == 3
    assert assistant_message.tool_calls == outcome.tool_calls

    touch_sql, touch_params = session.calls[0]
    assert "insert into agent_conversation" in touch_sql.lower()
    assert touch_params["delta"] == 2
    assert touch_params["title"] == "帮我看看 1 号机库存"
    assert touch_params["user_id"] == 7 and touch_params["scene"] == 2


async def test_record_turn_keeps_null_version_and_warns(monkeypatch, caplog):
    session = FakeSession()
    _install_session(monkeypatch, session)
    outcome = _Outcome(config_version=None, tool_calls=[], calibers=[], grounding={})

    with caplog.at_level("WARNING", logger="dkd.agent.chat"):
        await chat_api._record_turn(
            settings=_record_settings(),
            payload=_payload(),
            ctx_user_id=7,
            ctx_user_name="张三",
            rid="rid-2",
            thread_id="c-2",
            outcome=outcome,
        )

    messages = [obj for obj in session.added if isinstance(obj, Message)]
    assert [message.config_version for message in messages] == [None, None], "取不到版本写 NULL"
    assert any("配置版本" in record.getMessage() for record in caplog.records)


async def test_record_turn_does_nothing_when_audit_disabled(monkeypatch):
    monkeypatch.setattr(chat_api, "get_sessionmaker", _boom_factory)
    await chat_api._record_turn(
        settings=_record_settings(audit_enabled=False),
        payload=_payload(),
        ctx_user_id=7,
        ctx_user_name=None,
        rid="rid-3",
        thread_id="c-3",
        outcome=_Outcome(),
    )


async def test_record_turn_degrades_when_message_write_fails(monkeypatch, caplog):
    session = FakeSession(error=OperationalError("insert", {}, Exception("db down")))
    _install_session(monkeypatch, session)
    with caplog.at_level("WARNING", logger="dkd.agent.chat"):
        await chat_api._record_turn(
            settings=_record_settings(),
            payload=_payload(),
            ctx_user_id=7,
            ctx_user_name=None,
            rid="rid-4",
            thread_id="c-4",
            outcome=_Outcome(),
        )
    messages = [record.getMessage() for record in caplog.records]
    assert any("写入失败" in message or "降级" in message for message in messages), (
        "任一步失败都只 WARN，不得抛穿对话"
    )


# ====================================================================================
# 4) 工具执行采样（services/tool_timing，4-1 会话详情的耗时/结果规模）
# ====================================================================================
class _Args(BaseModel):
    inner_code: str = ""


def _fake_tool(**kwargs: Any) -> StructuredTool:
    async def _run(inner_code: str = "") -> Any:
        if kwargs.get("error") is not None:
            raise kwargs["error"]
        if kwargs.get("result") is not None:
            return kwargs["result"]
        return json.dumps({"items": [{"channel": 1}, {"channel": 2}]}, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=_run,
        name=kwargs.get("name", "get_machine_inventory"),
        description="查询设备货道",
        args_schema=_Args,
    )


async def test_wrapped_tool_keeps_metadata_and_records_timing():
    original = _fake_tool()
    wrapped = tool_timing.wrap_tools([original])[0]

    assert wrapped.name == original.name
    assert wrapped.description == original.description
    assert wrapped.args_schema is original.args_schema

    token = tool_timing.begin_turn()
    try:
        result = await wrapped.ainvoke({"inner_code": "A1000001"})
    finally:
        records = tool_timing.current_records()
        tool_timing.end_turn(token)

    assert json.loads(result)["items"]
    assert len(records) == 1
    record = records[0]
    assert record["name"] == "get_machine_inventory"
    assert record["status"] == "ok"
    assert record["durationMs"] >= 0
    assert record["resultRows"] == 2, "JSON 里的 items 行数"
    assert record["resultChars"] == len(result)
    assert "A1000001" in record["argsDigest"]


async def test_wrapped_tool_records_error_and_re_raises():
    original = _fake_tool(error=RuntimeError("boom"))
    wrapped = tool_timing.wrap_tools([original])[0]

    token = tool_timing.begin_turn()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await wrapped.ainvoke({"inner_code": "A1"})
    finally:
        records = tool_timing.current_records()
        tool_timing.end_turn(token)

    assert records[0]["status"] == "error"
    assert records[0]["resultRows"] == 0


def test_args_digest_masks_sensitive_values_and_truncates():
    digest = tool_timing.args_digest({"phone": "13812345678", "blob": "x" * 500})
    assert "138****5678" in digest, "参数摘要必须脱敏"
    assert len(digest) <= tool_timing.MAX_ARGS_DIGEST_CHARS + 1


def test_records_are_per_turn_and_reset():
    token = tool_timing.begin_turn()
    tool_timing.record_execution(name="a", arguments={}, result="ok", status="ok", duration_ms=1)
    assert len(tool_timing.current_records()) == 1
    tool_timing.end_turn(token)
    assert tool_timing.current_records() == []


def test_merge_tool_calls_prefers_langgraph_records():
    tool_calls = [{"name": "a", "args": "{}"}, {"name": "b", "args": "{}"}]
    records = [
        {
            "name": "b",
            "argsDigest": "{}",
            "durationMs": 7,
            "status": "ok",
            "resultChars": 2,
            "resultRows": 1,
        },
        {"name": "c", "status": "ok"},  # 多出来的记录：无对应 tool_call → 丢弃
    ]

    merged = tool_timing.merge_tool_calls(tool_calls, records)

    assert [item["name"] for item in merged] == ["a", "b"]
    assert merged[0]["durationMs"] == 0 and merged[0]["status"] is None, "对不上给 0/NULL"
    assert merged[0]["resultChars"] == 0 and merged[0]["resultRows"] == 0
    assert merged[1]["durationMs"] == 7 and merged[1]["resultRows"] == 1
    assert merged[1]["status"] == "ok"


async def test_chat_graph_wires_timed_tools_into_tool_node(monkeypatch):
    """图装配必须把**包装后**的工具交给 ToolNode（bind_tools 仍用原始对象）。"""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graphs import chat_graph as graph_module

    captured: dict[str, Any] = {}

    class _FakeToolNode:
        def __init__(self, tools: Any, handle_tool_errors: Any = None) -> None:
            captured["tools"] = list(tools)
            captured["handler"] = handle_tool_errors

        async def __call__(self, state: Any) -> dict[str, Any]:  # pragma: no cover
            return {}

    monkeypatch.setattr(graph_module, "ToolNode", _FakeToolNode)
    graph_module.build_chat_graph(
        checkpointer=InMemorySaver(),
        use_llm=True,
        tools=[_fake_tool(name="tool_a")],
    )

    assert [tool.name for tool in captured["tools"]] == ["tool_a"]
    assert captured["tools"][0] is not None
    assert captured["handler"] is graph_module._handle_tool_error
