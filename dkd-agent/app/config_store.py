"""智能体配置的读取/缓存层（排期 2-3，实现契约 §5.1）。

## 这一层解决什么

配置化之后，`restock` / `fault` / `chat` 三个载体的提示词、模型参数、业务阈值都来自
MySQL 的 `agent_config` 表，而**表的写入方是 Java**（RuoYi 权限 + 审计 + 乐观锁）：
Python 只读，不写（AGENTS §7.3 / 契约 §5.1）。本模块就是这条只读通路的唯一入口。

三条硬性设计（改之前先读，为什么这么定比怎么写更重要）：

1. **未配置 = 现状**：任何一步失败（总开关关闭 / 表不存在 / DB 不可达 / JSON 损坏 /
   runtime 非法）一律返回 `None`，调用方回**代码默认值**（`app/agent_defaults.py`）。
   绝不“猜一个配置”或用半截字段，否则线上行为差异无从归因（契约 §5.1 三重回落）。
2. **数值不合法不返回 None**：`params_json` 视为**不可信输入**（DB 里的值也是人工改出来的），
   逐字段夹取/回落（`agent_defaults.normalize_params`）+ WARN。返回 `None` 会把
   “参数写偏了一点”升级成“整份配置失效”，症状反而更重（AGENTS §7.6）。
3. **TTL 30s + reload 双通道**：Java 保存后异步 `POST /agents/config/reload` 清缓存，
   失败也不阻塞保存；reload 丢了也有 TTL 兜底（契约 §4.7 / §5.1）。

## 缓存与版本

缓存是「整份快照」粒度：TTL 内 `get()` 不查库（契约要求），TTL 到期或 `invalidate()`
后一次性重读**全部**行再替换（配置行只有个位数，逐 code 失效反而会让索引半新半旧）。
`version` 随快照透传，供 `agent_run.config_version` 埋点（排期 2-8）——“不编造版本号”，
回落时调用方拿到的是 `None`，不是 0。

## 停用（enabled=0）行的语义（Lead 已裁定，契约 §5.1 那句 enabled 过滤是笔误）

契约 §5.1 字面要求读取 SQL 限定 `enabled=1`，但 §5.2 要求内置 chat 场景在停用时返回
HTTP 400「该智能体已停用（原因）」。若 SQL 就把停用行滤掉，调用方**无法区分**
“没有记录（回落现状）”与“被停用（必须拒绝）”，§5.2 的拒绝分支永远不可达。
因此本实现：SQL 只过滤 `del_flag='0'`，`enabled` 的判断放在代码层——
- `get()` 返回该行快照（**无论 enabled**），调用方看 `snapshot.enabled` 决定是否拒绝；
- `get_by_scene()` / `list_enabled()` 只返回 `enabled=True` 的快照（停用行不被场景解析命中）。

这也是 `AgentConfigSnapshot` 保留 `enabled` 字段的意义。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.agent_defaults import (
    CUSTOM_SCENE_BASE,
    RUNTIME_CHAT,
    RUNTIMES,
    normalize_params,
)
from app.config import get_settings
from app.db import get_sessionmaker

logger = logging.getLogger("dkd.agent.config_store")

#: 内置 chat 载体的配置编码（seed 里固定为 `chat`，见 docs/ddl/agent_config_seed.sql）
CHAT_CONFIG_CODE = "chat"

#: 内置场景码（1-通用问答 2-补货 3-诊断 4-运营分析）。自定义 chat 从 101 起。
#: 不从 `app/prompts/assistant_chat.py` 导入 SCENE_* 常量：那里是提示词模块，
#: 本模块只需要“内置场景集合”这一个事实，避免把提示词改动耦进缓存层。
BUILTIN_CHAT_SCENES: frozenset[int] = frozenset({1, 2, 3, 4})

#: LLM 标量参数的默认值与区间（与 DDL 列注释、契约 §4.4 同口径）。
#: 为什么这里还要夹取：Java 保存时校验，但 DBA 直改 / 历史脏数据 / 迁移脚本都可能绕过，
#: 而这三类值会直接进 LLM 调用（温度、超时、重试），必须按不可信输入处理。
TIMEOUT_S_DEFAULT = 60
TIMEOUT_S_MIN = 5
TIMEOUT_S_MAX = 300
MAX_RETRIES_DEFAULT = 2
MAX_RETRIES_MIN = 0
MAX_RETRIES_MAX = 5
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
MAX_TOKENS_MIN = 256
MAX_TOKENS_MAX = 8192

#: MySQL "表不存在"错误号（契约要求只 WARN 一次，避免每 30s 刷屏）
MYSQL_TABLE_NOT_FOUND = 1146


def _monotonic() -> float:
    """单调时钟（单独成函数便于单测拨表，不依赖 time.time 的墙钟跳变）。"""
    return time.monotonic()


#: 配置加载器：一次返回**全部未软删行**（含停用行）的原始 mapping。
#: 单测注入假实现，禁止真连库（AGENTS §8）。
Loader = Callable[[], Awaitable[Sequence[Mapping[str, Any]]]]


@dataclass(frozen=True, slots=True)
class AgentConfigSnapshot:
    """某智能体**当前生效**配置的不可变快照（契约 §5.1 的字段口径）。

    只放运行期真正会被消费的字段：`description` / 审计列不进快照。
    `disabled_reason` 也未纳入（契约 §5.1 的 dataclass 没有该字段）——停用与否由
    `enabled` 判定，提示文案由调用方统一给出；若后续要求回显 DB 里的原因，
    需先改契约再在 dataclass 末尾**追加**一个带默认值的字段。
    """

    code: str
    name: str
    runtime: str
    scene: int
    builtin: bool
    system_prompt: str | None
    tools: tuple[str, ...]
    model: str | None
    temperature: float | None
    max_tokens: int | None
    timeout_s: int
    max_retries: int
    params: dict[str, float | int]
    enabled: bool
    version: int


# ------------------------------------------------------------------------------------
# 默认 loader：只读查询 agent_config
# ------------------------------------------------------------------------------------
# 为什么 SQL 只过滤 `del_flag='0'`、不在 SQL 里过滤 enabled：
#   停用行必须能被 `get()` 读到，否则「内置 chat 被停用」与「没有这条配置」在 Python 侧
#   完全不可区分，契约 §5.2「enabled=0 → HTTP 400 已停用」的拒绝分支不可达。
#   `enabled` 的过滤由 `list_enabled()` / `get_by_scene()` 在代码层完成（见模块 docstring）。
#   `del_flag='0'` 是 RuoYi 软删惯例（AGENTS §7.4），软删行一律不读。
# 只 SELECT 单表、只取运行期需要的列：不 JOIN、不聚合，避免在这个高频路径上引入慢查询。
_LOAD_SQL = """
SELECT code, name, runtime, scene, builtin, system_prompt, tools, model,
       temperature, max_tokens, timeout_s, max_retries, params_json,
       enabled, version
  FROM agent_config
 WHERE del_flag = '0'
 ORDER BY scene, code
