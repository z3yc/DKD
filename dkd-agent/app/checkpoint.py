"""会话持久化 / checkpointer（任务 0-10；PG 选型评审「方案 A」，2026-09-28）。

## 为什么这里变成「工厂 + 两个实现」

评审结论（`docs/dkd-agent-postgres-selection-review.md`，方案 A）：**只换 checkpointer**。
- 现状后端：SQLite（`AsyncSqliteSaver`）—— 单实例部署的默认值，零新增基础设施。
  本机 Redis 为 3.2.100（无模块系统、内核 < 5.0 无 Streams、`appendonly no`），
  `langgraph-checkpoint-redis` 不可用（方案 V1.1 §5.1 实测），所以当初没走 Redis。
- 新增后端：PostgreSQL（`AsyncPostgresSaver`）—— 出现**多实例部署**或**需跨机共享会话**时启用，
  一步销掉 FIX-14 的两条触发条件；未来 pgvector 知识库可复用同一实例。

⚠️ **红线边界（AGENTS §1 约束 5 / §7.3）**：PG 只承载 checkpoint（以及将来的向量索引），
**不是业务数据的第二通道**。业务读取仍走 MySQL 只读账号 + 表白名单，写操作仍必须回调 Java。

## 生命周期

由 FastAPI lifespan 持有，全进程单例；关闭时释放连接。

运维要点（需写入 runbook）：
  - **sqlite 后端**：数据文件 `DKD_AGENT_SQLITE_PATH`（默认 `var/checkpoints.db`）必须纳入定期备份；
    备份方式：停止写入后复制文件，或 `sqlite3 var/checkpoints.db ".backup out.db"`（热备份）。
  - **postgres 后端**：备份纳入现有 PG 备份体系；账号授权见 `docs/ddl/create_agent_pg_role.sql`。
  - **切换与回滚**：改 `DKD_AGENT_CHECKPOINT_BACKEND` 一行即可切换；
    **不做双向数据同步**（评审稿 D4 结论：旧会话价值低、非审计数据，切换后旧会话不可恢复，
    新旧双跑观察 1~2 周再停掉旧后端）。
"""

from __future__ import annotations

import logging
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Protocol

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings, get_settings

logger = logging.getLogger("dkd.agent.checkpoint")

BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgres"

# 匹配 `scheme://user:password@host` 中的口令段（AGENTS §7.8：日志/health 不得出现凭据）
_DSN_CREDENTIAL_RE = re.compile(r"://([^:/@]+):([^@]*)@")

_store: CheckpointBackend | None = None


def mask_dsn(dsn: str) -> str:
    """把连接串里的口令替换为 `***`，用于日志与 `/health`。

    为什么不用 `urllib.parse.urlparse`：psycopg 的连接串既支持 URI 形式也支持
    `host=... user=... password=...` 形式，urlparse 对后者会静默返回空 netloc——
    「脱敏函数在另一种格式上失效」是最危险的一类失败（看起来脱敏了，其实原样打印）。
    因此这里对两种形式都做兜底：正则没命中但连接串里**明显带凭据**时，
    宁可返回「无法安全脱敏」也不返回原文。
    """
    masked, count = _DSN_CREDENTIAL_RE.subn(r"://\1:***@", dsn)
    if count:
        return masked
    if "password" in dsn.lower() or "@" in dsn:
        return "<pg-dsn:已省略，无法安全脱敏>"
    return dsn


class CheckpointBackend(Protocol):
    """两种后端共同的生命周期合同。

    为什么用 Protocol 而不是抽象基类：这里只需要「打开 / 关闭 / 取 saver / 自述」四件事，
    继承会强迫两个实现共享无关状态，也让测试必须构造完整基类。
    Protocol 让 `tests/test_checkpoint_factory.py` 的假实现成为一等公民
    （AGENTS §8：dkd-agent 单测不得连真数据库）。
    """

    @property
    def backend(self) -> str:
        """后端标识（`sqlite` / `postgres`），用于日志与 `/health`。"""
        ...

    @property
    def location(self) -> str:
        """**已脱敏**的位置描述（绝不返回明文口令）。"""
        ...

    @property
    def is_open(self) -> bool:
        """存储是否**真的挂着**。

        为什么健康检查需要这个而不只是后端名：进程被关停后模块级单例仍存在，
        `/health` 只报后端名会变成「存储一切正常」——一个会误导排障的假绿灯（AGENTS §6.4）。
        """
        ...

    @property
    def saver(self) -> Any:
        """LangGraph checkpointer 实例；未 `open()` 前必须抛错而不是返回 None。"""
        ...

    async def open(self) -> Any:
        """建连接 + 建表（幂等 `setup()`）。返回 saver。"""
        ...

    async def close(self) -> None:
        """释放连接；重复调用必须安全。"""
        ...


