"""工具注册表 `agent_tool` 的读取/缓存/启动校验层（排期 3-4，契约 §5.3）。

## 这一层解决什么

3-2 的自定义智能体能让管理员配置「这个智能体可以调用哪些工具」，但**启用/停用**是
运营动作：某个工具（例如历史维修检索）数据质量不过关时，应该能在配置台一键停用，
而不是改代码发版。`agent_tool` 表就是这层开关，本模块是它唯一的运行期读取入口。

`app/tools/assistant_tools.py` 的 `tools_for_scene()` 仍然定义「场景候选工具集」
（代码事实），本注册表只做**开关层**：候选 ∩ 启用 ∩ 只读 = 本轮真正给模型的工具。
两层分离的原因是：工具实现属于代码，启停属于运营，混在一起就只能靠发版改行为。

## 四条纪律

1. **配置不可读 = 现状**：总开关（`DKD_AGENT_CONFIG_FROM_DB`）关闭 / 表不存在 /
   DB 不可达 / 查询异常 → 回落**全部内置助手工具启用**（`all_assistant_tools()`），
   只 WARN 不抛穿。旧环境不执行 DDL 也必须能启动（与 `config_store` 同一套三重回落精神）。
2. **未知 code = 启动即拒绝**：表存在且某行 `code` 映射不到代码里的工具名时，
   `validate_startup()` 抛 `RuntimeError`（排期 3-4 明确要求）。为什么不能只 WARN：
   一行映射不到的配置意味着「运营以为启用了某个能力、实际并不存在」，这种静默能力缺失
   比启动失败昂贵——页面会一直显示“已启用”。
3. **只读**：SQL 只有 SELECT（`del_flag='0'`），不写不删（AGENTS §7.3；写操作只能经 Java）。
4. **TTL + invalidate**：30s 缓存，避免每轮对话都打表；`invalidate()` 供测试/未来 reload 用。

## 与 `config_store.py` 的关系

两者是**平行的两个只读配置通道**（`agent_config` / `agent_tool`），都遵循“未配置=现状、
失败只回落不抛穿”。刻意不合并：配置快照按 code/scene 索引，工具开关按 scope/enabled 过滤，
混成一个缓存会让失效粒度互相牵连（改一个工具就重读全部配置）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db import get_sessionmaker

logger = logging.getLogger("dkd.agent.services.tool_registry")

#: chat 载体的 scope 值（与 DDL 注释 `scope: chat/restock/fault/all` 一致）。
CHAT_SCOPE = "chat"
#: 通配 scope：`all` 表示对所有载体生效。
SCOPE_ALL = "all"
#: 注册表缓存 TTL（秒）。与 `agent_config` 的 30s 对齐：运维点保存后最多等半分钟生效。
DEFAULT_TTL_S = 30.0
#: MySQL "表不存在"错误号（与 config_store 同口径：只 WARN 一次，避免按 TTL 刷屏）。
MYSQL_TABLE_NOT_FOUND = 1146

#: 默认 loader：一次返回全部未软删行（含停用行——启停判断在代码层做）。
#: 单测注入假实现，禁止真连库（AGENTS §8）。
Loader = Callable[[], Awaitable[Sequence[Mapping[str, Any]]]]

# 只 SELECT、只取运行期需要的列：不 JOIN、不聚合（这是每 30s 一次的高频路径）。
_LOAD_SQL = """
SELECT code, name, scope, source, read_only, enabled, timeout_ms, row_limit, builtin
  FROM agent_tool
 WHERE del_flag = '0'
 ORDER BY code
