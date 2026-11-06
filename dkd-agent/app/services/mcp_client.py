"""MCP（Model Context Protocol）HTTP JSON-RPC 客户端与只读工具接入（排期 3-7）。

## 这个模块守的四道闸（本任务的核心，比“能调通”重要）

1. **只读闸**：`tools/list` 里只有 `annotations.readOnlyHint is True` 的工具允许同步/调用；
   缺失该字段或为 false 一律拒绝（**不猜**）。MCP server 自己声明的“读工具”仍可能改数据，
   所以写操作永远只能走 Java 回调（AGENTS §7.3/§7.9）。
2. **超时闸**：单次调用超时 = `min(server.timeout_ms, DKD_AGENT_MCP_MAX_TIMEOUT_MS)`，
   默认硬上限 5000ms——外部 server 卡住不能占住我们的请求/工具轮次。
3. **行数闸**：`tools/call` 返回文本按行数截断到 `min(server.row_limit, 200)`，
   **截断必须显式标注**（`truncated=true` + 正文尾部说明）：静默截断会让模型把前 200 行
   当成全部数据下结论。
4. **密钥闸**：`agent_mcp_server.env_keys_json` 只存 `{HTTP头名: 环境变量名}`；
   本模块只在发请求时按**变量名**取值，密钥值只进请求头，**不进日志、不进异常消息、
   不进探测记录**（AGENTS §7.1）。

## 为什么不用官方 MCP SDK

首期只接 `http` JSON-RPC（initialize / tools/list / tools/call），依赖面要尽量小
（task-19 明确“不新增依赖”）；协议交互集中在 `_rpc()` 一个函数里，
将来换 SDK 或加 SSE/stdio 只改这一处，只读/超时/行数闸与数据落库都不动。

## 数据写入边界

同步写 `agent_mcp_tool` / `agent_tool`（source='mcp'）/ `agent_mcp_probe`，
并更新 `agent_mcp_server.status/last_probe_*`。**全程无 DELETE**：旧工具只 `enabled=0`
（AGENTS §7.4；dkd_agent 账号本身也没有 DELETE 权限）。所有 SQL 全参数化。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import count
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger("dkd.agent.services.mcp")

# ------------------------------------------------------------------------------------
# 常量：协议版本 / 上限 / 支持范围
# ------------------------------------------------------------------------------------
PROTOCOL_VERSION = "2024-11-05"
#: 只支持 HTTP JSON-RPC（stdio 需要拉起子进程，本期不做；遇到明确拒绝而不是静默失败）。
SUPPORTED_TRANSPORTS: frozenset[str] = frozenset({"http"})
#: MCP server 编码规则（与 DDL 注释一致）。
SERVER_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
#: 工具名要能作为 OpenAI function name：`^[a-zA-Z0-9_-]{1,64}$`（含点号的 code 不能直接用）。
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: 探测结果里错误摘要的长度上限（agent_mcp_probe.error_msg 是 VARCHAR(500)）。
MAX_ERROR_MSG_CHARS = 500
#: 单次工具返回的字符硬上限（行数闸之外的第二道保险，防外部 server 返回超大文本）。
MAX_RESULT_CHARS = 100_000
#: 工具描述落库/给模型的长度上限（agent_tool.description VARCHAR(500)）。
MAX_TOOL_DESCRIPTION_CHARS = 500

#: 请求头里的固定项；环境变量注入的头在 `build_auth_headers` 里追加。
_BASE_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

_RPC_IDS = count(1)


class McpError(RuntimeError):
    """MCP 调用失败的统一异常（error_type 决定 API 层映射的状态码）。

    error_type 取值：
      - `timeout`：超时 → API 504；
      - `validation`/`unsupported`/`disabled`/`write_denied`：调用方/配置问题 → API 400；
      - `not_found`：server/工具不存在 → API 404；
      - 其余（`transport`/`http`/`jsonrpc`/`protocol`）：上游失败 → API 502。

    **message 里不得出现密钥值**：本模块所有抛出点都不把 headers 拼进消息。
    """

    def __init__(self, error_type: str, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class McpServer:
    """`agent_mcp_server` 的一行（只放运行期需要的字段）。

    `env_keys`：`{HTTP头名: 环境变量名}` —— 只有变量名，**绝不含密钥值**。
    `timeout_ms` / `row_limit` 仍会在使用时被全局硬上限再夹一次（DB 配置视为不可信）。
    """

    id: int
    code: str
    name: str
    transport: str
    endpoint: str
    env_keys: dict[str, str]
    timeout_ms: int
    row_limit: int
    #: 是否已启用。**探测/同步不要求 enabled=1**（安全流程是“先停用新增 → 探测确认
    #: 只有只读工具 → 再启用”）；启动期加载与日常调用才要求启用。
    enabled: bool = True


# ------------------------------------------------------------------------------------
# 行解析 / 上限夹取
# ------------------------------------------------------------------------------------
def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _clip_text(value: Any, limit: int) -> str | None:
    """截断到列宽上限；截断动作本身记录 WARN（不静默丢内容）。"""
    rendered = _text(value)
    if rendered is None:
        return None
    if len(rendered) <= limit:
        return rendered
    logger.warning("MCP 文本超过长度上限，已截断：limit=%s actual=%s", limit, len(rendered))
    return rendered[:limit]


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, *, default: bool) -> bool:
    """TINYINT/字符串布尔归一化（`"0"` 必须是 False，不能用 bool("0")）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _parse_env_keys(raw: Any, *, code: str) -> dict[str, str]:
    """`env_keys_json` → `{HTTP头名: 环境变量名}`；坏数据只 WARN 并当空处理。

    为什么坏数据不直接抛：该 server 可能是不需要鉴权的公网只读 server；
    配置写坏时让探测给出“上游 401”比在解析阶段崩掉更容易定位。
    """
    payload: Any = raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            payload = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("MCP server env_keys_json 非法 UTF-8，按无鉴权处理：server=%s", code)
            return {}
    if isinstance(payload, str):
        if not payload.strip():
            return {}
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            logger.warning("MCP server env_keys_json 损坏，按无鉴权处理：server=%s", code)
            return {}
    if not isinstance(payload, Mapping):
        if payload is not None:
            logger.warning("MCP server env_keys_json 不是对象，按无鉴权处理：server=%s", code)
        return {}
    env_keys: dict[str, str] = {}
    for header, env_name in payload.items():
        header_name = _text(header)
        variable = _text(env_name)
        if header_name and variable:
            env_keys[header_name] = variable
    return env_keys


