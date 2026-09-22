"""数据库访问层（任务 0-4 / 0-12）。

权限模型（重要，与 AGENTS §7.3 的关系在此澄清）：
  dkd-agent 使用的 MySQL 账号 `dkd_agent` 采用**两级授权**：
    ① 业务表（tb_*）：**只读**（SELECT），且仅限表白名单；
    ② 智能体自有表（agent_*）：允许 SELECT/INSERT/UPDATE —— 这些表属于智能体自身的
       会话、留痕、计划数据，不是"业务事实"，不违反 §7.3「业务事实唯一所有者」；
       **DELETE 一律不授予**（软删除，AGENTS §7.4），DDL 不授予。
  业务数据的一切写操作仍必须走 Java REST 回调（§7.3）。

为什么读和写用同一引擎：账号是同一个，权限由 MySQL 侧控制；用两个引擎反而会掩盖
"误把业务表当自有表写"的错误——那种情况应该在 SQL 层就被 MySQL 拒绝。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

logger = logging.getLogger("dkd.agent.db")

# 智能体自有表（允许写）；其余一律视为业务表（只读）
AGENT_OWNED_TABLES = frozenset(
    {"agent_conversation", "agent_message", "agent_decision_log", "agent_restock_plan"}
)


class TableNotAllowedError(PermissionError):
    """访问了白名单外的表——在 SQL 到达 MySQL 之前就拦掉（AGENTS §8 拒绝路径）。"""


def assert_table_allowed(table: str, *, writable: bool = False) -> None:
    """表名白名单校验（工具层 SQL 必须经此校验）。

    writable=True 仅允许 agent_* 自有表；业务表写操作一律拒绝并提示走 Java 回调。
    """
    name = table.strip().strip("`").lower()
    if writable:
        if name not in AGENT_OWNED_TABLES:
            raise TableNotAllowedError(
                f"禁止直接写业务表 `{name}`：写操作必须回调 Java REST（AGENTS §7.3）"
            )
        return
    if name in AGENT_OWNED_TABLES or name in get_settings().whitelist_tables:
        return
    raise TableNotAllowedError(f"表 `{name}` 不在只读白名单内，拒绝查询")


@lru_cache
def get_engine() -> AsyncEngine:
    """进程级异步引擎（只读业务表 + 写 agent_* 表，权限由 MySQL 账号决定）。"""
    settings = get_settings()
    engine = create_async_engine(
        settings.dsn,
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,  # 防止 MySQL 空闲断连后拿到坏连接
        pool_recycle=1800,
        echo=False,
    )
    logger.info("db engine created: %s:%s/%s", settings.db_host, settings.db_port, settings.db_name)
    return engine


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """应用关闭时释放连接池。

    为什么必须先 `await engine.dispose()`：只清 `lru_cache` 只是丢掉引用，
    池里的 MySQL 连接要等 GC 才关——表现为进程退出时 `aiomysql Connection.__del__`
    在**已关闭的 event loop** 上抛 `RuntimeError: Event loop is closed`
    （看起来像崩溃，其实连接也没被优雅关闭）。
    修因：1-4 回测脚本收尾时发现该噪音，进而查到 dispose 实际没关连接。
    """
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