"""


def _monotonic() -> float:
    """单调时钟（单独成函数便于单测拨表，不依赖 time.time 的墙钟跳变）。"""
    return time.monotonic()


def _text(value: Any) -> str | None:
    """转成去空白字符串；None/空串 → None。"""
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _as_bool(value: Any, *, default: bool) -> bool:
    """TINYINT/字符串布尔归一化（`"0"` 必须是 False，不能用 bool("0")）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _as_int(value: Any, *, default: int) -> int:
    """整数归一化；非法/空 → default（工具级护栏是声明值，不参与安全决策）。"""
    if value is None or value == "":
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _is_missing_table_error(exc: BaseException) -> bool:
    """识别 MySQL 1146（表不存在）——它只 WARN 一次，其余异常每次 WARN。"""
    origin = getattr(exc, "orig", None) or exc
    args = getattr(origin, "args", ())
    return bool(args) and args[0] == MYSQL_TABLE_NOT_FOUND


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """`agent_tool` 的一行（只放运行期真正被消费的字段）。

    `timeout_ms` / `row_limit` 是**声明值**：本地工具的实际读取护栏仍由
    `app/tools/read_tools.py` 的 5s / 2000 行统一兜底（二者不冲突，取更严者）。
    """

    code: str
    name: str
    scope: str
    source: str
    read_only: bool
    enabled: bool
    timeout_ms: int
    row_limit: int
    builtin: bool


def _entry_from_row(row: Mapping[str, Any]) -> ToolEntry | None:
    """原始行 → `ToolEntry`；缺 code 的行无法归属，跳过并 WARN。"""
    code = _text(row.get("code"))
    if code is None:
        logger.warning("agent_tool 行缺少 code，已跳过（无法归属到任何工具）")
        return None
    return ToolEntry(
        code=code,
        name=_text(row.get("name")) or code,
        scope=_text(row.get("scope")) or CHAT_SCOPE,
        source=_text(row.get("source")) or "local",
        read_only=_as_bool(row.get("read_only"), default=True),
        enabled=_as_bool(row.get("enabled"), default=True),
        timeout_ms=_as_int(row.get("timeout_ms"), default=10_000),
        row_limit=_as_int(row.get("row_limit"), default=2_000),
        builtin=_as_bool(row.get("builtin"), default=True),
    )


@lru_cache(maxsize=1)
def _builtin_tool_names() -> frozenset[str]:
    """代码里已实现的工具名集合（启动校验与默认配置的唯一事实源）。

    为什么函数内 import + lru_cache：`assistant_tools` 会拉进 db/graphs 等一批模块，
    而注册表在启动早期就要用；延迟导入避免 import 顺序问题，缓存避免每次刷新都重建
    StructuredTool（8 个工具，代价不大但不该每 30s 白造一遍）。
    """
    from app.tools.assistant_tools import all_assistant_tools

    return frozenset(tool.name for tool in all_assistant_tools())


@lru_cache(maxsize=1)
def _default_entries() -> tuple[ToolEntry, ...]:
    """代码默认注册表：**全部内置助手工具启用且只读**（未配置/旧环境的行为）。

    用 lru_cache 缓存：DB 关闭/表缺失时每次对话都会走默认路径，不该反复重建这些对象。
    """
    return tuple(
        ToolEntry(
            code=name,
            name=name,
            scope=CHAT_SCOPE,
            source="local",
            read_only=True,
            enabled=True,
            timeout_ms=10_000,
            row_limit=2_000,
            builtin=True,
        )
        for name in sorted(_builtin_tool_names())
    )


def _match_scope(entry: ToolEntry, scope: str) -> bool:
    """scope 过滤：请求 chat 时 `chat` 与 `all` 都命中。"""
    return entry.scope == scope or entry.scope == SCOPE_ALL


async def load_tool_rows() -> list[Mapping[str, Any]]:
    """默认 DB loader：只读账号查 `agent_tool` 未软删的行（含停用行）。"""
    async with get_sessionmaker()() as session:
        result = await session.execute(text(_LOAD_SQL))
        rows = result.mappings().all()
    return [dict(row) for row in rows]


