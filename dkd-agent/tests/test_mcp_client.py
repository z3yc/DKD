"""MCP HTTP JSON-RPC 客户端测试（排期 3-7）。

全部打桩（AGENTS §8）：HTTP 层 monkeypatch `mcp_client._post_json`（或 httpx.AsyncClient），
数据库层用假 `AsyncSession`；**不连真库、不真出网、不需要真实 MCP server**。

覆盖四道闸 + 落库纪律：
- 只读闸：readOnlyHint 缺失/false 一律拒绝，sync 统计 rejected；
- 超时闸：httpx 超时 → McpError(timeout)，消息不含请求头/密钥；
- 行数闸：超行截断且 `truncated=true`，行数上限可被全局配置夹取；
- 密钥闸：env_keys 只按环境变量名解析，值只进请求头，日志/异常不含值。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import mcp as mcp_api
from app.config import get_settings
from app.services import mcp_client
from app.services.mcp_client import (
    MAX_RESULT_CHARS,
    McpError,
    McpServer,
    build_args_schema,
    build_auth_headers,
    build_langchain_tools,
    call_tool,
    is_read_only_tool,
    list_synced_tools,
    load_enabled_servers,
    load_server_by_code,
    load_synced_tool,
    probe_and_sync,
    probe_server,
    split_read_only_tools,
    sync_server_tools,
)

TEST_SECRET = "unit-test-secret"
AUTH_HEADERS = {"X-Agent-Secret": TEST_SECRET}


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch) -> Iterator[None]:
    """本文件需要服务间密钥与稳定的 MCP 配置；清 Settings 缓存避免用例之间串扰。"""
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", TEST_SECRET)
    monkeypatch.setenv("DKD_AGENT_MCP_ENABLED", "1")
    monkeypatch.setenv("DKD_AGENT_MCP_MAX_TIMEOUT_MS", "5000")
    monkeypatch.setenv("DKD_AGENT_MCP_MAX_ROW_LIMIT", "200")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ====================================================================================
# 公共构造：假会话 / 假 JSON-RPC / server
# ====================================================================================
class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    """记录 SQL 与参数；按调用序返回预置行/影响行数；可注入异常。"""

    def __init__(
        self,
        rows_by_call: list[list[dict[str, Any]]] | None = None,
        rowcount_by_call: list[int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self._rows_by_call = rows_by_call or []
        self._rowcount_by_call = rowcount_by_call or []
        self._error = error

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        if self._error is not None:
            raise self._error
        index = len(self.calls)
        self.calls.append((str(stmt), dict(params or {})))
        rows = self._rows_by_call[index] if index < len(self._rows_by_call) else []
        rowcount = self._rowcount_by_call[index] if index < len(self._rowcount_by_call) else 0
        return FakeResult(rows, rowcount=rowcount)

    async def commit(self) -> None:
        self.commits += 1


def _sql_text(session: FakeSession) -> str:
    return " ".join(sql for sql, _ in session.calls).lower()


def _server(**overrides: Any) -> McpServer:
    data: dict[str, Any] = {
        "id": 1,
        "code": "demo",
        "name": "演示 server",
        "transport": "http",
        "endpoint": "https://mcp.example.com/rpc",
        "env_keys": {},
        "timeout_ms": 3000,
        "row_limit": 100,
    }
    data.update(overrides)
    return McpServer(**data)


def _tool(
    name: str = "query_devices",
    *,
    read_only: bool | None = True,
    schema: dict[str, Any] | None = None,
    description: str = "查询设备",
) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": schema or {"type": "object", "properties": {}},
    }
    if read_only is not None:
        tool["annotations"] = {"readOnlyHint": read_only}
    return tool


class FakeRpc:
    """假 JSON-RPC：按 method 返回预置 result（或抛 _post_json 层异常）。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.timeouts: list[float] = []
        self._responses = responses

    async def __call__(
        self,
        url: str,
        payload: Any,
        headers: Any,
        timeout_s: float,
    ) -> dict[str, Any]:
        self.requests.append(dict(payload))
        self.headers.append(dict(headers))
        self.timeouts.append(timeout_s)
        value = self._responses.get(payload["method"])
        if isinstance(value, Exception):
            raise value
        return {"jsonrpc": "2.0", "id": payload["id"], "result": value}


