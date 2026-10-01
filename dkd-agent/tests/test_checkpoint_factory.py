"""checkpointer 后端工厂与配置校验 —— **零外部依赖**契约测试（评审稿 §6 的 T3）。

为什么必须有一组「不连真库」的用例（AGENTS §8 + 评审稿 §6）：
PG 真库路径只能标 `-m live` 人工触发，若默认门禁完全覆盖不到这条新增路径，
那么「配置写错」「依赖没装」「口令被打印到日志」这三类**最常见也最危险**的失败
就只会在上线当天被发现。本文件专测这三类，不碰任何数据库。

覆盖清单：
  1. 工厂分派（默认 sqlite / 显式 postgres）
  2. 配置拒绝路径（未知后端、选 postgres 但缺 DSN）
  3. `dsn` 与 `pg_dsn` 不串线（MySQL 只读账号**绝不能**因为新增 PG 字段而连错库）
  4. 生命周期合同（未 open 取 saver 必须抛错、close 幂等、close 后 saver 不可用）
  5. 依赖缺失时的可执行报错（模拟 ImportError）
  6. 口令脱敏（含「无法安全脱敏」兜底）+ `/health` 不泄露凭据
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.checkpoint import (
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    CheckpointStore,
    PostgresCheckpointStore,
    SqliteCheckpointStore,
    build_checkpoint_store,
    mask_dsn,
)
from app.config import Settings

# 测试用连接串：口令是**假值**，仅用于断言脱敏行为（AGENTS §7.1：真实口令只入 .env）
PG_DSN = "postgresql://agent_writer:s3cr3t-pass@127.0.0.1:5432/dkd_agent"


def _settings(**overrides: str) -> Settings:
    """构造 Settings：显式传参优先，避免被本机 `.env` 影响。"""
    base = {
        "DKD_AGENT_CHECKPOINT_BACKEND": "sqlite",
        "DKD_AGENT_SQLITE_PATH": "var/test-checkpoints.db",
        "DKD_AGENT_PG_DSN": "",
    }
    base.update(overrides)
    return Settings(**base)


# --- 1. 工厂分派 -------------------------------------------------------------


def test_default_backend_is_sqlite():
    """默认必须是 sqlite：切换后端是显式动作，不能“顺手”就变了存储位置。"""
    settings = _settings()
    assert settings.checkpoint_backend == BACKEND_SQLITE

    store = build_checkpoint_store(settings)
    assert isinstance(store, SqliteCheckpointStore)
    assert store.backend == BACKEND_SQLITE


def test_postgres_backend_dispatch():
    """显式选 postgres 时工厂必须给出 PG 实现（而不是静默回退 sqlite）。"""
    settings = _settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN=PG_DSN)
    store = build_checkpoint_store(settings)
    assert isinstance(store, PostgresCheckpointStore)
    assert store.backend == BACKEND_POSTGRES


def test_backend_value_is_normalized():
    """大小写/空白容错（部署时环境变量手写很常见）。"""
    settings = _settings(DKD_AGENT_CHECKPOINT_BACKEND="  POSTGRES  ", DKD_AGENT_PG_DSN=PG_DSN)
    assert settings.checkpoint_backend == BACKEND_POSTGRES


# --- 2. 配置拒绝路径 ---------------------------------------------------------


def test_unknown_backend_rejected():
    """拼错后端名必须启动即失败，而不是静默回退 sqlite。

    回退的后果：运维以为已切到 PG，直到多实例部署发现会话不共享才回头查——
    「看起来成功了」的错配比启动失败昂贵得多。
    """
    with pytest.raises(ValidationError) as exc:
        _settings(DKD_AGENT_CHECKPOINT_BACKEND="postgresql")
    assert "sqlite 或 postgres" in str(exc.value)


def test_postgres_without_dsn_rejected():
    with pytest.raises(ValidationError) as exc:
        _settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN="")
    # 报错必须点名缺哪个变量，否则运维只知道“起不来”
    assert "DKD_AGENT_PG_DSN" in str(exc.value)


# --- 3. dsn / pg_dsn 不串线（真实事故级回归锚点） -----------------------------


def test_mysql_dsn_unaffected_by_pg_dsn():
    """`dsn`（MySQL 只读账号）绝不能因为配了 `pg_dsn` 而改变。

    这是本轮新增字段最容易埋的坑：两个字段一旦同名或互相赋值，
    `app/db.py` 会静默连到 PG，业务读取全部落到一个没有业务表的库上，
    报错信息只会是「表不存在」，几乎不可能联想到是连接串串线。
    """
    settings = _settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN=PG_DSN)
    mysql_dsn = settings.dsn
    assert mysql_dsn.startswith("mysql+aiomysql://")
    assert "dkd_agent" in mysql_dsn  # MySQL 只读账号名仍在
    assert "postgresql" not in mysql_dsn
    assert settings.pg_dsn == PG_DSN


# --- 4. 生命周期合同 ---------------------------------------------------------


def _sqlite_store() -> SqliteCheckpointStore:
    return SqliteCheckpointStore(Path("var/x.db"))


def _pg_store() -> PostgresCheckpointStore:
    return PostgresCheckpointStore(PG_DSN)


@pytest.mark.parametrize("build", [_sqlite_store, _pg_store])
def test_saver_before_open_raises(build):
    """未 open 取 saver 必须抛错（而不是返回 None 让调用方在别处炸）。"""
    store = build()
    assert store.is_open is False
    with pytest.raises(RuntimeError):
        _ = store.saver


async def test_sqlite_open_close_is_idempotent(tmp_path):
    """`close()` 重复调用必须安全，`close()` 后 saver 必须不可用。

    为什么较真：lifespan 的 finally 与异常路径可能各关闭一次；
    第二次关闭若抛错，会把「真正的启动失败原因」盖成「关闭失败」，排障直接跑偏。
    """
    store = CheckpointStore(tmp_path / "checkpoints.db")
    saver = await store.open()
    assert saver is not None
    assert store.backend == BACKEND_SQLITE
    assert store.is_open is True

    await store.close()
    assert store.is_open is False  # 关闭后必须能被 /health 看出“已不是活着的存储”
    await store.close()  # 必须不抛

    with pytest.raises(RuntimeError):
        _ = store.saver


def test_backward_compat_alias_points_to_sqlite():
    """旧名 `CheckpointStore` 必须仍然可用且等价于 SQLite 实现。

    `tests/test_restock_graph.py` 与 `docs/scripts/verify-1-6/1-7*.py` 按旧名调用；
    verify 脚本是已归档的验收证据，改它们等于让「当时验过的代码」与「现在能跑的脚本」不一致。
    """
    assert CheckpointStore is SqliteCheckpointStore


# --- 5. 依赖缺失时的可执行报错 -----------------------------------------------


async def test_postgres_open_without_driver_reports_actionable_error(monkeypatch):
    """未安装 psycopg/checkpoint-postgres 时，报错必须给出可执行的补救命令。

    为什么不用「真的什么都不装」来测：环境里可能已经装了这两个包，
    那条断言就会变成「有时通过」的假测试。这里用 `sys.modules[x] = None`
    人为制造 ImportError（CPython 的既定行为），保证确定性。
    """
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", None)

    store = PostgresCheckpointStore(PG_DSN)
    with pytest.raises(RuntimeError) as exc:
        await store.open()
    message = str(exc.value)
    assert "langgraph-checkpoint-postgres" in message
    assert "uv add" in message  # 运维照着就能执行，而不是只看到“导入失败”
    # 负向：报错里不得回显连接串（口令泄露面）
    assert "s3cr3t-pass" not in message


# --- 6. 口令脱敏 -------------------------------------------------------------


@pytest.mark.parametrize(
    "dsn,secret",
    [
        (PG_DSN, "s3cr3t-pass"),
        ("postgresql://u:p%40ss@db:5432/x", "p%40ss"),
    ],
)
def test_mask_dsn_hides_password(dsn, secret):
    masked = mask_dsn(dsn)
    assert secret not in masked
    assert "***" in masked


def test_mask_dsn_keyvalue_form_never_returns_raw():
    """`host=... password=...` 形式（urlparse 会静默失效的那种）也必须挡住。"""
    masked = mask_dsn("host=127.0.0.1 user=u password=topsecret dbname=x")
    assert "topsecret" not in masked
    assert "已省略" in masked


def test_postgres_location_never_leaks_password():
    store = PostgresCheckpointStore(PG_DSN)
    assert "s3cr3t-pass" not in store.location
    assert "127.0.0.1" in store.location  # 仍能定位到目标实例，便于排障


# --- 7. Windows 事件循环策略（PG 异步的硬前置，2026-10-01 实测） --------------------


class _RecordingPolicy:
    """假策略类：只记录“被实例化过”，不碰真实事件循环。"""

    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1


def _patch_loop_policy(monkeypatch, platform: str) -> list:
    """把平台改成指定值，并拦下真实的策略设置（不动测试进程的事件循环）。"""
    import asyncio

    from app import checkpoint

    applied: list = []
    _RecordingPolicy.instances = 0
    monkeypatch.setattr(checkpoint.sys, "platform", platform)
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", _RecordingPolicy, raising=False)
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda p: applied.append(p))
    return applied


def test_windows_event_loop_policy_applied_for_postgres(monkeypatch):
    """Windows + postgres 必须切 SelectorEventLoop。

    不切的实际后果：服务能启动，但第一次真正连库时抛 psycopg.InterfaceError
    『cannot use the ProactorEventLoop』——报错指向 psycopg 内部，很难联想到事件循环策略。
    """
    from app.checkpoint import ensure_windows_event_loop_policy

    applied = _patch_loop_policy(monkeypatch, "win32")
    settings = _settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN=PG_DSN)

    assert ensure_windows_event_loop_policy(settings) is True
    assert len(applied) == 1 and isinstance(applied[0], _RecordingPolicy)


def test_windows_event_loop_policy_skipped_for_sqlite(monkeypatch):
    """sqlite 后端不得被 Windows 特有约束牵连（无谓地改全局策略是有代价的）。"""
    from app.checkpoint import ensure_windows_event_loop_policy

    applied = _patch_loop_policy(monkeypatch, "win32")
    assert ensure_windows_event_loop_policy(_settings()) is False
    assert applied == []


def test_windows_event_loop_policy_skipped_on_linux(monkeypatch):
    """Linux 部署不受影响。"""
    from app.checkpoint import ensure_windows_event_loop_policy

    applied = _patch_loop_policy(monkeypatch, "linux")
    settings = _settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN=PG_DSN)
    assert ensure_windows_event_loop_policy(settings) is False
    assert applied == []


# --- 8. 事件循环兼容性守卫（uvicorn 0.36+ 的 loop_factory 机制变更） ------------------


class _FakeLoop:
    """用类名伪造事件循环（真实循环很难在单测里换掉）。"""


class SelectorEventLoop(_FakeLoop):  # noqa: N801 —— 类名要与 asyncio 真类一致，守卫按类名判断
    pass


class ProactorEventLoop(_FakeLoop):  # noqa: N801
    pass


def test_loop_compat_accepts_selector():
    from app.checkpoint import loop_supports_async_pg

    assert loop_supports_async_pg(SelectorEventLoop()) is True


def test_loop_compat_rejects_proactor_on_windows(monkeypatch):
    """Windows + Proactor 是不兼容组合，必须能被守卫识别出来。"""
    from app import checkpoint
    from app.checkpoint import loop_supports_async_pg

    monkeypatch.setattr(checkpoint.sys, "platform", "win32")
    assert loop_supports_async_pg(ProactorEventLoop()) is False


def test_loop_compat_is_platform_gated(monkeypatch):
    """非 Windows 不受这个 Windows 特有约束影响（避免守卫在 Linux 上误杀）。"""
    from app import checkpoint
    from app.checkpoint import loop_supports_async_pg

    monkeypatch.setattr(checkpoint.sys, "platform", "linux")
    assert loop_supports_async_pg(ProactorEventLoop()) is True


async def test_postgres_open_on_proactor_loop_gives_actionable_error(monkeypatch):
    """事件循环不兼容时，报错必须是**可执行的启动指引**，而不是 psycopg 的内部异常。

    为什么较真：实测把 `uvicorn app.main:app`（不带 `--loop none`）拿去跑 PG 后端时，
    报错是 `psycopg.InterfaceError: cannot use the 'ProactorEventLoop'`——
    运维看到的是“库连不上”，而真正原因是 uvicorn 0.36+ 改用 loop_factory 后
    不再读全局事件循环策略。一个错误消息把排查方向带偏，代价比失败本身大。
    """
    import asyncio

    from app import checkpoint

    monkeypatch.setattr(checkpoint.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: ProactorEventLoop())

    store = PostgresCheckpointStore(PG_DSN)
    with pytest.raises(RuntimeError) as exc:
        await store.open()

    message = str(exc.value)
    assert "ProactorEventLoop" in message
    assert "--loop none" in message  # 可直接照抄的修法之一
    assert "python -m app" in message  # 推荐入口
    assert "uvicorn 0.36+" in message  # 说明“为什么”，否则下次还会踩
    # 负向：不得因为要报错而先把连接串打出来
    assert "s3cr3t-pass" not in message


def test_health_reports_actual_backend_and_no_credentials(client):
    """`/health` 必须报**实际装配**的后端，且不得出现口令。

    报实际值而非配置值的原因：配置成 postgres 但装配回退成 sqlite 时，
    只报配置值的 health 会告诉运维“一切正常”。
    """
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["checkpoint_backend"] == BACKEND_SQLITE
    assert body["checkpoint_location"].endswith("checkpoints.db")
    assert body["checkpoint_open"] is True  # 只在 lifespan 真正装配成功后才为 True
    dumped = resp.text.lower()
    assert "password" not in dumped
    assert "s3cr3t" not in dumped


# --- 9. PG 连接池契约（FIX-23：单连接会让一次抖动变成永久失效） ----------------------


def test_postgres_store_uses_configured_pool_size():
    """池大小必须来自配置（多实例部署时要按副本数算总量，不能写死）。"""
    store = PostgresCheckpointStore(PG_DSN, max_size=7)
    assert store.pool_size == 7


def test_pool_size_is_floored_at_one():
    """非法配置（0 或负数）不能造出一个“取不到连接”的池——下限夹到 1。"""
    assert PostgresCheckpointStore(PG_DSN, max_size=0).pool_size == 1
    assert PostgresCheckpointStore(PG_DSN, max_size=-3).pool_size == 1


def test_factory_wires_pool_settings_from_config():
    """工厂必须把 `pg_pool_max_size` / `pg_pool_open_timeout_s` 真正传下去。

    为什么单独立一条：这两个配置项很容易出现“配了但不生效”（工厂忘了传），
    而后果是运维改了池大小却不生效，且不会有任何报错——只能靠人看代码发现。
    """
    settings = _settings(
        DKD_AGENT_CHECKPOINT_BACKEND="postgres",
        DKD_AGENT_PG_DSN=PG_DSN,
        DKD_AGENT_PG_POOL_MAX="9",
    )
    store = build_checkpoint_store(settings)
    assert isinstance(store, PostgresCheckpointStore)
    assert store.pool_size == 9, "工厂没有把配置的池大小传下去"