class ToolRegistry:
    """`agent_tool` 的进程内 TTL 缓存 + 启动校验 + 调度过滤器。

    线程/协程安全：所有读入口先 `await _ensure_fresh()`，刷新在 `asyncio.Lock` 内串行，
    避免 TTL 到期瞬间多个并发请求同时打库（表只有个位数行，但仍不希望放大）。
    """

    def __init__(
        self,
        *,
        loader: Loader | None = None,
        ttl_s: float = DEFAULT_TTL_S,
        db_enabled: bool | None = None,
    ) -> None:
        # loader 注入是 AGENTS §8 的要求：单测绝不连真库。
        self._loader: Loader = loader or load_tool_rows
        self._ttl_s = float(ttl_s)
        # None = 动态读 Settings（生产）；True/False = 单测显式指定，避免受环境变量串扰。
        self._db_enabled = db_enabled
        self._lock: asyncio.Lock | None = None
        self._entries: dict[str, ToolEntry] = {}
        self._unknown_codes: tuple[str, ...] = ()
        self._loaded = False
        self._loaded_at = 0.0
        # 降级状态（health 只读；不参与业务判断）：True = 当前用的是代码默认而非 DB 配置
        self._degraded = True
        self._degrade_reason: str | None = "not_loaded"
        self._source = "pending"
        self._missing_table_warned = False

    # -- 公开读接口 --------------------------------------------------------------

    async def list_enabled(self, *, scope: str = CHAT_SCOPE) -> list[ToolEntry]:
        """启用且**只读**的工具（按 code 稳定排序）；配置不可读时回落代码默认。

        @param scope 载体分组；`chat` 会同时命中 `scope=all` 的行
        """
        if not self._from_db_enabled():
            self._apply_default("db_disabled")
            return [
                entry
                for entry in self._entries.values()
                if entry.enabled and entry.read_only and _match_scope(entry, scope)
            ]
        await self._ensure_fresh()
        return [
            entry
            for entry in sorted(self._entries.values(), key=lambda item: item.code)
            if entry.enabled and entry.read_only and _match_scope(entry, scope)
        ]

    async def validate_startup(self) -> None:
        """启动校验：表存在且存在无法映射的 code → `RuntimeError`（拒绝启动）。

        - 总开关关闭 → 直接用代码默认（不查库、不拒绝）；
        - 表不存在 / DB 不可达 → 回落代码默认 + WARN（旧环境必须能启动）；
        - 表存在但为空 → 不拒绝，WARN 提示执行 seed（空表 = 运营主动清空也是合法状态，
          但更可能是漏了 seed，静默少工具正是“提示与能力脱节”的入口）。
        """
        if not self._from_db_enabled():
            self._apply_default("db_disabled")
            logger.info("工具注册表：配置总开关关闭，使用代码默认（全部内置工具启用）")
            return
        await self._ensure_fresh()
        if self._unknown_codes:
            unknown = "、".join(sorted(self._unknown_codes))
            raise RuntimeError(
                "agent_tool 中存在无法映射到代码工具的行，拒绝启动："
                f"{unknown}。处理方式：修正 code 或软删这些行，"
                "或在 app/tools/assistant_tools.py 中实现同名工具（排期 3-4 启动校验）"
            )
        if self._degraded:
            logger.warning(
                "工具注册表不可用（%s），已回落代码默认（全部内置工具启用）", self._degrade_reason
            )
        elif not self._entries:
            logger.warning(
                "agent_tool 表为空：本轮不会向模型注入任何工具；"
                "请执行 docs/ddl/agent_tool_llm_profile.sql 的 seed"
            )

    def invalidate(self) -> None:
        """清缓存：下一次访问立即重读（测试与未来 reload 用）。"""
        self._loaded = False
        self._loaded_at = 0.0

    # -- 观测（health 用；只读缓存，**不触发加载/不连库**）-----------------------

    def stats(self) -> dict[str, Any]:
        """装配状态快照：启用工具数 / 是否降级 / 来源。"""
        enabled = (
            sum(
                1
                for entry in self._entries.values()
                if entry.enabled and entry.read_only and _match_scope(entry, CHAT_SCOPE)
            )
            if self._loaded
            else 0
        )
        return {
            "installed": True,
            "degraded": self._degraded,
            "enabled": enabled,
            "source": self._source,
            "unknown": len(self._unknown_codes),
        }

    # -- 内部：总开关 + 刷新 ------------------------------------------------------

    def _from_db_enabled(self) -> bool:
        """总开关（`DKD_AGENT_CONFIG_FROM_DB`）；关闭时行为与改造前完全一致。"""
        if self._db_enabled is not None:
            return self._db_enabled
        return bool(get_settings().config_from_db)

    def _is_fresh(self) -> bool:
        return self._loaded and (_monotonic() - self._loaded_at) < self._ttl_s

    async def _ensure_fresh(self) -> None:
        if self._is_fresh():
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._is_fresh():
                return
            await self._refresh()

    async def _refresh(self) -> None:
        """重读全部未软删行；失败 → 回代码默认 + WARN（旁路不得抛穿启动/对话）。"""
        try:
            rows = await self._loader()
        except Exception as exc:  # noqa: BLE001 —— 注册表是旁路，任何异常都只回落不抛穿
            self._apply_default("table_missing" if _is_missing_table_error(exc) else "load_failed")
            self._loaded = True
            self._loaded_at = _monotonic()
            if _is_missing_table_error(exc):
                if not self._missing_table_warned:
                    self._missing_table_warned = True
                    logger.warning(
                        "agent_tool 表不存在（MySQL 1146），工具开关回落代码默认（全部启用）；"
                        "请执行 docs/ddl/agent_tool_llm_profile.sql（本告警只提示一次）"
                    )
            else:
                logger.warning(
                    "读取 agent_tool 失败，工具开关回落代码默认（全部启用）：%s: %s",
                    type(exc).__name__,
                    str(exc)[:200],
                )
            return
        self._accept_rows(rows)

    def _accept_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """归一化 + 未知 code 收集；单行无法解释只跳过该行（其余配置仍生效）。"""
        code_names = _builtin_tool_names()
        entries: dict[str, ToolEntry] = {}
        unknown: list[str] = []
        for row in rows:
            entry = _entry_from_row(row)
            if entry is None:
                continue
            if entry.code not in code_names:
                unknown.append(entry.code)
            if entry.code in entries:
                # UK(code) 保证真实库不会重复；假 loader/历史脏数据下先到先得
                logger.warning("agent_tool 重复 code，已保留首行：code=%s", entry.code)
                continue
            entries[entry.code] = entry
        self._entries = entries
        self._unknown_codes = tuple(dict.fromkeys(unknown))
        self._degraded = False
        self._degrade_reason = None
        self._source = "db"
        self._loaded = True
        self._loaded_at = _monotonic()

    def _apply_default(self, reason: str) -> None:
        """回落代码默认（全部内置工具启用），并记录降级原因供 health 展示。"""
        self._entries = {entry.code: entry for entry in _default_entries()}
        self._unknown_codes = ()
        self._degraded = True
        self._degrade_reason = reason
        self._source = "default"
        self._loaded = True
        self._loaded_at = _monotonic()


# ------------------------------------------------------------------------------------
# 进程单例（main.py lifespan 初始化并做启动校验）
# ------------------------------------------------------------------------------------
_REGISTRY: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """进程内单例（TTL 取模块常量 30s；不存在并发创建问题——单事件循环、中间无 await）。"""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry()
    return _REGISTRY


def reset_tool_registry() -> None:
    """丢弃单例（**仅测试用**）。"""
    global _REGISTRY
    _REGISTRY = None


def tool_registry_stats() -> dict[str, Any]:
    """`/health` 用只读状态；单例未创建时返回 installed=False（不触发任何加载）。"""
    registry = _REGISTRY
    if registry is None:
        return {
            "installed": False,
            "degraded": False,
            "enabled": 0,
            "source": "not_created",
            "unknown": 0,
        }
    return registry.stats()


__all__ = [
    "CHAT_SCOPE",
    "DEFAULT_TTL_S",
    "SCOPE_ALL",
    "ToolEntry",
    "ToolRegistry",
    "get_tool_registry",
    "load_tool_rows",
    "reset_tool_registry",
    "tool_registry_stats",
]