# ====================================================================================
# 只读闸
# ====================================================================================
def test_read_only_hint_must_be_explicit_true():
    assert is_read_only_tool(_tool(read_only=True)) is True
    assert is_read_only_tool(_tool(read_only=False)) is False
    assert is_read_only_tool(_tool(read_only=None)) is False, "缺失 annotations 不得猜成只读"
    assert is_read_only_tool({"name": "x", "annotations": "oops"}) is False


def test_split_read_only_tools_counts_rejected():
    tools = [_tool("a"), _tool("b", read_only=False), _tool("c", read_only=None)]
    allowed, rejected = split_read_only_tools(tools)
    assert [tool["name"] for tool in allowed] == ["a"]
    assert [tool["name"] for tool in rejected] == ["b", "c"]


# ====================================================================================
# 探测：握手 / 过滤 / 失败映射
# ====================================================================================
async def test_probe_filters_non_read_only_and_counts(monkeypatch):
    rpc = FakeRpc(
        {
            "initialize": {"protocolVersion": "2024-11-05", "capabilities": {}},
            "tools/list": {
                "tools": [_tool("a"), _tool("b", read_only=False), _tool("c", read_only=None)]
            },
        }
    )
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await probe_server(_server())

    assert result["ok"] is True
    assert [tool["name"] for tool in result["tools"]] == ["a"]
    assert result["rejectedCount"] == 2
    assert result["errorType"] is None
    assert [request["method"] for request in rpc.requests] == ["initialize", "tools/list"]
    assert rpc.requests[0]["params"]["protocolVersion"] == "2024-11-05"


async def test_probe_failure_returns_error_type_without_raising(monkeypatch):
    rpc = FakeRpc({"initialize": McpError("timeout", "调用 MCP server 超时（5.0s）", None)})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await probe_server(_server())

    assert result["ok"] is False
    assert result["errorType"] == "timeout"
    assert result["tools"] == [] and result["rejectedCount"] == 0
    assert "超时" in result["errorMsg"]


async def test_probe_rejects_unsupported_transport_without_network(monkeypatch):
    rpc = FakeRpc({})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await probe_server(_server(transport="stdio", endpoint="node server.js"))

    assert result["ok"] is False
    assert result["errorType"] == "unsupported"
    assert rpc.requests == [], "不支持的 transport 不得发出任何请求"


async def test_probe_respects_mcp_enabled_switch(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_MCP_ENABLED", "0")
    get_settings.cache_clear()
    rpc = FakeRpc({})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await probe_server(_server())

    assert result["ok"] is False and result["errorType"] == "disabled"
    assert rpc.requests == []


# ====================================================================================
# 传输层：超时/HTTP/JSON 错误映射，且不泄露请求头
# ====================================================================================
class _FakeHttpxClient:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> _FakeHttpxClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, *, bad_json: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self) -> Any:
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


async def test_post_json_timeout_maps_to_mcp_error_without_secret(monkeypatch):
    monkeypatch.setattr(
        mcp_client.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeHttpxClient(httpx.TimeoutException("boom")),
    )
    secret = "super-secret-value"
    monkeypatch.setenv("DKD_MCP_TEST_KEY", secret)
    server = _server(env_keys={"Authorization": "DKD_MCP_TEST_KEY"})
    headers = build_auth_headers(server)
    assert headers["Authorization"] == secret

    with pytest.raises(McpError) as excinfo:
        await mcp_client._post_json(server.endpoint, {"jsonrpc": "2.0"}, headers, 5.0)

    assert excinfo.value.error_type == "timeout"
    assert secret not in str(excinfo.value) and secret not in excinfo.value.message


async def test_post_json_http_status_and_bad_json(monkeypatch):
    monkeypatch.setattr(
        mcp_client.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeHttpxClient(_FakeResponse(401, {"error": "unauthorized"})),
    )
    with pytest.raises(McpError) as http_error:
        await mcp_client._post_json("https://x", {}, {}, 5.0)
    assert http_error.value.error_type == "http"
    assert http_error.value.http_status == 401

    monkeypatch.setattr(
        mcp_client.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeHttpxClient(_FakeResponse(200, None, bad_json=True)),
    )
    with pytest.raises(McpError) as json_error:
        await mcp_client._post_json("https://x", {}, {}, 5.0)
    assert json_error.value.error_type == "jsonrpc"