def _server_from_row(row: Mapping[str, Any]) -> McpServer | None:
    """原始行 → `McpServer`；缺 code/endpoint 的行无法调用，跳过并 WARN。"""
    code = _text(row.get("code"))
    endpoint = _text(row.get("endpoint"))
    if code is None or endpoint is None:
        logger.warning("agent_mcp_server 行缺少 code/endpoint，已跳过")
        return None
    return McpServer(
        id=_as_int(row.get("id"), 0),
        code=code,
        name=_text(row.get("name")) or code,
        transport=(_text(row.get("transport")) or "http").lower(),
        endpoint=endpoint,
        env_keys=_parse_env_keys(row.get("env_keys_json"), code=code),
        timeout_ms=_as_int(row.get("timeout_ms"), 5000),
        row_limit=_as_int(row.get("row_limit"), 200),
        enabled=_as_bool(row.get("enabled"), default=False),
    )


def _max_timeout_ms() -> int:
    """全局超时硬上限（配置值非法时用 5000，绝不放宽到“无上限”）。"""
    return max(100, _as_int(get_settings().mcp_max_timeout_ms, 5000))


def _max_row_limit() -> int:
    """全局行数硬上限：契约固定 ≤200，配置只能往小调。"""
    return min(max(1, _as_int(get_settings().mcp_max_row_limit, 200)), 200)


def _timeout_s(server: McpServer) -> float:
    """单次调用超时（秒）= min(server.timeout_ms, 全局上限) / 1000。"""
    return min(max(1, server.timeout_ms), _max_timeout_ms()) / 1000.0


def _effective_row_limit(server: McpServer) -> int:
    """单次返回行数上限 = min(server.row_limit, 全局上限)。"""
    return min(max(1, server.row_limit), _max_row_limit())


# ------------------------------------------------------------------------------------
# 鉴权头：只按环境变量名解析
# ------------------------------------------------------------------------------------
def _settings_alias_value(env_name: str) -> str | None:
    """按环境变量名回落到 `Settings` 的同名字段（`validation_alias` 匹配）。

    某些部署把密钥只放进 Settings（.env 由 pydantic-settings 读走，进程环境里没有），
    这里按**变量名**找字段，绝不把值写进日志。
    """
    settings = get_settings()
    for field_name, field_info in type(settings).model_fields.items():
        alias = field_info.validation_alias
        if isinstance(alias, str) and alias == env_name:
            value = getattr(settings, field_name, None)
            return str(value) if value not in (None, "") else None
    return None