"""


async def load_config_rows() -> list[Mapping[str, Any]]:
    """默认 DB loader：用只读账号查 `agent_config` 未软删的行（含停用行）。

    失败（表不存在 / DB 不可达 / 查询异常）**不在这里兜**，而是向上抛给 `ConfigStore`
    统一处理——这样“哪类错误怎么告警”只有一处逻辑，注入假 loader 时也能覆盖同一路径。
    """
    async with get_sessionmaker()() as session:
        result = await session.execute(text(_LOAD_SQL))
        rows = result.mappings().all()
    return [dict(row) for row in rows]


# ------------------------------------------------------------------------------------
# 行 → 快照（逐字段容错；任何“无法安全解释”的行都返回 None，由调用方回代码默认）
# ------------------------------------------------------------------------------------
def _text(value: Any) -> str | None:
    """转成去空白字符串；None/空串 → None（DDL 里空串与 NULL 都表示“未设置”）。"""
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _raw_text(value: Any) -> str | None:
    """**原文**文本字段：只做 None/bytes 处理，**不去首尾空白**。

    为什么 system_prompt 必须走这里：prompt 首尾空白是刻意的排版，且种子模板来自
    `SYSTEM_PROMPT` 常量（末尾有换行）；一旦 strip，排期 2-7 的
    「配置=默认时 prompt 与代码常量逐字相同」验收必挂。空串按“未设置”处理。
    """
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("智能体配置 system_prompt 不是合法 UTF-8，按未设置处理")
            return None
    text_value = str(value)
    return text_value if text_value != "" else None


def _as_bool(value: Any, *, default: bool) -> bool:
    """TINYINT/字符串布尔归一化（`"0"` 必须是 False，不能用 bool("0")）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _as_number(value: Any) -> float | None:
    """转成有限数值；None/空串/非数值 → None（None 不是错误，调用方按默认值处理）。"""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN/Inf 不可用
        return None
    return number


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return None if number is None else int(round(number))


