"""执行日志采集（agent_log）单测（排期 2-4）。

覆盖任务验收点，**全部使用假 writer，不连 MySQL**（AGENTS §8）：
1. 开关关闭：不挂 handler、不写；
2. 异常不抛：写库失败只计数/首次 WARN，不影响业务；
3. 字段映射与截断：message/error_msg ≤1000、error_msg 只存类型+消息（不落堆栈）；
4. 队列满不阻塞：有界队列丢弃 + 计数，flush 只写已入队的行；
5. flush 批量：按 batch_size 切片调用 writer；
6. shutdown/flush：退出前把剩余日志写完；
7. 安全：record 上额外塞的 LLM 请求体/密钥不会被落库；
8. 后台任务：攒够 batch_size 或被 flush 周期唤醒时自动写。
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest

from app.config import Settings
from app.logging_conf import (
    current_conversation_id,
    current_run_id,
    current_scene,
    reset_conversation_id,
    reset_run_id,
    reset_scene,
    set_conversation_id,
    set_run_id,
    set_scene,
)
from app.obs import log_handler
from app.obs.log_handler import (
    DbLogHandler,
    install_db_log_handler,
    shutdown_db_log_handler,
)

_logger_seq = itertools.count()


class FakeWriter:
    """记录每批写入的假 writer（可模拟 DB 不可用）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.fail = fail

    async def write(self, rows: list[dict[str, Any]]) -> None:
        if self.fail:
            raise RuntimeError("table missing or db down")
        self.batches.append(list(rows))


def make_settings(**overrides: Any) -> Settings:
    """构造 Settings 而不读环境变量/.env（测试里显式给日志相关字段）。"""
    base: dict[str, Any] = {
        "log_db_enabled": True,
        "log_batch_size": 50,
        "log_flush_interval_ms": 60000,
        "log_retention_days": 30,
    }
    base.update(overrides)
    return Settings.model_construct(**base)