def build_auth_headers(
    server: McpServer,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """构造请求头：`{HTTP头名: 环境变量名}` → 从环境按名取值。

    缺变量只 WARN 不抛：公网只读 server 可能无需鉴权；真正需要密钥的会在上游返回 401，
    由 `_post_json` 映射成 `http/401`——错误消息里只有状态码，没有密钥。

    @param server MCP server 配置（`env_keys` 只有变量名）
    @param env 测试注入的环境映射；None = `os.environ`
    @return 完整请求头（含 Content-Type/Accept）
    """
    headers = dict(_BASE_HEADERS)
    source = os.environ if env is None else env
    for header_name, env_name in server.env_keys.items():
        value = source.get(env_name) or _settings_alias_value(env_name)
        if value:
            headers[header_name] = value
        else:
            # 只记变量名，不记值（AGENTS §7.1）
            logger.warning(
                "MCP server 缺少鉴权环境变量，请求头未注入：server=%s header=%s env=%s",
                server.code,
                header_name,
                env_name,
            )
    return headers


# ------------------------------------------------------------------------------------
# HTTP JSON-RPC 传输层（测试 monkeypatch 本函数即可，无需真出网）
# ------------------------------------------------------------------------------------
async def _post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_s: float,
) -> dict[str, Any]:
    """POST 一次 JSON-RPC 并返回已解析的响应体（错误统一转 `McpError`）。"""
    try:
        # trust_env=False：**刻意不读系统/环境代理**。原因（2026-11-04 真机踩到）：
        # 本机 Windows 配了系统代理，httpx 默认会把 127.0.0.1:19090 也发给代理，
        # 代理返回 502，最终表现为“MCP server 不可达/502”——真因与报错相距很远。
        # MCP endpoint 属于运营显式配置的受控出网点，走代理必须是显式决定；
        # 需要代理时应在后续版本显式增加 proxy 配置，而不是继承环境里的隐式代理。
        async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
            response = await client.post(url, json=dict(payload), headers=dict(headers))
    except httpx.TimeoutException as exc:
        # 消息只带超时值，不带 URL（URL 可能含 query 里的 token）与 headers
        raise McpError("timeout", f"调用 MCP server 超时（{timeout_s:.1f}s）", None) from exc
    except httpx.HTTPError as exc:
        raise McpError("transport", f"MCP server 不可达：{type(exc).__name__}", None) from exc
    if response.status_code >= 400:
        raise McpError("http", f"MCP server 返回 HTTP {response.status_code}", response.status_code)
    try:
        body = response.json()
    except ValueError as exc:
        raise McpError("jsonrpc", "MCP server 响应不是合法 JSON", response.status_code) from exc
    if not isinstance(body, dict):
        raise McpError("jsonrpc", "MCP server 响应格式异常（不是 JSON 对象）", response.status_code)
    if body.get("error"):
        error = body["error"]
        message = error.get("message") if isinstance(error, Mapping) else error
        raise McpError(
            "jsonrpc",
            f"MCP server 返回错误：{_clip_text(message, 200) or '未知错误'}",
            response.status_code,
        )
    return body


async def _rpc(
    server: McpServer,
    method: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """发一次 JSON-RPC 调用并取 `result`（`initialize`/`tools/list`/`tools/call` 共用）。"""
    payload = {
        "jsonrpc": "2.0",
        "id": next(_RPC_IDS),
        "method": method,
        "params": dict(params),
    }
    body = await _post_json(
        server.endpoint, payload, build_auth_headers(server), _timeout_s(server)
    )
    result = body.get("result")
    if not isinstance(result, dict):
        raise McpError("protocol", f"JSON-RPC 响应缺少 result：method={method}", None)
    return result


async def initialize(server: McpServer) -> dict[str, Any]:
    """MCP 握手（`initialize`）；协议版本不一致只 WARN，不中断探测。"""
    result = await _rpc(
        server,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "dkd-agent", "version": "0.1.0"},
        },
    )
    negotiated = _text(result.get("protocolVersion"))
    if negotiated and negotiated != PROTOCOL_VERSION:
        logger.warning(
            "MCP protocolVersion 不一致（server=%s got=%s want=%s），按兼容模式继续",
            server.code,
            negotiated,
            PROTOCOL_VERSION,
        )
    return result


async def list_tools(server: McpServer) -> list[dict[str, Any]]:
    """拉取工具清单（原始项，未做只读过滤）。"""
    result = await _rpc(server, "tools/list", {})
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list):
        raise McpError("protocol", "tools/list 响应缺少 tools 数组", None)
    return [dict(tool) for tool in raw_tools if isinstance(tool, Mapping)]


def is_read_only_tool(tool: Mapping[str, Any]) -> bool:
    """只读闸：**必须**显式 `annotations.readOnlyHint is True`（缺失/false 一律拒绝）。

    为什么“缺失 = 拒绝”而不是“默认只读”：MCP server 是外部系统，声明不全时
    猜成只读等于把写工具放进模型可调用集合——这正是 3-7 要防的事故。
    """
    annotations = tool.get("annotations")
    if not isinstance(annotations, Mapping):
        return False
    return annotations.get("readOnlyHint") is True