def _clamp_int(
    value: Any,
    *,
    default: int,
    low: int,
    high: int,
    field: str,
    code: str,
) -> int:
    """整数字段夹取；非法 → 默认值，越界 → 夹到边界（不返回 None，契约 §5.1）。"""
    number = _as_number(value)
    if number is None:
        if value not in (None, ""):
            logger.warning(
                "智能体配置数值非法，回落默认值：code=%s field=%s value=%r", code, field, value
            )
        return default
    rounded = int(round(number))
    clamped = min(max(rounded, low), high)
    if clamped != rounded:
        logger.warning(
            "智能体配置数值越界，已夹取：code=%s field=%s value=%r → %r",
            code,
            field,
            rounded,
            clamped,
        )
    return clamped


def _clamp_optional_float(
    value: Any,
    *,
    low: float,
    high: float,
    field: str,
    code: str,
) -> float | None:
    """可空浮点字段（temperature）：NULL 保持 None = 走模型默认（契约 Q3）。"""
    if value is None or value == "":
        return None
    number = _as_number(value)
    if number is None:
        logger.warning(
            "智能体配置数值非法，按未设置处理：code=%s field=%s value=%r", code, field, value
        )
        return None
    clamped = min(max(number, low), high)
    if clamped != number:
        logger.warning(
            "智能体配置数值越界，已夹取：code=%s field=%s value=%r → %r",
            code,
            field,
            number,
            clamped,
        )
    return clamped


def _clamp_optional_int(
    value: Any,
    *,
    low: int,
    high: int,
    field: str,
    code: str,
) -> int | None:
    """可空整数字段（max_tokens）：NULL 保持 None = 走模型默认。"""
    if value is None or value == "":
        return None
    number = _as_number(value)
    if number is None:
        logger.warning(
            "智能体配置数值非法，按未设置处理：code=%s field=%s value=%r", code, field, value
        )
        return None
    rounded = int(round(number))
    clamped = min(max(rounded, low), high)
    if clamped != rounded:
        logger.warning(
            "智能体配置数值越界，已夹取：code=%s field=%s value=%r → %r",
            code,
            field,
            rounded,
            clamped,
        )
    return clamped


def _parse_tools(value: Any) -> tuple[str, ...]:
    """逗号分隔的工具名 → 去重元组；是否合法由消费者按载体白名单过滤（契约 §5.2）。"""
    text_value = _text(value)
    if not text_value:
        return ()
    names = (part.strip() for part in text_value.split(","))
    return tuple(dict.fromkeys(name for name in names if name))


def _parse_params(code: str, runtime: str, raw: Any) -> dict[str, float | int] | None:
    """解析 `params_json` 并归一化；**损坏/非对象 → None**（整份配置回落代码默认）。

    为什么损坏 JSON 走 None 而不是 `{}`：`{}` 会被当成“用户显式清空”，
    随后 `normalize_params` 补默认值——症状与“配置不存在”一样，但根因被掩盖，
    这里必须让调用方知道“这条配置不可信”（契约 §5.1 三重回落第 3 条）。
    """
    if raw is None or raw == "" or raw == b"":
        return normalize_params(runtime, None)
    payload: Any = raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            payload = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("智能体配置 params_json 不是合法 UTF-8，回落代码默认值：code=%s", code)
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            logger.warning("智能体配置 params_json 损坏，回落代码默认值：code=%s", code)
            return None
    if not isinstance(payload, Mapping):
        logger.warning(
            "智能体配置 params_json 不是对象，回落代码默认值：code=%s type=%s",
            code,
            type(payload).__name__,
        )
        return None
    return normalize_params(runtime, payload)