class SqliteCheckpointStore:
    """SQLite 后端（默认）。单实例部署，零新增基础设施。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stack = AsyncExitStack()
        self._saver: AsyncSqliteSaver | None = None

    @property
    def backend(self) -> str:
        return BACKEND_SQLITE

    @property
    def location(self) -> str:
        return str(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._saver is not None

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
        logger.info("checkpointer ready: backend=%s path=%s", self.backend, self._path)
        return self._saver

    async def close(self) -> None:
        await self._stack.aclose()
        self._saver = None
        logger.info("checkpointer closed: backend=%s path=%s", self.backend, self._path)


class PostgresCheckpointStore:
    """PostgreSQL 后端（多实例部署 / 跨机共享会话时启用）。

    ## 为什么 `psycopg` / `langgraph-checkpoint-postgres` 在这里**惰性导入**

    默认部署是 sqlite，用不到这两个包。若在模块顶部导入，
    「默认路径」就被一个用不到的依赖绑死——本机网络不稳（FIX-18 记录了代理节点偶发不可达）时，
    连 SQLite 模式都起不来。惰性导入把失败限定在「真的选了 postgres」这一条路径上，
    并且给出可直接执行的补救命令。

    ## 未在真库验证（如实标注，AGENTS §9.3 不隐瞒）

    评审稿 D5（本机 PG 账号 / 生产是否允许新增 DB 引擎）**尚未闭环**，
    因此本类**没有跑过真实 PG**：
      - `open()` / `setup()` / `close()` 的 SQL 语义未验证；
      - 评审稿 §8-4 那条未验证假设（账号只有 DML 权限时 `setup()` 首次部署会失败）
        由 `docs/scripts/verify-checkpoint-pg.py` 在 D5 闭环后覆盖。
    工厂分派、配置校验、脱敏与失败降级由 `tests/test_checkpoint_factory.py` 覆盖（零外部依赖）。
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._stack = AsyncExitStack()
        self._saver: Any | None = None

    @property
    def backend(self) -> str:
        return BACKEND_POSTGRES

    @property
    def location(self) -> str:
        return mask_dsn(self._dsn)

    @property
    def is_open(self) -> bool:
        return self._saver is not None

    @property
    def saver(self) -> Any:
        if self._saver is None:
            raise RuntimeError("checkpointer 尚未打开，请先 await store.open()")
        return self._saver

    async def open(self) -> Any:
        """建连接 + `setup()` 幂等建表。"""
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:  # 依赖缺失：给出可执行的补救命令，而不是让它像“连不上库”
            raise RuntimeError(
                "checkpoint_backend=postgres 需要 langgraph-checkpoint-postgres 与 psycopg："
                '请执行 `uv add "langgraph-checkpoint-postgres" "psycopg[binary,pool]"` 后重试'
            ) from exc

        self._saver = await self._stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(self._dsn)
        )
        # setup() 需要**建表权限**；账号只有 DML 权限时这里会失败（评审稿 §8-4）
        await self._saver.setup()
        logger.info("checkpointer ready: backend=%s target=%s", self.backend, self.location)
        return self._saver

    async def close(self) -> None:
        await self._stack.aclose()
        self._saver = None
        logger.info("checkpointer closed: backend=%s", self.backend)


# 向后兼容别名：`tests/test_checkpoint.py`、`tests/test_restock_graph.py` 与
# `docs/scripts/verify-1-6-restock-plan.py`、`docs/scripts/verify-1-7-restock-api.py`
# 已按旧名 `CheckpointStore` 调用。verify 脚本是**已归档的验收证据**，
# 改它们会让「当时验过的代码」与「现在能跑的脚本」不再一致，因此保留别名而不是改调用方。
CheckpointStore = SqliteCheckpointStore


def build_checkpoint_store(settings: Settings) -> CheckpointBackend:
    """按配置造 store（**只构造、不打开**）。

    单独暴露本函数是为了让测试能断言分派结果，且能在不碰真库的前提下
    用假实现替换「打开」这一步。
    """
    if settings.checkpoint_backend == BACKEND_POSTGRES:
        return PostgresCheckpointStore(settings.pg_dsn)
    return SqliteCheckpointStore(settings.sqlite_path)


async def open_checkpoint_store(settings: Settings | None = None) -> CheckpointBackend:
    """进程级单例（由 lifespan 调用）。

    **构造即打开**：避免调用方忘记 await open() 后拿到未初始化的 saver。
    """
    global _store
    resolved = settings or get_settings()
    store = build_checkpoint_store(resolved)
    await store.open()
    _store = store
    return store


def get_checkpoint_store() -> CheckpointBackend:
    if _store is None:
        raise RuntimeError("checkpoint store 未初始化（应由 FastAPI lifespan 启动）")
    return _store


def get_checkpointer() -> Any:
    """供图编译使用。"""
    return get_checkpoint_store().saver