@contextlib.asynccontextmanager
async def handler_for(
    settings: Settings,
    writer: FakeWriter,
    *,
    queue_max: int = log_handler.DEFAULT_QUEUE_MAX,
    start: bool = False,
) -> AsyncIterator[tuple[DbLogHandler, logging.Logger]]:
    """挂一个独立 handler 到测试 logger；退出时 stop + 摘除（不留后台任务）。"""
    handler = DbLogHandler(settings, writer=writer, queue_max=queue_max)
    logger = logging.getLogger(f"dkd.agent.test_log_{next(_logger_seq)}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    if start:
        handler.start()
    try:
        yield handler, logger
    finally:
        await handler.stop()
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
async def _clean_db_log_handler() -> AsyncIterator[None]:
    """兜底：用例失败也要卸掉模块级单例，避免后台 task 泄漏到后续用例。"""
    yield
    await shutdown_db_log_handler()


# --- 1. 开关关闭 ---


async def test_disabled_switch_installs_nothing_and_writes_nothing() -> None:
    writer = FakeWriter()
    assert install_db_log_handler(make_settings(log_db_enabled=False), writer=writer) is None
    assert log_handler.db_log_stats()["installed"] is False

    logging.getLogger("dkd.agent.test_disabled").info("不应被采集")
    assert writer.batches == []


# --- 2. 异常不抛（表缺失 / DB 不可用） ---


async def test_writer_failure_does_not_raise_and_warns_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = FakeWriter(fail=True)
    async with handler_for(make_settings(log_batch_size=2), writer) as (handler, logger):
        logger.info("first")
        await handler.flush()  # 不抛异常：观测是旁路
        assert handler.write_errors == 1

        logger.info("second")
        await handler.flush()
        assert handler.write_errors == 2

    # 「DB 不可用只 WARN 一次」：第二次失败静默，避免把日志刷爆
    warnings = [r for r in caplog.records if r.name == "dkd.obs.log"]
    assert len(warnings) == 1
    assert "agent_log 写入失败" in warnings[0].getMessage()


# --- 3. 字段映射与截断 ---


async def test_emit_maps_fields_and_reads_record_attr_before_contextvar() -> None:
    writer = FakeWriter()
    settings = make_settings(log_batch_size=2)
    async with handler_for(settings, writer) as (handler, logger):
        tok_run = set_run_id("run-9")
        tok_scene = set_scene("restock")
        tok_conv = set_conversation_id("conv-ctx")
        try:
            logger.info(
                "hello %s",
                "world",
                extra={
                    "conversation_id": "conv-ext",  # record 属性优先于 contextvar
                    "task_id": "T-1",
                    "node": "collect_inventory",
                    "status": "ok",
                    "duration_ms": 12.6,
                    "request_id": "rid-x",
                },
            )
            logger.info("second")
            await handler.flush()
        finally:
            reset_conversation_id(tok_conv)
            reset_scene(tok_scene)
            reset_run_id(tok_run)

    assert len(writer.batches) == 1
    first, second = writer.batches[0]
    assert first["message"] == "hello world"
    assert first["level"] == "INFO"
    assert first["logger"].startswith("dkd.agent.test_log_")
    assert first["run_id"] == "run-9"
    assert first["scene"] == "restock"
    assert first["conversation_id"] == "conv-ext"
    assert first["task_id"] == "T-1"
    assert first["node"] == "collect_inventory"
    assert first["status"] == "ok"
    assert first["request_id"] == "rid-x"
    assert first["duration_ms"] == 13  # 12.6 四舍五入到毫秒整数
    assert first["create_by"] == "python-agent"
    assert isinstance(first["create_time"], datetime)

    # 无 record 属性时回落 contextvar；没有 request_id 时写 NULL（不编造，纪律 3）
    assert second["conversation_id"] == "conv-ctx"
    assert second["request_id"] is None
    assert second["run_id"] == "run-9"
    assert second["scene"] == "restock"
    assert second["duration_ms"] is None


async def test_exception_logs_type_and_message_without_traceback() -> None:
    writer = FakeWriter()
    async with handler_for(make_settings(), writer) as (handler, logger):
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("处理失败")
        await handler.flush()

    row = writer.batches[0][0]
    assert row["message"] == "处理失败"
    assert row["error_msg"] == "ValueError: boom"
    # 不落堆栈全文（可能带参数值/内部路径）
    assert "Traceback" not in (row["error_msg"] or "")


async def test_message_and_error_msg_are_truncated_to_1000() -> None:
    writer = FakeWriter()
    async with handler_for(make_settings(), writer) as (handler, logger):
        logger.info("m" * 1500)
        try:
            raise ValueError("e" * 2000)
        except ValueError:
            logger.exception("long error")
        await handler.flush()

    long_message, long_error = writer.batches[0]
    assert len(long_message["message"]) == log_handler.MAX_MESSAGE_LEN == 1000
    assert long_error["error_msg"].startswith("ValueError: ")
    assert len(long_error["error_msg"]) == log_handler.MAX_ERROR_LEN == 1000
    assert long_message["error_msg"] is None


async def test_extra_llm_body_and_secrets_are_never_persisted() -> None:
    writer = FakeWriter()
    async with handler_for(make_settings(), writer) as (handler, logger):
        logger.info(
            "call llm",
            extra={
                "llm_request_body": "PROMPT-SECRET-13800000000",
                "api_key": "sk-unit-test-secret",
            },
        )
        await handler.flush()

    dumped = str(writer.batches[0][0])
    # 字段白名单：record.__dict__ 里的其他内容一律不进入落库路径（AGENTS §7.8）
    assert "PROMPT-SECRET" not in dumped
    assert "sk-unit-test-secret" not in dumped
    assert "13800000000" not in dumped


async def test_below_info_and_foreign_loggers_are_ignored() -> None:
    writer = FakeWriter()
    async with handler_for(make_settings(), writer) as (handler, logger):
        logger.setLevel(logging.DEBUG)
        logger.debug("debug 噪声")  # 级别 < INFO：handler 直接过滤
        foreign = logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="三方库日志",
            args=(),
            exc_info=None,
        )
        handler.emit(foreign)  # 即使被直接 emit，也不在采集范围
        assert handler.queue_size == 0
        await handler.flush()

    assert writer.batches == []