# ====================================================================================
# 密钥闸：只按 env 名取值，缺失只 WARN 变量名
# ====================================================================================
def test_auth_headers_resolve_by_env_name_only(monkeypatch, caplog):
    monkeypatch.setenv("DKD_MCP_TEST_KEY", "secret-value")
    server = _server(env_keys={"Authorization": "DKD_MCP_TEST_KEY"})

    with caplog.at_level("WARNING", logger="dkd.agent.services.mcp"):
        headers = build_auth_headers(server)

    assert headers["Authorization"] == "secret-value"
    assert all("secret-value" not in record.getMessage() for record in caplog.records)
    assert headers["Content-Type"] == "application/json"


def test_auth_headers_missing_env_warns_without_value(monkeypatch, caplog):
    monkeypatch.delenv("DKD_MCP_TEST_KEY", raising=False)
    server = _server(env_keys={"Authorization": "DKD_MCP_TEST_KEY"})

    with caplog.at_level("WARNING", logger="dkd.agent.services.mcp"):
        headers = build_auth_headers(server)

    assert "Authorization" not in headers
    messages = [record.getMessage() for record in caplog.records]
    assert any("DKD_MCP_TEST_KEY" in message for message in messages)
    assert all("secret" not in message for message in messages)


# ====================================================================================
# 同步：upsert + 旧工具停用 + 上限夹取 + 无 DELETE
# ====================================================================================
async def test_sync_server_tools_upserts_and_disables_stale():
    session = FakeSession(rowcount_by_call=[0, 0, 0, 0, 3, 2])
    server = _server()
    tools = [_tool("a"), _tool("b")]

    counts = await sync_server_tools(session, server, tools)

    assert counts["synced"] == 2
    assert counts["disabled"] == 5
    assert session.commits == 1
    sql = _sql_text(session)
    assert "insert into agent_mcp_tool" in sql
    assert "insert into agent_tool" in sql
    assert "source" in sql and "'mcp'" in sql, "agent_tool 必须标记 source=mcp"
    assert "code not in" in sql, "本次清单外的旧工具必须停用（expanding IN 由 SQLAlchemy 展开）"
    assert "delete" not in sql, "AGENTS §7.4：只停用不删除"

    mcp_tool_params = session.calls[0][1]
    assert mcp_tool_params["code"] == "mcp.demo.a"
    assert mcp_tool_params["name"] == "a"
    agent_tool_params = session.calls[1][1]
    assert agent_tool_params["scope"] == "chat"
    assert agent_tool_params["timeout_ms"] == 3000
    assert agent_tool_params["row_limit"] == 100
    assert json.loads(agent_tool_params["input_schema_json"])["type"] == "object"


async def test_sync_server_tools_clamps_hard_limits():
    session = FakeSession()
    server = _server(timeout_ms=60000, row_limit=1000)

    await sync_server_tools(session, server, [_tool("a")])

    params = session.calls[1][1]
    assert params["timeout_ms"] == 5000
    assert params["row_limit"] == 200


async def test_sync_server_tools_without_read_only_disables_all():
    session = FakeSession()
    await sync_server_tools(session, _server(), [])

    sql = _sql_text(session)
    assert "insert into" not in sql
    assert "update agent_mcp_tool" in sql and "update agent_tool" in sql
    assert "not in" not in sql, "清单为空时无需 NOT IN 分支"


async def test_sync_server_tools_filters_write_tools_defensively():
    session = FakeSession()
    counts = await sync_server_tools(
        session, _server(), [_tool("read"), _tool("write", read_only=False)]
    )
    assert counts["synced"] == 1
    assert len(session.calls) == 4, "只有 1 个只读工具：2 条 upsert + 2 条停用旧工具"
    assert session.calls[0][1]["code"] == "mcp.demo.read"
    assert session.calls[1][1]["code"] == "mcp.demo.read"


