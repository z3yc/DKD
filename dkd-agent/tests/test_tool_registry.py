"""工具注册表（`agent_tool`，排期 3-4）测试。

覆盖任务验收线（全部假 loader/假 sessionmaker，**不连真库**，AGENTS §8）：
1. 三重回落：DB 总开关关闭 / 表不存在 / 查询异常 → 全部内置工具启用（含 WARN）；
2. 行过滤：enabled=0、read_only=0、非 chat scope 不进本轮工具集；timeout_ms/row_limit 透传；
3. 启动校验：表存在且有无法映射的 code → RuntimeError（**停用行也算**）；表缺失不拒绝；
4. 缓存：TTL 内不重复查库、invalidate 立即失效；
5. 运行期过滤：`resolve_scene_tools` 对内置 2/3 与自定义 ≥101 都按启用集过滤；
6. /health：只读统计（启用数/降级标记/来源），不触发加载。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.graphs.chat_graph import resolve_scene_tools
from app.services import tool_registry as registry_module
from app.services.tool_registry import (
    ToolEntry,
    ToolRegistry,
    get_tool_registry,
    reset_tool_registry,
    tool_registry_stats,
)

# 与 agent_tool seed（agent_tool_llm_profile.sql + agent_kb_tool_seed.sql）一致的 9 个本地工具
ALL_TOOLS = frozenset(
    {
        "build_device_diagnosis",
        "find_devices",
        "get_machine_inventory",
        "get_sales_summary",
        "list_device_anomalies",
        "list_inflight_work_orders",
        "list_restock_suggestions",
        "search_device_history",
        "search_knowledge_base",
    }
)
RESTOCK_TOOLS = (
    "list_restock_suggestions",
    "get_machine_inventory",
    "list_inflight_work_orders",
    "get_sales_summary",
)


@pytest.fixture(autouse=True)
def _clean_registry_singleton() -> Iterator[None]:
    """单例是进程级状态：每个用例前后重置，避免上一条用例的降级标记串进来。"""
    reset_tool_registry()
    yield
    reset_tool_registry()


def _row(**overrides: Any) -> dict[str, Any]:
    """一行 agent_tool（字段名与 DDL/loader 一致），默认“启用+只读+chat”。"""
    row: dict[str, Any] = {
        "code": "find_devices",
        "name": "设备定位",
        "scope": "chat",
        "source": "local",
        "read_only": 1,
        "enabled": 1,
        "timeout_ms": 10000,
        "row_limit": 2000,
        "builtin": 1,
    }
    row.update(overrides)
    return row


class FakeLoader:
    """假 loader：记录调用次数，可注入异常。"""

    def __init__(
        self, rows: list[dict[str, Any]] | None = None, *, error: Exception | None = None
    ) -> None:
        self.rows = list(rows or [])
        self.error = error
        self.calls = 0

    async def __call__(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [dict(row) for row in self.rows]


class MissingTableError(Exception):
    """模拟 MySQL 1146：错误号在 args[0]。"""


class FakeRegistry:
    """运行期过滤用的假注册表（只实现 list_enabled）。"""

    def __init__(self, codes: set[str]) -> None:
        self.codes = codes
        self.calls = 0

    async def list_enabled(self, *, scope: str = "chat") -> list[SimpleNamespace]:
        self.calls += 1
        return [SimpleNamespace(code=code) for code in sorted(self.codes)]


def _names(entries: list[ToolEntry]) -> set[str]:
    return {entry.code for entry in entries}


# ====================================================================================
# 1. 三重回落
# ====================================================================================
async def test_db_disabled_returns_all_tools_without_loading() -> None:
    """总开关关闭 = 改造前行为：不查库、全部内置工具启用。"""
    loader = FakeLoader([_row(code="find_devices")])
    registry = ToolRegistry(loader=loader, db_enabled=False)

    entries = await registry.list_enabled()

    assert _names(entries) == ALL_TOOLS
    assert loader.calls == 0
    stats = registry.stats()
    assert stats["degraded"] is True
    assert stats["source"] == "default"
    assert stats["enabled"] == len(ALL_TOOLS)


async def test_missing_table_falls_back_and_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    """表不存在（旧环境未执行 DDL）：回落默认，且告警只出现一次（不按 TTL 刷屏）。"""
    loader = FakeLoader(error=MissingTableError(1146, "Table 'dkd.agent_tool' doesn't exist"))
    registry = ToolRegistry(loader=loader, ttl_s=0.0, db_enabled=True)

    with caplog.at_level(logging.WARNING, logger="dkd.agent.services.tool_registry"):
        first = await registry.list_enabled()
        second = await registry.list_enabled()

    assert _names(first) == ALL_TOOLS
    assert _names(second) == ALL_TOOLS
    assert loader.calls == 2  # ttl=0 每次都回源，但“表不存在”告警只发一次
    messages = [record.getMessage() for record in caplog.records]
    assert sum("表不存在" in message for message in messages) == 1
    assert registry.stats()["degraded"] is True


async def test_query_error_falls_back_to_defaults() -> None:
    """DB 不可达等查询异常：同样回落代码默认（只 WARN，不抛穿）。"""
    loader = FakeLoader(error=RuntimeError("connection refused"))
    registry = ToolRegistry(loader=loader, db_enabled=True)

    assert _names(await registry.list_enabled()) == ALL_TOOLS
    stats = registry.stats()
    assert stats["degraded"] is True
    assert stats["source"] == "default"


# ====================================================================================
# 2. 行过滤与字段透传
# ====================================================================================
async def test_disable_readonly_scope_and_declarations() -> None:
    rows = [
        _row(code="find_devices", enabled=1, read_only=1),
        _row(code="list_device_anomalies", enabled=0),  # 停用 → 不注入
        _row(code="build_device_diagnosis", read_only=0),  # 写工具 → 绝不注入模型
        _row(code="get_machine_inventory", scope="restock"),  # 别的载体 → chat 不含
        _row(code="get_sales_summary", scope="all", timeout_ms=5000, row_limit=200),
    ]
    registry = ToolRegistry(loader=FakeLoader(rows), db_enabled=True)

    entries = await registry.list_enabled(scope="chat")

    assert _names(entries) == {"find_devices", "get_sales_summary"}
    by_code = {entry.code: entry for entry in entries}
    assert by_code["get_sales_summary"].timeout_ms == 5000
    assert by_code["get_sales_summary"].row_limit == 200
    assert by_code["find_devices"].read_only is True
    # 降级标记：读到了 DB 行就不算降级（即使有停用行）
    assert registry.stats()["degraded"] is False
    assert registry.stats()["source"] == "db"


# ====================================================================================
# 3. 启动校验
# ====================================================================================
async def test_unknown_code_rejects_startup() -> None:
    """表存在且某行 code 映射不到代码工具 → 拒绝启动（能力缺失必须响亮）。"""
    rows = [_row(code="find_devices"), _row(code="not_implemented_tool")]
    registry = ToolRegistry(loader=FakeLoader(rows), db_enabled=True)

    with pytest.raises(RuntimeError) as exc_info:
        await registry.validate_startup()

    message = str(exc_info.value)
    assert "not_implemented_tool" in message
    assert "拒绝启动" in message


async def test_unknown_code_on_disabled_row_still_rejects_startup() -> None:
    """停用行也参与校验：配置写错但先停用，不应被“停用”掩盖成能启动。"""
    rows = [_row(code="typo_tool", enabled=0)]
    registry = ToolRegistry(loader=FakeLoader(rows), db_enabled=True)

    with pytest.raises(RuntimeError):
        await registry.validate_startup()


async def test_missing_table_does_not_reject_startup() -> None:
    """旧环境（表未建）必须能启动：WARN + 回代码默认，不抛错。"""
    loader = FakeLoader(error=MissingTableError(1146, "no such table"))
    registry = ToolRegistry(loader=loader, db_enabled=True)

    await registry.validate_startup()  # 不抛

    assert registry.stats()["degraded"] is True
    assert _names(await registry.list_enabled()) == ALL_TOOLS


async def test_db_failure_does_not_reject_startup() -> None:
    """DB 不可达同理：注册表是旁路，不能把服务启动拖死。"""
    registry = ToolRegistry(loader=FakeLoader(error=RuntimeError("db down")), db_enabled=True)

    await registry.validate_startup()

    assert registry.stats()["degraded"] is True


async def test_empty_table_warns_and_enables_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """表存在但为空：不拒绝启动，但必须 WARN（多半是漏执行 seed）。"""
    registry = ToolRegistry(loader=FakeLoader([]), db_enabled=True)

    with caplog.at_level(logging.WARNING, logger="dkd.agent.services.tool_registry"):
        await registry.validate_startup()

    assert any("表为空" in record.getMessage() for record in caplog.records)
    assert await registry.list_enabled() == []


# ====================================================================================
# 4. 缓存 TTL / invalidate
# ====================================================================================
async def test_ttl_caches_and_invalidate_reloads() -> None:
    loader = FakeLoader([_row(code="find_devices")])
    registry = ToolRegistry(loader=loader, ttl_s=999.0, db_enabled=True)

    await registry.list_enabled()
    await registry.list_enabled()
    assert loader.calls == 1, "TTL 内不应重复查库"

    registry.invalidate()
    await registry.list_enabled()
    assert loader.calls == 2, "invalidate 后下一次访问必须立即回源"


# ====================================================================================
# 5. 默认 loader 的 SQL 纪律
# ====================================================================================
async def test_default_loader_sql_is_readonly_and_filters_del_flag(monkeypatch) -> None:
    statements: list[str] = []

    class _Result:
        def mappings(self) -> _Result:
            return self

        def all(self) -> list[dict[str, Any]]:
            return [{"code": "find_devices"}]

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def execute(self, statement: Any) -> _Result:
            statements.append(str(statement))
            return _Result()

    class _Maker:
        def __call__(self) -> _Session:
            return _Session()

    monkeypatch.setattr(registry_module, "get_sessionmaker", lambda: _Maker())

    loaded = await registry_module.load_tool_rows()

    assert loaded == [{"code": "find_devices"}]
    sql = statements[0].upper()
    assert "SELECT" in sql
    assert "AGENT_TOOL" in sql
    assert "DEL_FLAG" in sql
    assert "DELETE" not in sql
    assert "UPDATE" not in sql
    assert "INSERT" not in sql


# ====================================================================================
# 6. 运行期过滤（resolve_scene_tools）
# ====================================================================================
def _tools(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=name) for name in names]


async def test_resolve_scene_tools_filters_builtin_by_registry() -> None:
    """内置场景 2/3：候选集不变，但注册表停用的工具不得绑定。"""
    fake = FakeRegistry({"list_restock_suggestions", "get_machine_inventory"})

    kept = await resolve_scene_tools(
        2,
        builtin_tools=lambda scene: _tools(*RESTOCK_TOOLS),
        registry=lambda: _tools(*ALL_TOOLS),
        config_loader=None,
        tool_registry=fake,
    )

    assert [tool.name for tool in kept] == ["list_restock_suggestions", "get_machine_inventory"]


async def test_resolve_scene_tools_filters_custom_by_registry_intersection() -> None:
    """自定义场景：配置 tools ∩ 代码注册表 ∩ 注册表启用（read_only 已由注册表保证）。"""
    snapshot = SimpleNamespace(tools=("find_devices", "get_sales_summary"))
    fake = FakeRegistry({"find_devices"})

    async def loader(_scene: int) -> SimpleNamespace:
        return snapshot

    kept = await resolve_scene_tools(
        101,
        builtin_tools=lambda scene: [],
        registry=lambda: _tools(*ALL_TOOLS),
        config_loader=loader,
        tool_registry=fake,
    )

    assert [tool.name for tool in kept] == ["find_devices"]


async def test_resolve_scene_tools_without_registry_is_unchanged() -> None:
    """不注入注册表 = 改造前行为（单测/旧调用方的安全默认，不做任何过滤）。"""
    kept = await resolve_scene_tools(
        3,
        builtin_tools=lambda scene: _tools("find_devices", "build_device_diagnosis"),
        registry=lambda: _tools(*ALL_TOOLS),
        config_loader=None,
    )

    assert [tool.name for tool in kept] == ["find_devices", "build_device_diagnosis"]


# ====================================================================================
# 7. /health 只读统计 + 单例
# ====================================================================================
def test_health_reports_registry_stats(client: TestClient) -> None:
    """conftest 钉死 CONFIG_FROM_DB=0 → 注册表降级为代码默认，health 必须如实报出来。"""
    body = client.get("/health").json()

    assert body["tool_registry_installed"] is True
    assert body["tool_registry_degraded"] is True
    assert body["tool_registry_source"] == "default"
    assert body["tool_registry_enabled"] == len(ALL_TOOLS)
    assert body["tool_registry_unknown"] == 0


def test_singleton_and_stats_before_creation() -> None:
    assert tool_registry_stats()["installed"] is False

    first = get_tool_registry()
    assert get_tool_registry() is first, "必须是进程单例（provider 与启动校验看到同一份）"