def _snapshot_from_row(row: Mapping[str, Any]) -> AgentConfigSnapshot | None:
    """原始行 → 快照；任何无法安全解释的行返回 None 并 WARN（契约 §5.1）。"""
    code = _text(row.get("code"))
    if code is None:
        logger.warning("agent_config 行缺少 code，已跳过（无法归属到任何智能体）")
        return None
    runtime = _text(row.get("runtime"))
    if runtime not in RUNTIMES:
        # runtime 决定“配置面”与校验规则，非法 runtime 意味着整行语义不可知，
        # 只能回落代码默认（契约 §5.1 第 3 条）。
        logger.warning("智能体配置 runtime 非法，回落代码默认值：code=%s runtime=%r", code, runtime)
        return None
    scene = _as_int(row.get("scene"))
    if scene is None:
        logger.warning(
            "智能体配置 scene 非法，回落代码默认值：code=%s scene=%r", code, row.get("scene")
        )
        return None
    version = _as_int(row.get("version"))
    if version is None:
        # 版本号是 agent_run.config_version 的唯一来源，“不编造版本号”（契约 §5.2 run 埋点）
        logger.warning(
            "智能体配置 version 非法，回落代码默认值：code=%s version=%r", code, row.get("version")
        )
        return None
    params = _parse_params(code, runtime, row.get("params_json"))
    if params is None:
        return None
    return AgentConfigSnapshot(
        code=code,
        name=_text(row.get("name")) or code,
        runtime=runtime,
        scene=scene,
        builtin=_as_bool(row.get("builtin"), default=False),
        system_prompt=_raw_text(row.get("system_prompt")),
        tools=_parse_tools(row.get("tools")),
        model=_text(row.get("model")),
        temperature=_clamp_optional_float(
            row.get("temperature"),
            low=TEMPERATURE_MIN,
            high=TEMPERATURE_MAX,
            field="temperature",
            code=code,
        ),
        max_tokens=_clamp_optional_int(
            row.get("max_tokens"),
            low=MAX_TOKENS_MIN,
            high=MAX_TOKENS_MAX,
            field="max_tokens",
            code=code,
        ),
        timeout_s=_clamp_int(
            row.get("timeout_s"),
            default=TIMEOUT_S_DEFAULT,
            low=TIMEOUT_S_MIN,
            high=TIMEOUT_S_MAX,
            field="timeout_s",
            code=code,
        ),
        max_retries=_clamp_int(
            row.get("max_retries"),
            default=MAX_RETRIES_DEFAULT,
            low=MAX_RETRIES_MIN,
            high=MAX_RETRIES_MAX,
            field="max_retries",
            code=code,
        ),
        params=params,
        enabled=_as_bool(row.get("enabled"), default=True),
        version=version,
    )


def _is_missing_table_error(exc: BaseException) -> bool:
    """识别 MySQL 1146（表不存在）——它只 WARN 一次，其余异常每次都 WARN。"""
    origin = getattr(exc, "orig", None) or exc
    args = getattr(origin, "args", ())
    return bool(args) and args[0] == MYSQL_TABLE_NOT_FOUND


