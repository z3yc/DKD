"""智能体配置读取层测试（排期 2-3，契约 §5.1/§5.3）。

覆盖三条验收主线：
1. **三重回落**：总开关关闭 / 表缺失或 DB 异常 / JSON 损坏或 runtime 非法 → `None`；
2. **缓存**：TTL 内不查库、TTL 到期回源、invalidate 立即失效；
3. **夹取与场景解析**：数值越界夹取而非 None、`factor_min>=factor_max` 回落默认、
   内置场景 1~4 → chat 行、自定义 ≥101 按 scene、停用行语义（get 可见 / 场景解析不可见）。

纪律（AGENTS §8）：全部用假 loader / 假 sessionmaker，**不连真库**；
默认 loader 的 SQL 也用假 session 捕获后断言（校验“只读、只过滤 del_flag”）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest

from app import config_store
from app.agent_defaults import PARAM_SPECS, RUNTIME_CHAT, RUNTIME_FAULT, RUNTIME_RESTOCK
from app.api import ops
from app.config import get_settings
from app.config_store import (
    AgentConfigSnapshot,
    ConfigStore,
    get_config_store,
    reset_config_store,
    resolve_chat_scene,
)
from app.db import (
    AGENT_OWNED_TABLES,
    AGENT_READ_ONLY_TABLES,
    TableNotAllowedError,
    assert_table_allowed,
)


@pytest.fixture(autouse=True)
def _config_db_enabled(monkeypatch) -> Iterator[None]:
    """本文件显式打开“从 DB 读配置”（conftest 全局钉死为 0，保持既有用例行为不变）。"""
    monkeypatch.setenv("DKD_AGENT_CONFIG_FROM_DB", "1")
    get_settings.cache_clear()
    reset_config_store()
    yield
    reset_config_store()
    get_settings.cache_clear()


def _row(**overrides: object) -> dict[str, object]:
    """一行 `agent_config`（字段名与 DDL/loader 结果一致），按需覆盖。"""
    row: dict[str, object] = {
        "code": "chat",
        "name": "AI 助手（对话载体）",
        "runtime": RUNTIME_CHAT,
        "scene": 1,
        "builtin": 1,
        "system_prompt": "你是帝可得智能助手。",
        "tools": "list_restock_suggestions,get_machine_inventory",
        "model": "",
        "temperature": None,
        "max_tokens": None,
        "timeout_s": 60,
        "max_retries": 2,
        "params_json": None,
        "enabled": 1,
        "version": 3,
    }
    row.update(overrides)
    return row


class FakeLoader:
    """假 loader：记录调用次数，可注入异常（覆盖三重回落的所有失败路径）。"""

    def __init__(self, rows: list[dict[str, object]], error: Exception | None = None) -> None:
        self.rows = list(rows)
        self.error = error
        self.calls = 0

    async def __call__(self) -> list[dict[str, object]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [dict(row) for row in self.rows]


class MissingTableError(Exception):
    """模拟 MySQL 1146：pymysql/SQLAlchemy 的 args[0] 是错误号。"""


# ====================================================================================
# 1. 三重回落
# ====================================================================================
async def test_total_switch_off_returns_none(monkeypatch):
    """总开关关闭 = 与改造前完全一致：连 loader 都不调用。"""
    monkeypatch.setenv("DKD_AGENT_CONFIG_FROM_DB", "0")
    get_settings.cache_clear()
    loader = FakeLoader([_row()])
    store = ConfigStore(loader=loader)

    assert await store.get("chat") is None
    assert await store.get_by_scene(1) is None
    assert await store.list_enabled() == []
    assert loader.calls == 0


async def test_missing_table_falls_back_and_warns_once(caplog):
    """表不存在（旧环境未执行 DDL）：回落 None，且只 WARN 一次（不按 TTL 刷屏）。"""
    loader = FakeLoader([], error=MissingTableError(1146, "Table 'dkd.agent_config' doesn't exist"))
    store = ConfigStore(loader=loader, ttl_s=0.0)

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        assert await store.get("chat") is None
        assert await store.get("restock") is None

    assert loader.calls == 2  # ttl=0 每次都回源，但告警不重复
    messages = [record.getMessage() for record in caplog.records]
    assert len([msg for msg in messages if "表不存在" in msg]) == 1


async def test_db_error_falls_back_with_warning(caplog):
    """DB 不可达/查询异常：同样回落 None + WARN（带异常类型，便于排障）。"""
    loader = FakeLoader([], error=RuntimeError("Can't connect to MySQL server"))
    store = ConfigStore(loader=loader, ttl_s=30.0)

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        assert await store.get("chat") is None

    assert any("RuntimeError" in record.getMessage() for record in caplog.records)


async def test_broken_params_json_returns_none_but_other_rows_survive(caplog):
    """单行 JSON 损坏只影响该行：chat 回落 None，restock 仍可用。"""
    loader = FakeLoader(
        [
            _row(code="chat", params_json='{"quantile": '),
            _row(code="restock", runtime=RUNTIME_RESTOCK, scene=2, params_json='{"quantile": 0.8}'),
        ]
    )
    store = ConfigStore(loader=loader)

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        assert await store.get("chat") is None

    restock = await store.get("restock")
    assert restock is not None
    assert restock.params["quantile"] == 0.8
    assert "params_json" in "".join(record.getMessage() for record in caplog.records)


async def test_params_json_non_object_returns_none():
    """JSON 合法但不是对象（如数组）：语义不可知，按损坏处理 → None。"""
    store = ConfigStore(loader=FakeLoader([_row(params_json="[1, 2, 3]")]))
    assert await store.get("chat") is None


async def test_illegal_runtime_returns_none(caplog):
    """runtime 非 chat/restock/fault：整行无法解释 → None（回落代码默认）。"""
    store = ConfigStore(loader=FakeLoader([_row(runtime="unknown")]))

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        assert await store.get("chat") is None

    assert any("runtime 非法" in record.getMessage() for record in caplog.records)


async def test_illegal_scene_or_version_returns_none():
    assert await ConfigStore(loader=FakeLoader([_row(scene="abc")])).get("chat") is None
    assert await ConfigStore(loader=FakeLoader([_row(version=None)])).get("chat") is None


# ====================================================================================
# 2. 缓存：TTL / invalidate / 版本透传
# ====================================================================================
async def test_cache_hit_does_not_query_within_ttl():
    """同一 TTL 内不查库（契约要求），且返回同一快照对象（含 version）。"""
    loader = FakeLoader([_row(version=7)])
    store = ConfigStore(loader=loader, ttl_s=30.0)

    first = await store.get("chat")
    second = await store.get("chat")

    assert loader.calls == 1
    assert first is second
    assert first is not None and first.version == 7


async def test_cache_refreshes_after_ttl_expiry():
    """TTL 到期后回源：版本变化能被看到（reload 丢失时的兜底通道）。"""
    loader = FakeLoader([_row(version=1)])
    store = ConfigStore(loader=loader, ttl_s=0.0)

    assert (await store.get("chat")).version == 1
    loader.rows = [_row(version=2)]
    assert (await store.get("chat")).version == 2
    assert loader.calls == 2


async def test_invalidate_clears_cache_immediately():
    """reload 接口调用 invalidate 后，下一次读取必须回源（不等 TTL）。"""
    loader = FakeLoader([_row(version=1)])
    store = ConfigStore(loader=loader, ttl_s=30.0)

    assert (await store.get("chat")).version == 1
    store.invalidate()
    loader.rows = [_row(version=2)]
    assert (await store.get("chat")).version == 2
    assert loader.calls == 2

    store.invalidate("chat")
    await store.get("chat")
    assert loader.calls == 3


async def test_version_change_is_logged(caplog):
    """版本比对：配置更新只记一条 INFO（排障时行为变化能对上某次保存）。"""
    loader = FakeLoader([_row(version=3)])
    store = ConfigStore(loader=loader, ttl_s=0.0)

    with caplog.at_level(logging.INFO, logger="dkd.agent.config_store"):
        await store.get("chat")

    assert not [record for record in caplog.records if "配置已更新" in record.getMessage()]

    caplog.clear()
    loader.rows = [_row(version=4)]
    with caplog.at_level(logging.INFO, logger="dkd.agent.config_store"):
        await store.get("chat")

    messages = [
        record.getMessage() for record in caplog.records if "配置已更新" in record.getMessage()
    ]
    assert len(messages) == 1
    assert "version=3→4" in messages[0]


async def test_get_config_store_is_singleton(monkeypatch):
    fake = FakeLoader([_row()])
    monkeypatch.setattr(config_store, "load_config_rows", fake)
    reset_config_store()

    first = await get_config_store()
    second = await get_config_store()

    assert first is second
    assert await second.get("chat") is not None
    assert fake.calls == 1


async def test_snapshot_is_frozen():
    """快照不可变：避免调用方“就地改配置”造成同一进程内行为漂移。"""
    snapshot = AgentConfigSnapshot(
        code="chat",
        name="AI 助手",
        runtime=RUNTIME_CHAT,
        scene=1,
        builtin=True,
        system_prompt="p",
        tools=(),
        model=None,
        temperature=None,
        max_tokens=None,
        timeout_s=60,
        max_retries=2,
        params={},
        enabled=True,
        version=1,
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.version = 2  # type: ignore[misc]


# ====================================================================================
# 3. 数值夹取（不返回 None）与停用行语义
# ====================================================================================
async def test_params_are_clamped_not_dropped(caplog):
    """越界夹取、非法回落默认、矛盾区间回落默认——整份配置仍然可用（不是 None）。"""
    specs = PARAM_SPECS[RUNTIME_RESTOCK]
    loader = FakeLoader(
        [
            _row(
                code="restock",
                runtime=RUNTIME_RESTOCK,
                scene=2,
                params_json=json.dumps(
                    {
                        "quantile": 9,
                        "coverage_days": "oops",
                        "factor_min": 1.8,
                        "factor_max": 1.2,
                        "batch_size": 9999,
                        "unknown_key": 1,
                    }
                ),
            )
        ]
    )
    store = ConfigStore(loader=loader)

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        snapshot = await store.get("restock")

    assert snapshot is not None
    assert snapshot.params["quantile"] == specs["quantile"]["max"]
    assert snapshot.params["coverage_days"] == specs["coverage_days"]["default"]
    # factor_min >= factor_max：两者一起回落默认值，只修一个会让语义更怪
    assert snapshot.params["factor_min"] == specs["factor_min"]["default"]
    assert snapshot.params["factor_max"] == specs["factor_max"]["default"]
    assert snapshot.params["batch_size"] == specs["batch_size"]["max"]
    assert "unknown_key" not in snapshot.params
    assert any("夹取" in record.getMessage() for record in caplog.records)


async def test_null_params_json_uses_defaults():
    store = ConfigStore(loader=FakeLoader([_row(code="restock", runtime=RUNTIME_RESTOCK, scene=2)]))
    snapshot = await store.get("restock")
    assert snapshot is not None
    assert snapshot.params == {
        key: spec["default"] for key, spec in PARAM_SPECS[RUNTIME_RESTOCK].items()
    }


async def test_scalar_fields_are_clamped():
    store = ConfigStore(
        loader=FakeLoader(
            [
                _row(
                    temperature=9.5,
                    max_tokens=1,
                    timeout_s=9999,
                    max_retries=-1,
                    model="  ",
                )
            ]
        )
    )
    snapshot = await store.get("chat")
    assert snapshot is not None
    assert snapshot.temperature == 2.0
    assert snapshot.max_tokens == 256
    assert snapshot.timeout_s == 300
    assert snapshot.max_retries == 0
    assert snapshot.model is None  # 空串 = 未设置 = 走 env 默认


async def test_system_prompt_is_kept_verbatim():
    """system_prompt 是**原文**：首尾空白/换行不得被 strip（2-7 逐字相同验收的前提）。"""
    prompt = "\n  你是助手。\n\n"
    store = ConfigStore(loader=FakeLoader([_row(system_prompt=prompt)]))
    snapshot = await store.get("chat")
    assert snapshot is not None
    assert snapshot.system_prompt == prompt


async def test_disabled_row_visible_to_get_but_not_scene_or_list(caplog):
    """停用行：get 可见（enabled=False，供 chat 400 判断）；场景解析/启用列表不可见。"""
    store = ConfigStore(loader=FakeLoader([_row(enabled=0)]))

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        snapshot = await store.get("chat")

    assert snapshot is not None
    assert snapshot.enabled is False
    assert await store.get_by_scene(1) is None
    assert await store.list_enabled() == []


async def test_list_enabled_filters_and_sorts():
    loader = FakeLoader(
        [
            _row(code="chat", scene=1),
            _row(code="restock", runtime=RUNTIME_RESTOCK, scene=2),
            _row(code="ops_bot", name="运营助手", scene=101, builtin=0),
            _row(code="disabled_bot", scene=102, enabled=0),
        ]
    )
    store = ConfigStore(loader=loader)
    enabled = await store.list_enabled()
    assert [snapshot.code for snapshot in enabled] == ["chat", "restock", "ops_bot"]
    assert all(snapshot.enabled for snapshot in enabled)


# ====================================================================================
# 4. 场景解析（内置 1~4 → chat 行；自定义 ≥101 按 scene）
# ====================================================================================
async def test_resolve_chat_scene_builtin_and_custom(monkeypatch):
    loader = FakeLoader(
        [
            _row(code="chat", scene=1),
            _row(code="ops_bot", name="运营助手", scene=101, builtin=0),
            _row(code="disabled_bot", scene=102, enabled=0),
        ]
    )
    monkeypatch.setattr(config_store, "load_config_rows", loader)
    reset_config_store()

    for scene in (1, 2, 3, 4):
        snapshot = await resolve_chat_scene(scene)
        assert snapshot is not None and snapshot.code == "chat"

    custom = await resolve_chat_scene(101)
    assert custom is not None and custom.code == "ops_bot"

    assert await resolve_chat_scene(102) is None  # 停用行不参与场景解析
    assert await resolve_chat_scene(5) is None  # 5~100 未定义
    assert await resolve_chat_scene(0) is None


async def test_resolve_chat_scene_rejects_non_chat_runtime(monkeypatch, caplog):
    """scene 指向非 chat 载体的行（脏数据）：忽略 + WARN，不把 restock 配置当 chat 用。"""
    loader = FakeLoader([_row(code="weird", runtime=RUNTIME_RESTOCK, scene=150)])
    monkeypatch.setattr(config_store, "load_config_rows", loader)
    reset_config_store()

    with caplog.at_level(logging.WARNING, logger="dkd.agent.config_store"):
        assert await resolve_chat_scene(150) is None

    assert any("非 chat 载体" in record.getMessage() for record in caplog.records)


async def test_fault_runtime_snapshot_keeps_none_prompt():
    """fault 不调 LLM：system_prompt 为 NULL 时快照保持 None（不是空串）。"""
    loader = FakeLoader(
        [_row(code="fault", runtime=RUNTIME_FAULT, scene=3, system_prompt=None, tools="")]
    )
    snapshot = await ConfigStore(loader=loader).get("fault")
    assert snapshot is not None
    assert snapshot.system_prompt is None
    assert snapshot.tools == ()


# ====================================================================================
# 5. 默认 loader：只读 SQL 口径（用假 sessionmaker，绝不连库）
# ====================================================================================
class _FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _FakeSession:
    def __init__(self, captured: list[str], rows: list[dict[str, object]]) -> None:
        self._captured = captured
        self._rows = rows

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, statement: object) -> _FakeResult:
        self._captured.append(str(statement))
        return _FakeResult(self._rows)


async def test_default_loader_sql_is_read_only(monkeypatch):
    """默认 loader：单表 SELECT、只过滤 del_flag（停用行需要被 get 读到，见 Lead 裁定）。"""
    captured: list[str] = []
    monkeypatch.setattr(
        config_store,
        "get_sessionmaker",
        lambda: lambda: _FakeSession(captured, [dict(_row(code="chat"))]),
    )
    store = ConfigStore(ttl_s=30.0)

    snapshot = await store.get("chat")

    assert snapshot is not None
    sql = captured[0].upper()
    assert "FROM AGENT_CONFIG" in sql
    assert "DEL_FLAG = '0'" in sql
    assert "ENABLED = 1" not in sql  # enabled 过滤在代码层（list_enabled/get_by_scene）
    assert "INSERT" not in sql and "UPDATE " not in sql and "DELETE" not in sql


# ====================================================================================
# 6. 只读表白名单（配置只能 Java 写，contract §5.1）
# ====================================================================================
def test_agent_config_tables_are_readable():
    for table in AGENT_READ_ONLY_TABLES:
        assert_table_allowed(table)  # 不抛异常即通过


def test_agent_config_tables_are_not_writable():
    """拒绝路径优先：Python 写配置会绕过 Java 的权限/审计/乐观锁。"""
    for table in sorted(AGENT_READ_ONLY_TABLES):
        with pytest.raises(TableNotAllowedError, match="只读配置表"):
            assert_table_allowed(table, writable=True)


def test_read_only_tables_disjoint_from_owned():
    assert AGENT_READ_ONLY_TABLES.isdisjoint(AGENT_OWNED_TABLES)


# ====================================================================================
# 7. 运维接口：reload / effective（直接调端点函数，避免起服务与连库）
# ====================================================================================
async def test_reload_endpoint_invalidates_cache(monkeypatch):
    loader = FakeLoader([_row(version=1)])
    store = ConfigStore(loader=loader, ttl_s=30.0)

    async def _fake_get_store() -> ConfigStore:
        return store

    monkeypatch.setattr(ops, "get_config_store", _fake_get_store)
    assert (await store.get("chat")).version == 1

    response = await ops.reload_config()

    assert response == {"code": 200, "msg": "ok", "data": {"invalidated": True}}
    loader.rows = [_row(version=9)]
    assert (await store.get("chat")).version == 9
    assert loader.calls == 2


async def test_effective_endpoint_reports_db_source(monkeypatch):
    store = ConfigStore(loader=FakeLoader([_row(version=4)]))

    async def _fake_get_store() -> ConfigStore:
        return store

    monkeypatch.setattr(ops, "get_config_store", _fake_get_store)

    body = await ops.effective_config(code="chat")
    assert body["code"] == 200
    data = body["data"]
    assert isinstance(data, dict) and data["source"] == "db"
    config = data["config"]
    assert isinstance(config, dict)
    assert config["code"] == "chat" and config["version"] == 4
    assert config["system_prompt"] == "你是帝可得智能助手。"


async def test_effective_endpoint_reports_default_source(monkeypatch):
    store = ConfigStore(loader=FakeLoader([]))

    async def _fake_get_store() -> ConfigStore:
        return store

    monkeypatch.setattr(ops, "get_config_store", _fake_get_store)
    body = await ops.effective_config(code="nope")
    assert body["data"] == {"source": "default", "config": None}


def test_config_routes_registered():
    paths = {route.path for route in ops.router.routes}
    assert {"/agents/config/reload", "/agents/config/effective"} <= paths