def split_read_only_tools(
    tools: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把工具清单拆成（允许同步的只读工具, 被拒绝的其余工具）。"""
    allowed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for tool in tools:
        if is_read_only_tool(tool):
            allowed.append(dict(tool))
        else:
            rejected.append(dict(tool))
    return allowed, rejected


# ------------------------------------------------------------------------------------
# tools/call：结果提取 + 行数截断
# ------------------------------------------------------------------------------------
def _extract_result_text(result: Mapping[str, Any]) -> str:
    """MCP `tools/call` 结果 → 文本：拼接 `content[].text`，无文本时回退整段 JSON。"""
    content = result.get("content")
    texts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                value = item.get("text")
                if value is not None:
                    texts.append(str(value))
    if texts:
        return "\n".join(texts)
    return json.dumps(result, ensure_ascii=False, default=str)


def _truncate_rows(text_value: str, row_limit: int) -> tuple[str, bool]:
    """按行截断并**显式标注**；返回（截断后文本, 是否截断）。"""
    lines = text_value.splitlines() or [""]
    truncated = len(lines) > row_limit or len(text_value) > MAX_RESULT_CHARS
    if len(lines) > row_limit:
        text_value = "\n".join(lines[:row_limit]) + (
            f"\n…（已截断：共 {len(lines)} 行，仅返回前 {row_limit} 行）"
        )
    if len(text_value) > MAX_RESULT_CHARS:
        text_value = text_value[:MAX_RESULT_CHARS] + "\n…（已截断：内容超过字符上限）"
    return text_value, truncated


async def call_tool(
    server: McpServer,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """调用一个工具并返回 `{text, truncated, isError}`（行数/字符双重截断）。"""
    if not TOOL_NAME_PATTERN.match(tool_name or ""):
        raise McpError("validation", f"工具名非法：{tool_name!r}", None)
    result = await _rpc(
        server,
        "tools/call",
        {"name": tool_name, "arguments": dict(arguments or {})},
    )
    text_value, truncated = _truncate_rows(
        _extract_result_text(result), _effective_row_limit(server)
    )
    return {
        "text": text_value,
        "truncated": truncated,
        "isError": bool(result.get("isError")),
    }


# ------------------------------------------------------------------------------------
# 探测 / 同步 / 落库
# ------------------------------------------------------------------------------------
async def probe_server(server: McpServer) -> dict[str, Any]:
    """探测一个 MCP server：握手 + 拉工具清单 + 只读过滤。

    不抛异常（探测失败也是结果）：返回
    `{ok, latencyMs, tools, rejectedCount, errorType, errorMsg}`，
    其中 `tools` 是**只读允许清单**（未过滤的原始清单不返回，避免调用方误用）。

    @return 探测结果；`errorMsg` 已截断且不含密钥
    """
    started = time.perf_counter()
    try:
        if not get_settings().mcp_enabled:
            raise McpError("disabled", "MCP 功能已关闭（DKD_AGENT_MCP_ENABLED=false）", None)
        if server.transport not in SUPPORTED_TRANSPORTS:
            raise McpError(
                "unsupported",
                f"不支持的 transport：{server.transport}（首期仅 http JSON-RPC）",
                None,
            )
        await initialize(server)
        raw_tools = await list_tools(server)
        allowed, rejected = split_read_only_tools(raw_tools)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info(
            "MCP 探测成功：server=%s tools=%s rejected=%s latency_ms=%s",
            server.code,
            len(allowed),
            len(rejected),
            latency_ms,
        )
        return {
            "ok": True,
            "latencyMs": latency_ms,
            "tools": allowed,
            "rejectedCount": len(rejected),
            "errorType": None,
            "errorMsg": None,
        }
    except McpError as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        # 只记类型/状态码/消息；消息由本模块构造，不含密钥
        logger.warning(
            "MCP 探测失败：server=%s type=%s status=%s msg=%s",
            server.code,
            exc.error_type,
            exc.http_status,
            exc.message,
        )
        return {
            "ok": False,
            "latencyMs": latency_ms,
            "tools": [],
            "rejectedCount": 0,
            "errorType": exc.error_type,
            "errorMsg": _clip_text(exc.message, MAX_ERROR_MSG_CHARS),
        }
    except Exception as exc:  # noqa: BLE001 —— 探测不得抛穿（调用方要写一条失败记录）
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.warning(
            "MCP 探测异常：server=%s err=%s", server.code, type(exc).__name__, exc_info=True
        )
        return {
            "ok": False,
            "latencyMs": latency_ms,
            "tools": [],
            "rejectedCount": 0,
            "errorType": type(exc).__name__,
            "errorMsg": "MCP 探测异常，详见服务日志",
        }


# -- SQL（全参数化；无 DELETE）--------------------------------------------------------
_LOAD_SERVER_SQL = """
SELECT id, code, name, transport, endpoint, env_keys_json, enabled, timeout_ms, row_limit
  FROM agent_mcp_server
 WHERE code = :code
   AND enabled = 1
   AND del_flag = '0'
"""

# 探测/同步专用：**不过滤 enabled**（安全流程：先停用新增 → 探测确认只读 → 再启用）
_LOAD_SERVER_INCLUDE_DISABLED_SQL = """
SELECT id, code, name, transport, endpoint, env_keys_json, enabled, timeout_ms, row_limit
  FROM agent_mcp_server
 WHERE code = :code
   AND del_flag = '0'
"""

# 启动期加载：只取启用行（停用 server 不参与启动探测）
_LOAD_ENABLED_SERVERS_SQL = """
SELECT id, code, name, transport, endpoint, env_keys_json, enabled, timeout_ms, row_limit
  FROM agent_mcp_server
 WHERE enabled = 1
   AND del_flag = '0'
 ORDER BY code
"""

_UPSERT_MCP_TOOL_SQL = """
INSERT INTO agent_mcp_tool
    (server_id, code, name, description, input_schema, read_only, enabled, synced_at)
VALUES
    (:server_id, :code, :name, :description, :input_schema, 1, 1, NOW())
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    input_schema = VALUES(input_schema),
    read_only = 1,
    enabled = 1,
    synced_at = NOW()
"""

_UPSERT_AGENT_TOOL_SQL = """
INSERT INTO agent_tool
    (code, name, description, scope, source, read_only, enabled, timeout_ms, row_limit,
     builtin, server_id, input_schema_json, create_by, create_time, update_time, del_flag)
VALUES
    (:code, :name, :description, :scope, 'mcp', 1, 1, :timeout_ms, :row_limit,
     0, :server_id, :input_schema_json, :operator, NOW(), NOW(), '0')
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    scope = VALUES(scope),
    source = 'mcp',
    read_only = 1,
    enabled = 1,
    timeout_ms = VALUES(timeout_ms),
    row_limit = VALUES(row_limit),
    server_id = VALUES(server_id),
    input_schema_json = VALUES(input_schema_json),
    update_time = NOW(),
    del_flag = '0'
"""

_DISABLE_MCP_TOOLS_SQL = """
UPDATE agent_mcp_tool
   SET enabled = 0
 WHERE server_id = :server_id
"""

_DISABLE_MCP_TOOLS_STALE_SQL = """
UPDATE agent_mcp_tool
   SET enabled = 0
 WHERE server_id = :server_id
   AND code NOT IN :codes
"""

_DISABLE_AGENT_TOOLS_SQL = """
UPDATE agent_tool
   SET enabled = 0,
       update_time = NOW()
 WHERE source = 'mcp'
   AND server_id = :server_id
"""

_DISABLE_AGENT_TOOLS_STALE_SQL = """
UPDATE agent_tool
   SET enabled = 0,
       update_time = NOW()
 WHERE source = 'mcp'
   AND server_id = :server_id
   AND code NOT IN :codes
"""


def _tool_schema(tool: Mapping[str, Any]) -> dict[str, Any] | None:
    schema = tool.get("inputSchema") or tool.get("input_schema")
    return dict(schema) if isinstance(schema, Mapping) else None


async def sync_server_tools(
    session: AsyncSession,
    server: McpServer,
    tools: Sequence[Mapping[str, Any]],
    *,
    operator: str = "mcp-sync",
) -> dict[str, int]:
    """把只读工具同步进 `agent_mcp_tool` 与 `agent_tool`（source='mcp'）。

    纪律：
    - 只读闸在**这里再过滤一次**（调用方已过滤，但不依赖调用方）；
    - code 固定 `mcp.<server>.<tool>`，`scope='chat'`、`builtin=0`、`read_only=1`；
    - timeout_ms/row_limit 取 server 声明与全局硬上限的更小值（DB 配置视为不可信）；
    - 本次清单里没有的旧工具 → `enabled=0`，**不 DELETE**。

    @return `{synced, disabled}`；`disabled` 为两张表被停用的行数合计
    """
    allowed = [tool for tool in tools if is_read_only_tool(tool)]
    timeout_ms = min(max(1, server.timeout_ms), _max_timeout_ms())
    row_limit = min(max(1, server.row_limit), _max_row_limit())
    codes: list[str] = []
    for tool in allowed:
        tool_name = _text(tool.get("name"))
        if tool_name is None or not TOOL_NAME_PATTERN.match(tool_name):
            logger.warning("MCP 工具名非法，跳过同步：server=%s tool=%r", server.code, tool_name)
            continue
        code = f"mcp.{server.code}.{tool_name}"
        codes.append(code)
        schema = _tool_schema(tool)
        schema_json = json.dumps(schema, ensure_ascii=False) if schema is not None else None
        description = _clip_text(tool.get("description"), MAX_TOOL_DESCRIPTION_CHARS)
        params = {
            "server_id": server.id,
            "code": code,
            "name": tool_name,
            "description": description,
            "input_schema": schema_json,
            "input_schema_json": schema_json,
            "timeout_ms": timeout_ms,
            "row_limit": row_limit,
            "scope": "chat",
            "operator": operator,
        }
        await session.execute(text(_UPSERT_MCP_TOOL_SQL), params)
        await session.execute(text(_UPSERT_AGENT_TOOL_SQL), params)

    disabled = 0
    if codes:
        mcp_stmt = text(_DISABLE_MCP_TOOLS_STALE_SQL).bindparams(bindparam("codes", expanding=True))
        agent_stmt = text(_DISABLE_AGENT_TOOLS_STALE_SQL).bindparams(
            bindparam("codes", expanding=True)
        )
        mcp_result = await session.execute(mcp_stmt, {"server_id": server.id, "codes": codes})
        agent_result = await session.execute(agent_stmt, {"server_id": server.id, "codes": codes})
    else:
        # 清单里一个只读工具都没有：该 server 的历史工具全部停用（下次可恢复）
        mcp_result = await session.execute(text(_DISABLE_MCP_TOOLS_SQL), {"server_id": server.id})
        agent_result = await session.execute(
            text(_DISABLE_AGENT_TOOLS_SQL), {"server_id": server.id}
        )
    disabled += int(getattr(mcp_result, "rowcount", 0) or 0)
    disabled += int(getattr(agent_result, "rowcount", 0) or 0)
    await session.commit()
    logger.info(
        "MCP 工具同步完成：server=%s synced=%s disabled=%s", server.code, len(codes), disabled
    )
    return {"synced": len(codes), "disabled": disabled}


async def sync_server(
    session: AsyncSession,
    server: McpServer,
    *,
    operator: str = "mcp-sync",
) -> dict[str, int]:
    """只同步（不写探测记录）：拉清单 → 只读过滤 → 落库，返回 `{synced, rejected, disabled}`。"""
    raw_tools = await list_tools(server)
    allowed, rejected = split_read_only_tools(raw_tools)
    counts = await sync_server_tools(session, server, allowed, operator=operator)
    return {"synced": counts["synced"], "rejected": len(rejected), "disabled": counts["disabled"]}


_INSERT_PROBE_SQL = """
INSERT INTO agent_mcp_probe
    (server_id, ok, latency_ms, tool_count, error_type, error_msg, create_time)
VALUES
    (:server_id, :ok, :latency_ms, :tool_count, :error_type, :error_msg, NOW(3))
"""

_UPDATE_SERVER_STATUS_SQL = """
UPDATE agent_mcp_server
   SET status = :status,
       last_probe_time = NOW(3),
       last_probe_msg = :last_probe_msg,
       update_time = NOW()
 WHERE id = :server_id
   AND del_flag = '0'
"""


async def probe_and_sync(
    session: AsyncSession,
    server: McpServer,
    *,
    operator: str = "mcp-sync",
) -> dict[str, Any]:
    """探测 + （成功时）同步 + 写探测记录 + 更新 server 状态（一次事务）。

    为什么失败也写记录/更新状态：运营在页面上要能看到“上次探测为什么失败”，
    且失败记录是 6-2 告警的数据源；只写成功等于把故障藏起来。
    """
    probe = await probe_server(server)
    ok = bool(probe["ok"])
    tool_count = len(probe["tools"])
    error_type = _clip_text(probe["errorType"], 64)
    error_msg = _clip_text(probe["errorMsg"], MAX_ERROR_MSG_CHARS)
    status = "ok" if ok else "error"
    last_probe_msg = (
        f"探测成功：{tool_count} 个只读工具"
        if ok
        else f"探测失败（{error_type}）：{error_msg or '未知原因'}"
    )

    await session.execute(
        text(_INSERT_PROBE_SQL),
        {
            "server_id": server.id,
            "ok": 1 if ok else 0,
            "latency_ms": int(round(float(probe["latencyMs"] or 0))),
            "tool_count": tool_count,
            "error_type": error_type,
            "error_msg": error_msg,
        },
    )
    await session.execute(
        text(_UPDATE_SERVER_STATUS_SQL),
        {
            "server_id": server.id,
            "status": status,
            "last_probe_msg": _clip_text(last_probe_msg, MAX_ERROR_MSG_CHARS),
        },
    )
    result = dict(probe)
    if ok:
        counts = await sync_server_tools(session, server, probe["tools"], operator=operator)
        result.update({"synced": counts["synced"], "disabled": counts["disabled"]})
    else:
        # 失败不改动已同步工具（保留上一次成功的结果，页面仍可用）
        await session.commit()
        result.update({"synced": 0, "disabled": 0})
    logger.info(
        "MCP 探测并同步完成：server=%s ok=%s synced=%s",
        server.code,
        ok,
        result.get("synced"),
    )
    return result


async def load_server_by_code(
    session: AsyncSession, code: str, *, include_disabled: bool = False
) -> McpServer | None:
    """按 code 读 server；`include_disabled=True` 时**包含已停用行**。

    为什么默认只读启用行、但探测/同步必须能读停用行：
    安全流程是「新增默认 `enabled=0` → 探测确认只读工具 → 人工复核后启用」——
    若探测也被 enabled 挡住，运营永远无法在启用前验证，只能“先启用再探测”，
    安全闸形同虚设。运行期调用（`/call`）与启动加载仍只认启用行。

    @param include_disabled 是否包含 `enabled=0` 的行（探测/同步传 True）
    @return 不存在/已软删 → None
    """
    sql = _LOAD_SERVER_INCLUDE_DISABLED_SQL if include_disabled else _LOAD_SERVER_SQL
    rows = (await session.execute(text(sql), {"code": code})).mappings().all()
    if not rows:
        return None
    return _server_from_row(rows[0])


async def load_enabled_servers(session: AsyncSession) -> list[McpServer]:
    """启动期加载**全部启用**的 MCP server（停用行不参与启动探测）。

    SQL 已过滤 `enabled=1`，这里再按解析出的 `enabled` 过滤一次：
    真实库不会出现不一致，但假 loader/历史脏数据下“启动期不探测停用 server”是安全口径，
    不能在数据边界上赌。
    """
    rows = (await session.execute(text(_LOAD_ENABLED_SERVERS_SQL))).mappings().all()
    servers: list[McpServer] = []
    for row in rows:
        server = _server_from_row(row)
        if server is not None and server.enabled:
            servers.append(server)
    return servers


# ------------------------------------------------------------------------------------
# 已同步工具查询（API「工具列表」与「调用前只读校验」）
# ------------------------------------------------------------------------------------
_SELECT_SYNCED_TOOLS_SQL = """
SELECT t.code, t.name, t.description, t.read_only, t.enabled, t.synced_at,
       s.code AS server_code
  FROM agent_mcp_tool t
  JOIN agent_mcp_server s ON s.id = t.server_id
 WHERE s.del_flag = '0'
 ORDER BY t.code
"""

_SELECT_SYNCED_TOOLS_BY_SERVER_SQL = """
SELECT t.code, t.name, t.description, t.read_only, t.enabled, t.synced_at,
       s.code AS server_code
  FROM agent_mcp_tool t
  JOIN agent_mcp_server s ON s.id = t.server_id
 WHERE s.del_flag = '0'
   AND s.code = :server_code
 ORDER BY t.code
"""

_SELECT_SYNCED_TOOL_SQL = """
SELECT code, name, read_only, enabled
  FROM agent_mcp_tool
 WHERE server_id = :server_id
   AND (name = :tool_name OR code = :tool_name)
 LIMIT 1
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat(sep=" ", timespec="seconds") if callable(isoformat) else str(value)


def _synced_tool_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """已同步工具行（**布尔字段用 1/0**：前端表格用 `row.readOnly === 1` 严格比较）。"""
    return {
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "description": row.get("description"),
        "readOnly": 1 if bool(row.get("read_only")) else 0,
        "enabled": 1 if bool(row.get("enabled")) else 0,
        "serverCode": row.get("server_code"),
        "syncedAt": _iso(row.get("synced_at")),
    }


async def list_synced_tools(
    session: AsyncSession, server_code: str | None = None
) -> list[dict[str, Any]]:
    """已同步的 MCP 工具列表（单查询，不在循环里查 server——无 N+1）。"""
    if server_code:
        rows = (
            (
                await session.execute(
                    text(_SELECT_SYNCED_TOOLS_BY_SERVER_SQL), {"server_code": server_code}
                )
            )
            .mappings()
            .all()
        )
    else:
        rows = (await session.execute(text(_SELECT_SYNCED_TOOLS_SQL))).mappings().all()
    return [_synced_tool_row(row) for row in rows]


async def load_synced_tool(
    session: AsyncSession, server_id: int, tool_name: str
) -> dict[str, Any] | None:
    """按 server + 工具名（或完整 code）读同步记录；API 调用前用它做只读校验。"""
    rows = (
        (
            await session.execute(
                text(_SELECT_SYNCED_TOOL_SQL),
                {"server_id": server_id, "tool_name": tool_name},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "read_only": bool(row.get("read_only")),
        "enabled": bool(row.get("enabled")),
    }


# ------------------------------------------------------------------------------------
# LangChain 工具包装：只读工具 → StructuredTool
# ------------------------------------------------------------------------------------
_PRIMITIVE_TYPES: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}
_SAFE_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _python_type(spec: Mapping[str, Any]) -> tuple[type, bool]:
    """JSON Schema 基本类型 → Python 类型；复杂类型回 str（返回是否被降级）。"""
    declared = _text(spec.get("type")) or "string"
    python_type = _PRIMITIVE_TYPES.get(declared)
    if python_type is None:
        return str, True
    return python_type, False


def build_args_schema(tool_name: str, input_schema: Mapping[str, Any] | None) -> type[BaseModel]:
    """`inputSchema.properties` → pydantic 模型（StructuredTool 的 args_schema）。

    支持的 JSON Schema 类型：string/number/integer/boolean；数组/对象等复杂类型**降级为
    字符串**并在字段描述里注明（模型传 JSON 字符串，`tools/call` 原样转发给 server）。

    字段名必须是合法 Python 标识符（pydantic 的限制）：非法字段名**直接跳过并 WARN**，
    而不是改名——改名会让参数与 server 期望的名字对不上，调用必然失败且很难查。
    """
    properties: Mapping[str, Any] = {}
    required: set[str] = set()
    if isinstance(input_schema, Mapping):
        raw_properties = input_schema.get("properties")
        if isinstance(raw_properties, Mapping):
            properties = raw_properties
        raw_required = input_schema.get("required")
        if isinstance(raw_required, Sequence) and not isinstance(raw_required, str):
            required = {str(item) for item in raw_required}

    fields: dict[str, Any] = {}
    for field_name, spec in properties.items():
        name = str(field_name)
        if not _SAFE_FIELD_NAME.match(name):
            logger.warning(
                "MCP 工具参数名不是合法 Python 标识符，已跳过：tool=%s field=%s",
                tool_name,
                name,
            )
            continue
        spec_mapping = spec if isinstance(spec, Mapping) else {}
        python_type, degraded = _python_type(spec_mapping)
        description = _text(spec_mapping.get("description")) or None
        if degraded:
            note = "（复杂类型，按 JSON 字符串传入）"
            description = f"{description}{note}" if description else note
        if name in required:
            fields[name] = (python_type, Field(..., description=description))
        else:
            fields[name] = (python_type | None, Field(default=None, description=description))

    model_name = re.sub(r"[^A-Za-z0-9_]", "_", tool_name) or "McpTool"
    return create_model(f"{model_name}_Args", **fields)  # type: ignore[call-overload]


def _tool_coroutine(server: McpServer, tool_name: str) -> Callable[..., Awaitable[str]]:
    """构造某个工具的调用协程。

    为什么用工厂函数而不是在循环里直接定义闭包：`async def` 在循环里会**晚绑定**
    循环变量——所有工具最后都会调用最后一个工具名（经典 late-binding 陷阱，
    表现为“调 A 却打到 B”这种极难排查的错）。
    """

    async def _coroutine(**kwargs: Any) -> str:
        result = await call_tool(server, tool_name, kwargs)
        text_value = str(result.get("text") or "")
        # 只记元数据（长度/是否错误/是否截断），不记返回正文：MCP 返回可能含业务数据。
        logger.info(
            "MCP 工具调用完成：server=%s tool=%s text_len=%d is_error=%s truncated=%s",
            server.code,
            tool_name,
            len(text_value),
            bool(result.get("isError")),
            bool(result.get("truncated")),
        )
        if result.get("isError"):
            return f"工具执行失败：{text_value}"
        return text_value

    return _coroutine


def build_langchain_tools(
    server: McpServer,
    tools: Sequence[Mapping[str, Any]],
) -> list[StructuredTool]:
    """把**只读** MCP 工具包装成 LangChain `StructuredTool`（写工具永不提供）。

    - 工具名必须能作为 OpenAI function name（`^[a-zA-Z0-9_-]{1,64}$`）；
      `agent_tool.code` 里的点号不能进 function name，因此这里用原始 tool 名；
    - coroutine 调 `tools/call`，返回文本（超过行数上限时带截断说明）；
    - 工具级超时/行数由 `McpServer` 声明与全局硬上限共同夹取（见 `call_tool`）。

    @return StructuredTool 列表（顺序与入参一致；被拒绝/非法的工具静默跳过并 WARN）
    """
    built: list[StructuredTool] = []
    for tool in tools:
        if not is_read_only_tool(tool):
            logger.warning("MCP 工具不是显式只读，拒绝包装：server=%s", server.code)
            continue
        name = _text(tool.get("name"))
        if name is None or not TOOL_NAME_PATTERN.match(name):
            logger.warning("MCP 工具名非法，拒绝包装：server=%s tool=%r", server.code, name)
            continue
        schema = _tool_schema(tool)
        args_schema = build_args_schema(name, schema)
        description = _clip_text(tool.get("description"), MAX_TOOL_DESCRIPTION_CHARS) or name
        description = f"{description}（外部 MCP 只读工具；返回超过行数上限会截断并标注）"

        built.append(
            StructuredTool.from_function(
                coroutine=_tool_coroutine(server, name),
                name=name,
                description=description,
                args_schema=args_schema,
            )
        )
    return built


__all__ = [
    "MAX_RESULT_CHARS",
    "McpError",
    "McpServer",
    "PROTOCOL_VERSION",
    "SERVER_CODE_PATTERN",
    "SUPPORTED_TRANSPORTS",
    "TOOL_NAME_PATTERN",
    "build_args_schema",
    "build_auth_headers",
    "build_langchain_tools",
    "call_tool",
    "initialize",
    "is_read_only_tool",
    "list_synced_tools",
    "list_tools",
    "load_enabled_servers",
    "load_server_by_code",
    "load_synced_tool",
    "probe_and_sync",
    "probe_server",
    "split_read_only_tools",
    "sync_server",
    "sync_server_tools",
]