# ------------------------------------------------------------------------------------
# ConfigStore：TTL 缓存 + 三重回落
# ------------------------------------------------------------------------------------
class ConfigStore:
    """`agent_config` 的进程内 TTL 缓存（契约 §5.1 的公开 API）。

    线程/协程安全：所有读入口都先 `await _ensure_fresh()`，刷新在 `asyncio.Lock` 内串行，
    避免 TTL 到期瞬间多个并发请求同时打库（一次只有个位数行，但仍不希望放大）。
    """

    def __init__(self, *, loader: Loader | None = None, ttl_s: float = 30.0) -> None:
        # loader 注入是 AGENTS §8 的要求：单测绝不连真库。
        self._loader: Loader = loader or load_config_rows
        self._ttl_s = float(ttl_s)
        self._lock: asyncio.Lock | None = None
        self._by_code: dict[str, AgentConfigSnapshot] = {}
        self._by_scene: dict[int, AgentConfigSnapshot] = {}
        self._loaded_at = 0.0
        self._loaded = False
        # 上一次成功读到的版本（code → version）：只用于“配置已更新”的可观测日志，
        # 不参与任何行为决策；invalidate 不随之清空，否则 reload 后就看不到版本变化了。
        self._versions: dict[str, int] = {}
        # 表不存在（1146）只告警一次：每 TTL 刷屏会让真正的 WARN 被淹没
        self._missing_table_warned = False

    # -- 公开读接口 --------------------------------------------------------------
    async def get(self, code: str) -> AgentConfigSnapshot | None:
        """按编码取配置；开关关闭/加载失败/无此编码 → None（调用方回代码默认）。

        **停用行也会返回**（`snapshot.enabled is False`）：调用方必须显式判断，
        才能区分“无记录（回落现状）”与“被停用（拒绝）”。见模块 docstring。
        """
        if not self._from_db_enabled():
            return None
        await self._ensure_fresh()
        key = code.strip()
        return self._by_code.get(key)

    async def get_by_scene(self, scene: int) -> AgentConfigSnapshot | None:
        """按场景码取**已启用**配置（停用行不参与场景解析，契约 §5.2）。"""
        if not self._from_db_enabled():
            return None
        await self._ensure_fresh()
        return self._by_scene.get(int(scene))

    async def list_enabled(self) -> list[AgentConfigSnapshot]:
        """全部启用配置（按场景、编码稳定排序；空列表 = 回落代码默认）。"""
        if not self._from_db_enabled():
            return []
        await self._ensure_fresh()
        return sorted(
            (snapshot for snapshot in self._by_code.values() if snapshot.enabled),
            key=lambda snapshot: (snapshot.scene, snapshot.code),
        )

    def invalidate(self, code: str | None = None) -> None:
        """清缓存（`/agents/config/reload` 调用；code=None 清全量）。

        为什么指定 code 时也整体失效：重新加载是**一次只读查询**（行数个位数），
        而“只删一行、其余保留”会让 code/scene 两个索引短暂不一致；reload 的唯一触发方
        是 Java 保存后的通知，此时全量刷新正是想要的效果。
        """
        if code is None:
            self._by_code = {}
            self._by_scene = {}
        else:
            key = code.strip()
            snapshot = self._by_code.pop(key, None)
            if snapshot is not None and self._by_scene.get(snapshot.scene) is snapshot:
                self._by_scene.pop(snapshot.scene, None)
        # 标记为未加载：下一次访问立即重读，而不是等 TTL 到期。
        # 注意不清 `_versions`：reload 后仍要能记出“版本 a→b”的变更日志。
        self._loaded = False
        self._loaded_at = 0.0

    # -- 内部：总开关 + 刷新 ------------------------------------------------------
    @staticmethod
    def _from_db_enabled() -> bool:
        """总开关（`DKD_AGENT_CONFIG_FROM_DB`）；关闭时行为与改造前完全一致。"""
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
        """重读全部未软删行并整体替换缓存；失败 → 清空 + WARN（三重回落第 2 条）。"""
        try:
            rows = await self._loader()
        except Exception as exc:  # noqa: BLE001 —— 配置读取是旁路，任何异常都只回落不抛穿
            # 失败时清空缓存 = 调用方回落代码默认；但保留 `_versions`，
            # 这样 DB 恢复后仍能对比出“配置在故障期间被改过”。
            self._by_code = {}
            self._by_scene = {}
            self._loaded = True
            self._loaded_at = _monotonic()
            if _is_missing_table_error(exc):
                if not self._missing_table_warned:
                    self._missing_table_warned = True
                    logger.warning(
                        "agent_config 表不存在（MySQL 1146），智能体全部回落代码默认值；"
                        "请执行 docs/ddl/agent_config.sql 与 seed（本告警只提示一次）"
                    )
            else:
                logger.warning(
                    "读取 agent_config 失败，智能体全部回落代码默认值：%s: %s",
                    type(exc).__name__,
                    str(exc)[:200],
                )
            return
        self._accept_rows(rows)

    def _accept_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """归一化 + 建索引 + 版本比对；单行无法解释只跳过该行（其他配置仍可用）。"""
        by_code: dict[str, AgentConfigSnapshot] = {}
        by_scene: dict[int, AgentConfigSnapshot] = {}
        for row in rows:
            snapshot = _snapshot_from_row(row)
            if snapshot is None:
                continue
            if snapshot.code in by_code:
                # UK(code) 保证真实库不会重复；假 loader/历史脏数据下先到先得，避免随机覆盖
                logger.warning("agent_config 重复 code，已保留首行：code=%s", snapshot.code)
                continue
            by_code[snapshot.code] = snapshot
            if not snapshot.enabled:
                continue
            if snapshot.scene in by_scene:
                logger.warning(
                    "agent_config scene 重复，已保留首个：scene=%s code=%s（被忽略：%s）",
                    snapshot.scene,
                    by_scene[snapshot.scene].code,
                    snapshot.code,
                )
                continue
            by_scene[snapshot.scene] = snapshot
        # 版本比对：只记“配置已更新 version a→b”（INFO）。这是排障线索——
        # 行为变了能直接对上“哪一次保存生效了”，而不是盲查 TTL/reload 时序。
        for code, snapshot in by_code.items():
            previous = self._versions.get(code)
            if previous is not None and previous != snapshot.version:
                logger.info(
                    "智能体配置已更新：code=%s version=%s→%s",
                    code,
                    previous,
                    snapshot.version,
                )
        self._versions = {code: snapshot.version for code, snapshot in by_code.items()}
        self._by_code = by_code
        self._by_scene = by_scene
        self._loaded = True
        self._loaded_at = _monotonic()