# ====================================================================================
# 探测并同步：写探测记录 + 更新 server 状态
# ====================================================================================
async def test_probe_and_sync_records_success(monkeypatch):
    rpc = FakeRpc(
        {
            "initialize": {"protocolVersion": "2024-11-05"},
            "tools/list": {"tools": [_tool("a")]},
        }
    )
    monkeypatch.setattr(mcp_client, "_post_json", rpc)
    session = FakeSession()

    result = await probe_and_sync(session, _server())

    assert result["ok"] is True and result["synced"] == 1
    assert "insert into agent_mcp_probe" in session.calls[0][0].lower()
    probe_params = session.calls[0][1]
    assert probe_params["ok"] == 1 and probe_params["tool_count"] == 1
    assert "update agent_mcp_server" in session.calls[1][0].lower()
    assert session.calls[1][1]["status"] == "ok"
    assert session.commits >= 1


async def test_probe_and_sync_records_failure_and_keeps_tools(monkeypatch):
    rpc = FakeRpc({"initialize": McpError("timeout", "调用 MCP server 超时（5.0s）", None)})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)
    session = FakeSession()

    result = await probe_and_sync(session, _server())

    assert result["ok"] is False and result["synced"] == 0
    probe_params = session.calls[0][1]
    assert probe_params["ok"] == 0 and probe_params["error_type"] == "timeout"
    assert session.calls[1][1]["status"] == "error"
    assert "insert into agent_mcp_tool" not in _sql_text(session), "失败不得改动已同步工具"
    assert session.commits == 1


# ====================================================================================
# tools/call：文本提取 + 行数截断（必须显式标注）
# ====================================================================================
async def test_call_tool_extracts_text_and_is_error(monkeypatch):
    rpc = FakeRpc(
        {
            "tools/call": {
                "content": [{"type": "text", "text": "第一行"}, {"type": "text", "text": "第二行"}],
                "isError": True,
            }
        }
    )
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await call_tool(_server(), "query", {"device_id": "A1"})

    assert result["text"] == "第一行\n第二行"
    assert result["isError"] is True
    assert result["truncated"] is False
    assert rpc.requests[0]["params"] == {"name": "query", "arguments": {"device_id": "A1"}}


async def test_call_tool_truncates_by_row_limit_with_explicit_marker(monkeypatch):
    text = "\n".join(f"第{index}行数据" for index in range(1, 301))
    rpc = FakeRpc({"tools/call": {"content": [{"type": "text", "text": text}]}})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await call_tool(_server(row_limit=200), "query")

    assert result["truncated"] is True
    lines = result["text"].splitlines()
    assert len(lines) == 201, "前 200 行 + 一行截断说明"
    assert "已截断" in lines[-1] and "300" in lines[-1]


async def test_call_tool_clamps_row_limit_to_global_max(monkeypatch):
    text = "\n".join(str(index) for index in range(1, 301))
    rpc = FakeRpc({"tools/call": {"content": [{"type": "text", "text": text}]}})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await call_tool(_server(row_limit=1000), "query")

    assert result["truncated"] is True
    assert len(result["text"].splitlines()) == 201, "server 声明 1000 行也要被夹到 200"


async def test_call_tool_hard_char_limit(monkeypatch):
    text = "a" * (MAX_RESULT_CHARS + 100)
    rpc = FakeRpc({"tools/call": {"content": [{"type": "text", "text": text}]}})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)

    result = await call_tool(_server(), "query")

    assert result["truncated"] is True
    assert len(result["text"]) <= MAX_RESULT_CHARS + 100


async def test_call_tool_rejects_illegal_name_without_request(monkeypatch):
    rpc = FakeRpc({})
    monkeypatch.setattr(mcp_client, "_post_json", rpc)
    with pytest.raises(McpError) as excinfo:
        await call_tool(_server(), "bad name")
    assert excinfo.value.error_type == "validation"
    assert rpc.requests == []


