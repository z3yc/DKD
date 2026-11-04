"""执行日志异步批量写入 `agent_log`（排期 2-4）。

## 为什么需要它

`agent_run` / `agent_step` / `agent_tool_call` 是**过程观测**（结构化、按运行组织）；
排障时还需要“某一时刻哪一行 WARN 说了什么”（非结构化、按时间组织）。
本模块把 `dkd.agent.*` 的日志（级别 >= INFO）落进 `agent_log`，供 Java 只读接口
与后台「执行日志」页按时间/request_id/级别检索（DDL 见 `docs/ddl/agent_config.sql`）。

## 五条纪律（改动前先读这里）

1. **同步段只入队、绝不阻塞**：`emit()` 只做字段映射 + 入有界队列；写库全部在后台
   asyncio task 里。队列满时**丢弃并计数**，不阻塞、绝不抛异常——日志是旁路，
   不能反过来拖垮业务请求（与 `app/obs/run_recorder.py` 纪律 1 一致）。
2. **有界内存**：队列长度上限 `queue_max`；丢弃计数在下次 flush 时用 WARN 播报一次，
   避免“日志写不进去 → 每个请求都打一条 WARN”把日志刷爆。
3. **字段白名单**：只取下方显式列出的 record 属性与 contextvars，
   **绝不 dump `record.__dict__`/`extra`**；这样即使业务用 `extra=` 顺手塞了
   LLM 请求体或密钥，也不会被落库（AGENTS §7.8、§6.1）。`error_msg` 只记
   异常类型 + 消息，**不落堆栈全文**（堆栈可能带参数值与内部路径）。
4. **旁路不阻断启动**：表未建 / DB 不可达时首次失败 WARN 一次，之后静默降级；
   `install_db_log_handler()` 不探测数据库，因此不会把启动挂住。
5. **不 DELETE**（AGENTS §7.4）：本模块只有 INSERT；30 天清理是独立 SQL 模板，
   由 Java/运维执行（Agent 账号无 DELETE 权限，见 `docs/scripts/cleanup-agent-log.sql`）。

## 上下文

`request_id`（已有）+ `run_id`/`conversation_id`/`scene`（`app/logging_conf.py` 的
contextvars）在 `emit()` 时刻读取；record 上显式带 `extra={"conversation_id": ...}`
等属性时**以 record 属性优先**——这样 chat 路由等调用方不必为了入库去改业务模块，
按需在关键日志点用 `extra=` 补充即可。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import text

from app.config import Settings
from app.db import get_sessionmaker
from app.logging_conf import (
    UNSET,
    current_conversation_id,
    current_request_id,
    current_run_id,
    current_scene,
)

# 采集范围：只处理 `dkd.agent` 树下的日志（handler 也挂在同一个 logger 上，
# 这里再判一次是为了防止有人把 handler 挪到 root 后把三方库日志也写进库）。
LOGGER_PREFIX = "dkd.agent"

# handler 自身的 WARN 用**不属于** dkd.agent 树的 logger：
# 若用 dkd.agent.* 记录，WARN 会再次进入本 handler 的队列，队列满时会形成反馈循环。
_META_LOGGER = logging.getLogger("dkd.obs.log")

CREATE_BY = "python-agent"
MAX_MESSAGE_LEN = 1000
MAX_ERROR_LEN = 1000
DEFAULT_QUEUE_MAX = 5000

_INSERT_SQL = text(
    """
    INSERT INTO agent_log
        (create_time, level, logger, run_id, conversation_id, task_id, scene, node,
         status, message, error_msg, request_id, duration_ms, create_by)
    VALUES
        (:create_time, :level, :logger, :run_id, :conversation_id, :task_id, :scene, :node,
         :status, :message, :error_msg, :request_id, :duration_ms, :create_by)
    """
)


class LogWriter(Protocol):
    """批量写入接口（单测注入假实现，AGENTS §8：测试不得连真库）。"""

    async def write(self, rows: list[dict[str, Any]]) -> None:
        """写入一批 agent_log 行。"""


class SqlLogWriter:
    """默认实现：一条 executemany INSERT 写一批（无 DDL/无 DELETE）。"""

    async def write(self, rows: list[dict[str, Any]]) -> None:
        """批量 INSERT；由调用方负责异常兜底。"""
        if not rows:
            return
        async with get_sessionmaker()() as session:
            await session.execute(_INSERT_SQL, rows)
            await session.commit()


def _text_of(value: Any, limit: int) -> str | None:
    """转字符串并截断；空值统一为 None（不写 `-`/空串这种假值）。"""
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered[:limit] if rendered else None


def _pick(value: Any, fallback: str) -> str | None:
    """record 属性优先、contextvar 兜底；哨兵值转 None（不编造 ID）。"""
    if value is not None:
        return _text_of(value, 64)
    return None if fallback in (None, "", UNSET) else fallback


def _error_msg(record: logging.LogRecord) -> str | None:
    """异常摘要 = `类型: 消息`，**不含堆栈**（AGENTS §6.1 / 本模块纪律 3）。"""
    exc_info = record.exc_info
    if not exc_info or exc_info[0] is None:
        return None
    exc = exc_info[1]
    if exc is None:
        return None
    return _text_of(f"{type(exc).__name__}: {exc}", MAX_ERROR_LEN)


def _message_of(record: logging.LogRecord) -> str | None:
    """取日志正文；格式化失败时退回原 msg（handler 不允许抛异常）。"""
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 —— 日志 handler 的格式化失败不得影响调用方
        message = str(record.msg)
    return _text_of(message, MAX_MESSAGE_LEN)


def _duration_of(record: logging.LogRecord) -> int | None:
    """耗时统一成毫秒整数；非法值置 None（不猜、不四舍五入到 0）。"""
    value = getattr(record, "duration_ms", None)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def record_to_row(record: logging.LogRecord) -> dict[str, Any] | None:
    """把一条 LogRecord 映射成 agent_log 行；非采集范围返回 None。

    只读**显式白名单**里的属性：`record_dict` 里的其他内容（包括可能存在的
    LLM 请求体/密钥）一律不进入落库路径。
    """
    if record.name != LOGGER_PREFIX and not record.name.startswith(LOGGER_PREFIX + "."):
        return None
    return {
        # DATETIME(3)：record.created 是 epoch 秒（含小数），fromtimestamp 取本地时间，
        # 与 MySQL 侧 NOW() 的时区口径一致（部署约定 UTC+8，见 .env.example）。
        "create_time": datetime.fromtimestamp(record.created),
        "level": record.levelname,
        "logger": _text_of(record.name, 128),
        "run_id": _pick(getattr(record, "run_id", None), current_run_id()),
        "conversation_id": _pick(
            getattr(record, "conversation_id", None), current_conversation_id()
        ),
        "task_id": _text_of(getattr(record, "task_id", None), 64),
        "scene": _pick(getattr(record, "scene", None), current_scene()),
        "node": _text_of(getattr(record, "node", None), 64),
        "status": _text_of(getattr(record, "status", None), 32),
        "message": _message_of(record),
        "error_msg": _error_msg(record),
        "request_id": _pick(getattr(record, "request_id", None), current_request_id()),
        "duration_ms": _duration_of(record),
        "create_by": CREATE_BY,
    }


class DbLogHandler(logging.Handler):
    """把 `dkd.agent.*` 日志异步批量写入 `agent_log` 的 logging.Handler。

    同步路径（emit）只做映射 + 入队；后台 task 按 `batch_size` / `flush_interval_ms`
    写库；`flush()` 可显式触发（单测与 shutdown 用）。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        writer: LogWriter | None = None,
        queue_max: int = DEFAULT_QUEUE_MAX,
    ) -> None:
        super().__init__(level=logging.INFO)
        self._settings = settings
        self._writer: LogWriter = writer or SqlLogWriter()
        self._queue_max = max(1, int(queue_max))
        self._batch_size = max(1, int(settings.log_batch_size))
        self._interval_s = max(settings.log_flush_interval_ms / 1000, 0.05)
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        # 观测计数（health 端点只读它们；不参与业务判断）
        self.dropped = 0
        self.write_errors = 0
        self._dropped_reported = 0
        self._error_warned = False

    # --- 生命周期 ---

    def start(self) -> None:
        """启动后台 flush task；没有运行中的事件循环时只保留手动 flush 能力。"""
        if self._task is not None:
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
            _META_LOGGER.warning(
                "agent_log 采集未启动后台任务：当前没有运行中的事件循环（仅手动 flush 可用）"
            )
            return
        self._task = self._loop.create_task(self._run(), name="agent-log-flush")

    async def stop(self) -> None:
        """停止后台 task、flush 剩余日志（shutdown 必须调用，否则进程退出会丢最后一批）。"""
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.flush()
        self.close()

    @property
    def queue_size(self) -> int:
        """当前队列长度（health 排障用）。"""
        with self._lock:
            return len(self._queue)

    # --- 同步路径：只入队，不阻塞、不抛 ---

    def emit(self, record: logging.LogRecord) -> None:
        """把日志放入有界队列；**绝不阻塞/抛异常**（日志是旁路）。"""
        try:
            row = record_to_row(record)
            if row is None:
                return
            with self._lock:
                if len(self._queue) >= self._queue_max:
                    self.dropped += 1
                    return
                self._queue.append(row)
                reached_batch = len(self._queue) >= self._batch_size
            if reached_batch:
                self._wake_background()
        except Exception:  # noqa: BLE001 —— 日志 handler 不得影响业务调用方
            # 这里连 WARN 都不能走正常链路（可能在 emit 里再次进入本 handler），
            # 计数后由下次 flush 的 meta logger 播报。
            self.dropped += 1

    def _wake_background(self) -> None:
        """通知后台 task 立即 flush（攒够一批时）。

        跨线程/跨上下文调用时事件循环可能已关闭（进程退出阶段），失败静默——
        此时日志本来也要丢，不能因此让业务线程抛异常。
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._wake.set)

    # --- 异步路径：批量写 ---

    async def _run(self) -> None:
        """后台任务：等一批或等一个 flush 周期，然后写库。"""
        try:
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._interval_s)
                self._wake.clear()  # 到点也强制 flush（低流量时也能在 1s 内落库）
                await self.flush()
        except asyncio.CancelledError:
            raise

    def _drain(self) -> tuple[list[dict[str, Any]], int]:
        """一次性取走队列（持锁时间仅拷贝/清空，写库在锁外进行）。"""
        with self._lock:
            rows = list(self._queue)
            self._queue.clear()
            return rows, self.dropped

    def _report_drops(self, dropped_total: int) -> None:
        """播报新增丢弃数：只报增量，避免每轮 flush 都刷同一条 WARN。"""
        newly = dropped_total - self._dropped_reported
        if newly <= 0:
            return
        self._dropped_reported = dropped_total
        _META_LOGGER.warning(
            "agent_log 队列已满：丢弃 %d 条日志（写库过慢或数据库不可用；队列上限 %d）",
            newly,
            self._queue_max,
        )

    async def flush(self) -> None:
        """把当前队列按 batch_size 切片写出；任何失败都只计数/首次 WARN，不抛异常。"""
        rows, dropped_total = self._drain()
        self._report_drops(dropped_total)
        for start in range(0, len(rows), self._batch_size):
            chunk = rows[start : start + self._batch_size]
            try:
                await self._writer.write(chunk)
            except Exception as exc:  # noqa: BLE001 —— 旁路写失败不得中断业务/关闭流程
                self.write_errors += 1
                if not self._error_warned:
                    self._error_warned = True
                    _META_LOGGER.warning(
                        "agent_log 写入失败（表未建/DB 不可用；仅告警一次，业务不受影响）：%s: %s",
                        type(exc).__name__,
                        exc,
                    )


# --- 模块级单例：由 app/main.py 的 lifespan 挂载/卸载 ---

_handler: DbLogHandler | None = None


def install_db_log_handler(
    settings: Settings,
    *,
    writer: LogWriter | None = None,
    queue_max: int = DEFAULT_QUEUE_MAX,
) -> DbLogHandler | None:
    """挂载执行日志 handler；开关关闭或无事件循环时安全降级，**不阻断启动**。

    @param settings 服务配置（读 log_db_enabled / batch_size / flush_interval_ms）
    @param writer 测试注入的假写入器；None 用默认 SqlLogWriter
    @param queue_max 队列长度上限（测试可调小以验证丢弃路径）
    @return 挂载的 handler；开关关闭或已挂载时返回当前/None
    """
    global _handler
    if not settings.log_db_enabled:
        return None
    if _handler is not None:
        return _handler
    handler = DbLogHandler(settings, writer=writer, queue_max=queue_max)
    logging.getLogger(LOGGER_PREFIX).addHandler(handler)
    handler.start()
    _handler = handler
    _META_LOGGER.info(
        "agent_log 执行日志采集已挂载（batch_size=%d, flush_interval=%dms）",
        handler._batch_size,
        settings.log_flush_interval_ms,
    )
    return handler


async def shutdown_db_log_handler() -> None:
    """flush 剩余日志并卸载 handler（lifespan finally 中、关闭连接池之前调用）。"""
    global _handler
    handler = _handler
    if handler is None:
        return
    _handler = None
    try:
        await handler.stop()
    finally:
        logging.getLogger(LOGGER_PREFIX).removeHandler(handler)


def db_log_stats() -> dict[str, Any]:
    """执行日志采集运行状态（health 端点用；不连库、不触发 flush）。"""
    handler = _handler
    if handler is None:
        return {"installed": False, "queue_size": 0, "dropped": 0, "write_errors": 0}
    return {
        "installed": True,
        "queue_size": handler.queue_size,
        "dropped": handler.dropped,
        "write_errors": handler.write_errors,
    }