# --- 4. 队列满不阻塞 ---


async def test_queue_full_drops_without_blocking() -> None:
    writer = FakeWriter()
    settings = make_settings(log_batch_size=50)  # 不触发“攒满一批”的唤醒
    async with handler_for(settings, writer, queue_max=2) as (handler, logger):
        for index in range(5):
            logger.info("line-%d", index)  # 不得抛异常/阻塞
        assert handler.queue_size == 2
        assert handler.dropped == 3

        await handler.flush()

    # 只写出已入队的 2 条；丢弃行不会补写（补写会在库不可用时无限膨胀内存）
    assert len(writer.batches) == 1
    assert [row["message"] for row in writer.batches[0]] == ["line-0", "line-1"]


# --- 5. flush 批量切片 ---


async def test_flush_writes_batches_by_batch_size() -> None:
    writer = FakeWriter()
    settings = make_settings(log_batch_size=2)
    async with handler_for(settings, writer) as (handler, logger):
        for index in range(5):
            logger.info("line-%d", index)
        await handler.flush()

    assert [len(batch) for batch in writer.batches] == [2, 2, 1]
    assert [row["message"] for batch in writer.batches for row in batch] == [
        f"line-{index}" for index in range(5)
    ]


# --- 6. shutdown 前 flush 剩余日志 ---


async def test_shutdown_flushes_remaining_and_detaches() -> None:
    writer = FakeWriter()
    settings = make_settings(log_batch_size=50)
    handler = install_db_log_handler(settings, writer=writer)
    assert handler is not None
    logger = logging.getLogger("dkd.agent.test_shutdown")
    logger.setLevel(logging.INFO)
    try:
        logger.info("最后一行")
        await shutdown_db_log_handler()
    finally:
        logger.setLevel(logging.NOTSET)

    assert log_handler.db_log_stats()["installed"] is False
    assert handler not in logging.getLogger("dkd.agent").handlers
    assert sum(len(batch) for batch in writer.batches) == 1
    assert writer.batches[0][0]["message"] == "最后一行"


# --- 7. 后台 task：攒满 batch_size / 到 flush 周期自动写 ---


async def test_background_task_flushes_when_batch_reached() -> None:
    writer = FakeWriter()
    settings = make_settings(log_batch_size=2, log_flush_interval_ms=60000)
    async with handler_for(settings, writer, start=True) as (_handler, logger):
        logger.info("a")
        logger.info("b")  # 攒满 2 条 → 唤醒后台 task 立即写
        for _ in range(100):
            if writer.batches:
                break
            await asyncio.sleep(0.01)
        assert sum(len(batch) for batch in writer.batches) == 2


async def test_background_task_flushes_on_interval() -> None:
    writer = FakeWriter()
    settings = make_settings(log_batch_size=100, log_flush_interval_ms=50)
    async with handler_for(settings, writer, start=True) as (_handler, logger):
        logger.info("低流量也要在周期内落库")
        for _ in range(100):
            if writer.batches:
                break
            await asyncio.sleep(0.02)
        assert sum(len(batch) for batch in writer.batches) == 1


# --- 8. 上下文辅助函数 ---


async def test_context_helpers_set_and_reset() -> None:
    tok_run = set_run_id("r1")
    tok_conv = set_conversation_id("c1")
    tok_scene = set_scene("fault")
    try:
        assert current_run_id() == "r1"
        assert current_conversation_id() == "c1"
        assert current_scene() == "fault"
    finally:
        reset_scene(tok_scene)
        reset_conversation_id(tok_conv)
        reset_run_id(tok_run)

    assert current_run_id() == log_handler.UNSET
    assert current_conversation_id() == log_handler.UNSET
    assert current_scene() == log_handler.UNSET