# ====================================================================================
# LangChain 包装：schema → pydantic、只读、晚绑定回归
# ====================================================================================
def test_build_args_schema_maps_primitive_and_degrades_complex_types():
    schema = {
        "type": "object",
        "properties": {
            "device_id": {"type": "string", "description": "设备号"},
            "days": {"type": "integer"},
            "ratio": {"type": "number"},
            "active": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["device_id"],
    }

    model = build_args_schema("query", schema)

    assert model.model_fields["device_id"].annotation is str
    assert model.model_fields["device_id"].is_required() is True
    assert model.model_fields["days"].annotation == int | None
    assert model.model_fields["ratio"].annotation == float | None
    assert model.model_fields["active"].annotation == bool | None
    assert model.model_fields["tags"].annotation == str | None
    assert "复杂类型" in (model.model_fields["tags"].description or "")


def test_build_args_schema_skips_illegal_field_names():
    schema = {
        "type": "object",
        "properties": {"bad-name": {"type": "string"}, "ok": {"type": "string"}},
    }
    model = build_args_schema("query", schema)
    assert list(model.model_fields) == ["ok"]


async def test_build_langchain_tools_only_read_only_and_binds_correct_name(monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call_tool(
        server: McpServer, tool_name: str, arguments: Any = None
    ) -> dict[str, Any]:
        calls.append((tool_name, dict(arguments or {})))
        return {"text": f"{tool_name} 结果", "truncated": False, "isError": False}

    monkeypatch.setattr(mcp_client, "call_tool", fake_call_tool)
    tools = build_langchain_tools(
        _server(),
        [
            _tool("first", schema={"type": "object", "properties": {"q": {"type": "string"}}}),
            _tool("second"),
            _tool("write_tool", read_only=False),
        ],
    )

    assert [tool.name for tool in tools] == ["first", "second"]
    assert await tools[0].ainvoke({"q": "库存"}) == "first 结果"
    assert await tools[1].ainvoke({}) == "second 结果"
    # 晚绑定回归：调第二个工具不能落到第一个/最后一个的名字上
    assert calls == [("first", {"q": "库存"}), ("second", {})]
    assert all("只读工具" in (tool.description or "") for tool in tools)


def test_build_langchain_tools_skips_illegal_tool_names(caplog):
    with caplog.at_level("WARNING", logger="dkd.agent.services.mcp"):
        tools = build_langchain_tools(_server(), [_tool("bad.name"), _tool("good")])
    assert [tool.name for tool in tools] == ["good"]
    assert any("工具名非法" in record.getMessage() for record in caplog.records)


# ====================================================================================
# 读取：server / 已同步工具
# ====================================================================================
def _server_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 7,
        "code": "demo",
        "name": "演示",
        "transport": "http",
        "endpoint": "https://mcp.example.com/rpc",
        "env_keys_json": '{"Authorization": "DKD_MCP_KEY"}',
        "enabled": 1,
        "timeout_ms": 4000,
        "row_limit": 50,
    }
    row.update(overrides)
    return row


async def test_load_server_by_code_parses_env_keys():
    session = FakeSession(rows_by_call=[[_server_row()]])
    server = await load_server_by_code(session, "demo")
    assert server is not None
    assert server.id == 7 and server.timeout_ms == 4000 and server.row_limit == 50
    assert server.env_keys == {"Authorization": "DKD_MCP_KEY"}
    sql, params = session.calls[0]
    assert "enabled = 1" in sql and "del_flag = '0'" in sql
    assert params["code"] == "demo"


async def test_load_server_by_code_returns_none_when_absent():
    assert await load_server_by_code(FakeSession(rows_by_call=[[]]), "demo") is None


async def test_load_server_by_code_include_disabled_changes_sql_scope():
    """探测/同步必须能读停用 server；日常读取默认只认 enabled=1。"""
    session = FakeSession(rows_by_call=[[_server_row(enabled=0)]])
    server = await load_server_by_code(session, "demo", include_disabled=True)

    assert server is not None and server.enabled is False
    sql = session.calls[0][0]
    assert "enabled = 1" not in sql, "探测/同步不能被 enabled 挡住（安全流程：先停用再探测）"
    assert "del_flag = '0'" in sql

    strict = FakeSession(rows_by_call=[[_server_row()]])
    await load_server_by_code(strict, "demo")
    assert "enabled = 1" in strict.calls[0][0]


async def test_load_enabled_servers_excludes_disabled_rows():
    """启动期加载只取启用行：停用 server 不该在启动探测里被访问。"""
    session = FakeSession(
        rows_by_call=[[_server_row(code="on", enabled=1), _server_row(code="off", enabled=0)]]
    )

    servers = await load_enabled_servers(session)

    assert [server.code for server in servers] == ["on"]
    sql = session.calls[0][0]
    assert "enabled = 1" in sql and "del_flag = '0'" in sql


async def test_probe_and_sync_works_for_disabled_server(monkeypatch):
    """停用 server 也能探测并同步（运营在启用前就要确认“只有只读工具”）。"""
    rpc = FakeRpc(
        {
            "initialize": {"protocolVersion": "2024-11-05"},
            "tools/list": {"tools": [_tool("a")]},
        }
    )
    monkeypatch.setattr(mcp_client, "_post_json", rpc)
    session = FakeSession()

    result = await probe_and_sync(session, _server(enabled=False))

    assert result["ok"] is True and result["synced"] == 1
    assert session.calls[1][1]["status"] == "ok"


async def test_list_synced_tools_returns_camel_case():
    row = {
        "code": "mcp.demo.a",
        "name": "a",
        "description": "说明",
        "read_only": 1,
        "enabled": 0,
        "synced_at": None,
        "server_code": "demo",
    }
    rows = await list_synced_tools(FakeSession(rows_by_call=[[row]]), "demo")
    assert rows[0]["readOnly"] == 1 and rows[0]["enabled"] == 0, "前端 === 1 严格比较，必须回 1/0"
    assert rows[0]["serverCode"] == "demo" and rows[0]["code"] == "mcp.demo.a"


async def test_load_synced_tool_returns_record_or_none():
    session = FakeSession(rows_by_call=[[{"code": "c", "name": "n", "read_only": 1, "enabled": 1}]])
    record = await load_synced_tool(session, 7, "n")
    assert record == {"code": "c", "name": "n", "read_only": True, "enabled": True}
    assert await load_synced_tool(FakeSession(rows_by_call=[[]]), 7, "n") is None


# ====================================================================================
# API：信封 / 鉴权 / 错误映射 / 写工具拒绝
# ====================================================================================
def _build_client(session: Any) -> TestClient:
    app = FastAPI()
    mcp_api.register_error_handlers(app)
    app.include_router(mcp_api.router)

    async def _override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[mcp_api.get_session] = _override
    return TestClient(app)


def test_api_requires_service_secret():
    client = _build_client(FakeSession())
    assert client.post("/agents/mcp/probe/demo").status_code == 401
    assert client.get("/agents/mcp/tools").status_code == 401


def test_api_probe_returns_envelope(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_load(session: Any, code: str, **kwargs: Any) -> McpServer:
        captured.update(kwargs)
        return _server()

    async def fake_probe(session: Any, server: McpServer) -> dict[str, Any]:
        return {
            "ok": True,
            "latencyMs": 12.3,
            "tools": [],
            "rejectedCount": 0,
            "errorType": None,
            "errorMsg": None,
            "synced": 0,
            "disabled": 0,
        }

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "probe_and_sync", fake_probe)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/probe/demo", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200 and body["data"]["ok"] is True
    assert captured == {"include_disabled": True}, "探测必须能操作停用 server"


def test_api_probe_allows_disabled_server(monkeypatch):
    """新增 server 默认 enabled=0：页面点「探测」必须能直接用（否则只能先启用再探测）。"""

    async def fake_load(session: Any, code: str, **kwargs: Any) -> McpServer:
        assert kwargs.get("include_disabled") is True
        return _server(enabled=False)

    async def fake_probe(session: Any, server: McpServer) -> dict[str, Any]:
        assert server.enabled is False
        return {
            "ok": True,
            "latencyMs": 1.0,
            "tools": [],
            "rejectedCount": 0,
            "errorType": None,
            "errorMsg": None,
            "synced": 0,
            "disabled": 0,
        }

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "probe_and_sync", fake_probe)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/probe/demo", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True


