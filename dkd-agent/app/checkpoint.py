"""会话持久化（任务 0-10）。

选型：**SQLite（AsyncSqliteSaver）** 而非 Redis——本机 Redis 为 3.2.100，
无模块系统（不支持 RedisJSON/RediSearch）、内核 3.2 < 5.0（无 Streams），
`langgraph-checkpoint-redis` 不可用（方案 V1.1 §5.1）。

生命周期：由 FastAPI lifespan 持有，全进程单例；关闭时释放文件句柄。
运维要点（需写入 runbook）：
  - 数据文件 `DKD_AGENT_SQLITE_PATH`（默认 `var/checkpoints.db`）**必须纳入定期备份**；
  - 备份方式：停止写入后复制文件，或 `sqlite3 var/checkpoints.db ".backup out.db"`（热备份）；
  - 迁移触发条件：出现多实例部署或需跨机共享会话 → 换 Postgres saver 或升级 Redis(8.0+/Stack)。
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import get_settings

logger = logging.getLogger("dkd.agent.checkpoint")

_store: CheckpointStore | None = None


class CheckpointStore:
    """SQLite checkpoint 存储的生命周期封装。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stack = AsyncExitStack()
        self._saver: AsyncSqliteSaver | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def saver(self) -> AsyncSqliteSaver:
        if self._saver is None:
            raise RuntimeError("checkpointer 尚未打开，请先 await store.open()")
        return self._saver

    async def open(self) -> AsyncSqliteSaver:
        """建目录 + 建表（setup 幂等）+ 建立连接。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._saver = await self._stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(self._path))
        )
        # setup() 会创建 checkpoints/writes 等表；重复调用安全
        await self._saver.setup()
        logger.info("checkpointer ready: path=%s", self._path)
        return self._saver

    async def close(self) -> None:
        await self._stack.aclose()
        self._saver = None
        logger.info("checkpointer closed: path=%s", self._path)


async def open_checkpoint_store(path: Path | None = None) -> CheckpointStore:
    """进程级单例（由 lifespan 调用）。

    **构造即打开**：避免调用方忘记 await open() 后拿到未初始化的 saver。
    """
    global _store
    store = CheckpointStore(path or get_settings().sqlite_path)
    await store.open()
    _store = store
    return store


def get_checkpoint_store() -> CheckpointStore:
    if _store is None:
        raise RuntimeError("checkpoint store 未初始化（应由 FastAPI lifespan 启动）")
    return _store


def get_checkpointer() -> AsyncSqliteSaver:
    """供图编译使用。"""
    return get_checkpoint_store().saver