# ------------------------------------------------------------------------------------
# 进程单例与场景解析
# ------------------------------------------------------------------------------------
_STORE: ConfigStore | None = None


async def get_config_store() -> ConfigStore:
    """进程内单例（TTL 取 `DKD_AGENT_CONFIG_TTL_S`）。

    为什么函数内不 `await` 任何东西：单事件循环下“检查 + 赋值”之间没有让步点，
    不会并发创建出两个 store（多 store 会让 reload 只清掉其中一个的缓存）。
    多实例部署时各进程各自持有缓存，reload 通知需逐实例送达（TTL 是最终兜底）。
    """
    global _STORE
    if _STORE is None:
        _STORE = ConfigStore(ttl_s=get_settings().config_ttl_s)
    return _STORE


def reset_config_store() -> None:
    """丢弃单例（**仅测试用**）。"""
    global _STORE
    _STORE = None


async def resolve_chat_scene(scene: int) -> AgentConfigSnapshot | None:
    """把 chat 请求的 `scene` 解析为配置快照（契约 §5.2 的判定口径）。

    - 内置场景 1~4 → 内置 `chat` 行（四个场景共用一张配置；**可能是停用行**
      `enabled=False`，调用方必须判断，见 `api/chat.py` 的 400 分支）；
    - 自定义场景 ≥101 → 按 scene 查**已启用** chat 配置（停用/不存在都返回 None）；
    - 其余（0、5~100）→ None，调用方按“场景不存在”拒绝（契约 §5.2）。

    `None` 在这里有双重含义（“没有配置”或“配置回落”），是否要回代码默认由调用方决定——
    本函数不替载体做降级决策。
    """
    if scene >= CUSTOM_SCENE_BASE:
        snapshot = await (await get_config_store()).get_by_scene(scene)
        if snapshot is not None and snapshot.runtime != RUNTIME_CHAT:
            # Java 侧 scene 分配保证是 chat 行，但 DB 是“外部输入”，防御一次
            logger.warning(
                "scene 命中了非 chat 载体的配置，已忽略：scene=%s code=%s runtime=%s",
                scene,
                snapshot.code,
                snapshot.runtime,
            )
            return None
        return snapshot
    if scene in BUILTIN_CHAT_SCENES:
        return await (await get_config_store()).get(CHAT_CONFIG_CODE)
    return None


__all__ = [
    "BUILTIN_CHAT_SCENES",
    "CHAT_CONFIG_CODE",
    "AgentConfigSnapshot",
    "ConfigStore",
    "Loader",
    "get_config_store",
    "load_config_rows",
    "reset_config_store",
    "resolve_chat_scene",
]