def test_api_probe_404_when_server_missing(monkeypatch):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/probe/demo", headers=AUTH_HEADERS)
    assert response.status_code == 404
    assert response.json()["code"] == 404


def test_api_probe_400_for_illegal_code(monkeypatch):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> McpServer:  # pragma: no cover
        raise AssertionError("非法编码不应查库")

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/probe/BAD", headers=AUTH_HEADERS)
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [("timeout", 504), ("http", 502), ("transport", 502), ("jsonrpc", 502)],
)
def test_api_probe_error_mapping(monkeypatch, error_type: str, expected_status: int):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> McpServer:
        return _server()

    async def fake_probe(session: Any, server: McpServer) -> dict[str, Any]:
        raise McpError(error_type, f"{error_type} 失败", 500)

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "probe_and_sync", fake_probe)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/probe/demo", headers=AUTH_HEADERS)
    assert response.status_code == expected_status
    body = response.json()
    assert body["code"] == expected_status
    assert "sql" not in body["msg"].lower()


def test_api_sync_returns_counts(monkeypatch):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> McpServer:
        return _server()

    async def fake_list_tools(server: McpServer) -> list[dict[str, Any]]:
        return [_tool("a"), _tool("b", read_only=False)]

    async def fake_sync(session: Any, server: McpServer, tools: Any) -> dict[str, int]:
        assert len(tools) == 1
        return {"synced": 1, "disabled": 2}

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "list_tools", fake_list_tools)
    monkeypatch.setattr(mcp_api, "sync_server_tools", fake_sync)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/sync/demo", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["data"] == {"synced": 1, "rejected": 1, "disabled": 2}


def test_api_tools_lists_synced(monkeypatch):
    async def fake_list(session: Any, server_code: str | None = None) -> list[dict[str, Any]]:
        assert server_code == "demo"
        return [{"code": "mcp.demo.a", "name": "a", "readOnly": 1, "enabled": 1}]

    monkeypatch.setattr(mcp_api, "list_synced_tools", fake_list)
    client = _build_client(FakeSession())

    response = client.get("/agents/mcp/tools?serverCode=demo", headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["code"] == "mcp.demo.a" and data[0]["readOnly"] == 1


def test_api_call_rejects_write_tool(monkeypatch):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> McpServer:
        return _server()

    async def fake_load_tool(session: Any, server_id: int, tool: str) -> dict[str, Any]:
        return {"code": "mcp.demo.w", "name": "w", "read_only": False, "enabled": True}

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "load_synced_tool", fake_load_tool)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/call/demo/w", json={"arguments": {}}, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert "写工具禁止直调" in response.json()["msg"]


def test_api_call_404_when_tool_not_synced(monkeypatch):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> McpServer:
        return _server()

    async def fake_load_tool(session: Any, server_id: int, tool: str) -> None:
        return None

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "load_synced_tool", fake_load_tool)
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/call/demo/x", json={}, headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_api_call_success(monkeypatch):
    async def fake_load(session: Any, code: str, **_kwargs: Any) -> McpServer:
        return _server()

    async def fake_load_tool(session: Any, server_id: int, tool: str) -> dict[str, Any]:
        return {"code": "mcp.demo.a", "name": "a", "read_only": True, "enabled": True}

    async def fake_call(server: McpServer, name: str, arguments: Any = None) -> dict[str, Any]:
        assert name == "a" and arguments == {"q": "x"}
        return {"text": "结果", "truncated": False, "isError": False}

    monkeypatch.setattr(mcp_api, "load_server_by_code", fake_load)
    monkeypatch.setattr(mcp_api, "load_synced_tool", fake_load_tool)
    monkeypatch.setattr(mcp_api, "call_tool", fake_call)
    client = _build_client(FakeSession())

    response = client.post(
        "/agents/mcp/call/demo/a", json={"arguments": {"q": "x"}}, headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["data"]["text"] == "结果"


def test_api_call_body_validation_maps_400():
    client = _build_client(FakeSession())
    response = client.post(
        "/agents/mcp/call/demo/a", json={"arguments": "not-an-object"}, headers=AUTH_HEADERS
    )
    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_api_mcp_disabled_returns_400(monkeypatch):
    monkeypatch.setenv("DKD_AGENT_MCP_ENABLED", "0")
    get_settings.cache_clear()
    client = _build_client(FakeSession())

    response = client.post("/agents/mcp/probe/demo", headers=AUTH_HEADERS)
    assert response.status_code == 400
    assert "MCP 功能已关闭" in response.json()["msg"]
