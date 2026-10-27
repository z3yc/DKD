"""运行观测写入器：agent_run / agent_step / agent_tool_call（F-02，2026-10-26）。

## 为什么需要它

`agent_decision_log` 是**决策**粒度（一次写操作一条），回答"谁在什么时候点了什么、结果如何"；
但运营问的是"这一轮为什么这么建议"——那需要**过程**粒度：派给了谁、调了哪个工具、拿到几行、花了多久。
三张表用 `run_id` 串起来，补货与故障两个场景共用（`scene_code` 区分）。

## 五条纪律（与 `services/decision_audit.py` 对齐，改动前先读这里）

1. **观测失败不得中断业务**：所有写入都包在 try 里，失败只记 WARN + 堆栈；
2. **关闭时不写**（`DKD_AGENT_OBS_ENABLED=0`）：单测/演示没有库时也要能跑；
3. **`request_id` 缺失写 NULL，不编造**：编一个像真的 ID 会让跨进程日志按 rid 检索永远对不上；
4. **只 INSERT/UPDATE，不 DELETE**（AGENTS §7.4）；状态更新由 run 的状态机驱动；
5. **参数只存摘要，不存原文**（AGENTS §7.8）：`params_digest` 是 sha256 前缀，
   防止手机号等 PII 落库。

## 幂等

中断恢复会**从头重跑被中断的节点**，同一 `step_no` / `(step_no, call_seq)` 会被写两次；
写入侧用 `INSERT ... ON DUPLICATE KEY UPDATE` 覆盖为最新一次，页面不会看到重复轮次。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text

from app.config import Settings
from app.db import get_sessionmaker
from app.logging_conf import current_request_id

logger = logging.getLogger("dkd.agent.obs.run")

# 无网关注入 request_id 时 contextvar 的缺省哨兵值（见 app/logging_conf.py）
NO_REQUEST_ID = "-"

# 状态/场景白名单：拼错（runing / FAULT）会让页面永远筛不到，因此在写入前显式校验
RUN_STATUSES = frozenset({"running", "waiting_confirm", "done", "failed", "interrupted"})
TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "interrupted"})
SCENE_CODES = frozenset({"restock", "fault", "chat"})
STEP_STATUSES = frozenset({"ok", "error", "timeout", "skipped"})
RESULT_STATUSES = frozenset({"ok", "error", "timeout", "truncated"})
TRIGGER_TYPES = frozenset({1, 2, 3})

# 启动扫描写入的终止原因（页面直接展示给运营）
STALE_RUN_REASON = "服务重启，运行中断，可重新发起"


class RunStore(Protocol):
    """观测写入器接口（单测注入假实现，AGENTS §8 要求测试不连真库）。"""

    async def insert_run(self, **kwargs: Any) -> None:
        """插入一条 run。"""

    async def update_run(self, **kwargs: Any) -> None:
        """更新 run 状态。"""

    async def insert_step(self, **kwargs: Any) -> None:
        """插入/覆盖一条 step。"""

    async def insert_tool_call(self, **kwargs: Any) -> None:
        """插入/覆盖一条工具调用。"""

    async def interrupt_stale_runs(self, **kwargs: Any) -> int:
        """把遗留 running 改成 interrupted，返回影响行数。"""


def digest_params(params: dict[str, Any] | None) -> str | None:
    """参数摘要（**不落原文**）。

    为什么用 sha256 而不是截断字符串：截断后仍可能带出手机号前几位；
    摘要只用于"两次调用是否同一参数"的对账，不需要可读性。

    @param params 工具入参（可为 None）
    @return `sha256:<前16位>`；入参为空时返回 None
    """
    if not params:
        return None
    try:
        canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        logger.warning("工具参数无法序列化，摘要写 None", exc_info=True)
        return None
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _resolve_request_id() -> str | None:
    """取全链路 request_id；哨兵/空值转 None（纪律 3）。"""
    rid = current_request_id()
    return None if rid in (None, "", NO_REQUEST_ID) else rid


def _dump_evidence(evidence: dict[str, Any] | list[Any] | None) -> str | None:
    """证据/提案序列化（中文不转义，页面直接可读）。"""
    if evidence is None:
        return None
    try:
        return json.dumps(evidence, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        logger.warning("证据 JSON 序列化失败，写 None", exc_info=True)
        return None


def _allowed(value: Any, allowed: frozenset[Any], field: str) -> bool:
    """白名单校验；不合法只 WARN 并跳过（纪律 1：旁路不得中断业务）。"""
    if value in allowed:
        return True
    logger.warning("运行观测参数非法，已跳过写入：%s=%r（允许：%s）", field, value, sorted(allowed))
    return False


class SqlRunStore:
    """默认实现：直接写 dkd 库的 agent_run / agent_step / agent_tool_call。"""

    async def insert_run(
        self,
        *,
        run_id: str,
        scene_code: str,
        subject_type: str,
        subject_id: str,
        request_id: str | None,
        trigger_type: int,
        config_version: int | None,
        create_by: str | None,
    ) -> None:
        """插入新 run（status 固定从 running 开始）。"""
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO agent_run
                        (run_id, scene_code, subject_type, subject_id, request_id, trigger_type,
                         status, config_version, started_at, create_by, create_time, update_time)
                    VALUES
                        (:run_id, :scene_code, :subject_type, :subject_id,
                         :request_id, :trigger_type,
                         'running', :config_version, NOW(), :create_by, NOW(), NOW())
                    """
                ),
                {
                    "run_id": run_id,
                    "scene_code": scene_code,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "request_id": request_id,
                    "trigger_type": trigger_type,
                    "config_version": config_version,
                    "create_by": create_by,
                },
            )
            await session.commit()

    async def update_run(
        self,
        *,
        run_id: str,
        status: str,
        stop_reason: str | None,
        finished: bool,
    ) -> None:
        """更新 run 状态；只有终态才写 finished_at（waiting_confirm 仍是进行中）。"""
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    """
                    UPDATE agent_run
                       SET status = :status,
                           stop_reason = COALESCE(:stop_reason, stop_reason),
                           finished_at = CASE WHEN :finished = 1 THEN NOW() ELSE finished_at END,
                           update_time = NOW()
                     WHERE run_id = :run_id AND del_flag = '0'
                    """
                ),
                {
                    "run_id": run_id,
                    "status": status,
                    "stop_reason": stop_reason,
                    "finished": 1 if finished else 0,
                },
            )
            await session.commit()

    async def insert_step(
        self,
        *,
        run_id: str,
        step_no: int,
        role_code: str,
        action: str,
        summary: str | None,
        evidence_json: str | None,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        status: str,
        error_msg: str | None,
    ) -> None:
        """写入/覆盖一轮调度（uk(run_id, step_no) 保证重跑幂等）。"""
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO agent_step
                        (run_id, step_no, role_code, action, summary, evidence_json,
                         input_tokens, output_tokens, duration_ms, status, error_msg)
                    VALUES
                        (:run_id, :step_no, :role_code, :action, :summary, :evidence_json,
                         :input_tokens, :output_tokens, :duration_ms, :status, :error_msg)
                    ON DUPLICATE KEY UPDATE
                        role_code = VALUES(role_code),
                        action = VALUES(action),
                        summary = VALUES(summary),
                        evidence_json = VALUES(evidence_json),
                        input_tokens = VALUES(input_tokens),
                        output_tokens = VALUES(output_tokens),
                        duration_ms = VALUES(duration_ms),
                        status = VALUES(status),
                        error_msg = VALUES(error_msg)
                    """
                ),
                {
                    "run_id": run_id,
                    "step_no": step_no,
                    "role_code": role_code,
                    "action": action,
                    "summary": summary,
                    "evidence_json": evidence_json,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": duration_ms,
                    "status": status,
                    "error_msg": error_msg,
                },
            )
            await session.commit()

    async def insert_tool_call(
        self,
        *,
        run_id: str,
        step_no: int,
        call_seq: int,
        tool_code: str,
        params_digest: str | None,
        row_count: int,
        duration_ms: int,
        result_status: str,
        error_msg: str | None,
    ) -> None:
        """写入/覆盖一次工具调用（uk(run_id, step_no, call_seq)）。"""
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO agent_tool_call
                        (run_id, step_no, call_seq, tool_code, params_digest,
                         row_count, duration_ms, result_status, error_msg)
                    VALUES
                        (:run_id, :step_no, :call_seq, :tool_code, :params_digest,
                         :row_count, :duration_ms, :result_status, :error_msg)
                    ON DUPLICATE KEY UPDATE
                        tool_code = VALUES(tool_code),
                        params_digest = VALUES(params_digest),
                        row_count = VALUES(row_count),
                        duration_ms = VALUES(duration_ms),
                        result_status = VALUES(result_status),
                        error_msg = VALUES(error_msg)
                    """
                ),
                {
                    "run_id": run_id,
                    "step_no": step_no,
                    "call_seq": call_seq,
                    "tool_code": tool_code,
                    "params_digest": params_digest,
                    "row_count": row_count,
                    "duration_ms": duration_ms,
                    "result_status": result_status,
                    "error_msg": error_msg,
                },
            )
            await session.commit()

    async def interrupt_stale_runs(self, *, older_than_minutes: int, reason: str) -> int:
        """把超过阈值的 running 改成 interrupted（F-05 启动扫描）。"""
        threshold = datetime.now() - timedelta(minutes=older_than_minutes)
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE agent_run
                       SET status = 'interrupted',
                           stop_reason = :reason,
                           finished_at = NOW(),
                           update_time = NOW()
                     WHERE status = 'running'
                       AND del_flag = '0'
                       AND started_at < :threshold
                    """
                ),
                {"reason": reason, "threshold": threshold},
            )
            await session.commit()
            return int(result.rowcount or 0)


async def _write(settings: Settings, store: RunStore | None, op: str, **kwargs: Any) -> None:
    """统一写入入口：开关判断 + 失败兜底（纪律 1/2）。"""
    if not settings.obs_enabled:
        return
    target: RunStore = store or SqlRunStore()
    try:
        method = getattr(target, op)
        await method(**kwargs)
    except Exception as exc:  # noqa: BLE001 —— 故意宽捕获：观测是旁路，不得中断业务
        logger.warning(
            "运行观测写入失败（业务不受影响）op=%s err=%s",
            op,
            type(exc).__name__,
            exc_info=True,
        )


async def start_run(
    settings: Settings,
    store: RunStore | None,
    *,
    run_id: str,
    scene_code: str,
    subject_type: str,
    subject_id: str,
    trigger_type: int = 1,
    config_version: int | None = None,
    create_by: str | None = None,
) -> None:
    """开始一次运行（写 agent_run，status=running）。"""
    if not settings.obs_enabled:
        return
    if not _allowed(scene_code, SCENE_CODES, "scene_code"):
        return
    if not _allowed(trigger_type, TRIGGER_TYPES, "trigger_type"):
        return
    await _write(
        settings,
        store,
        "insert_run",
        run_id=run_id,
        scene_code=scene_code,
        subject_type=subject_type,
        subject_id=subject_id,
        request_id=_resolve_request_id(),
        trigger_type=trigger_type,
        config_version=config_version,
        create_by=create_by,
    )


async def finish_run(
    settings: Settings,
    store: RunStore | None,
    *,
    run_id: str,
    status: str,
    stop_reason: str | None = None,
) -> None:
    """结束一次运行（终态写 finished_at；waiting_confirm 不算终态）。"""
    if not settings.obs_enabled:
        return
    if not _allowed(status, RUN_STATUSES, "run.status"):
        return
    await _write(
        settings,
        store,
        "update_run",
        run_id=run_id,
        status=status,
        stop_reason=stop_reason,
        finished=status in TERMINAL_RUN_STATUSES,
    )


async def record_step(
    settings: Settings,
    store: RunStore | None,
    *,
    run_id: str,
    step_no: int,
    role_code: str,
    action: str,
    summary: str | None = None,
    evidence: dict[str, Any] | list[Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    status: str = "ok",
    error_msg: str | None = None,
) -> None:
    """记录一轮调度（写 agent_step）。"""
    if not settings.obs_enabled:
        return
    if step_no < 1:
        logger.warning("运行观测参数非法，已跳过写入：step_no=%r（从 1 开始）", step_no)
        return
    if not _allowed(status, STEP_STATUSES, "step.status"):
        return
    await _write(
        settings,
        store,
        "insert_step",
        run_id=run_id,
        step_no=step_no,
        role_code=role_code,
        action=action,
        summary=summary,
        evidence_json=_dump_evidence(evidence),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        status=status,
        error_msg=error_msg,
    )


async def record_tool_call(
    settings: Settings,
    store: RunStore | None,
    *,
    run_id: str,
    step_no: int,
    call_seq: int,
    tool_code: str,
    params: dict[str, Any] | None = None,
    row_count: int = 0,
    duration_ms: int = 0,
    result_status: str = "ok",
    error_msg: str | None = None,
) -> None:
    """记录一次工具调用（写 agent_tool_call；参数只存摘要）。"""
    if not settings.obs_enabled:
        return
    if step_no < 1 or call_seq < 1:
        logger.warning(
            "运行观测参数非法，已跳过写入：step_no=%r call_seq=%r（均从 1 开始）",
            step_no,
            call_seq,
        )
        return
    if not _allowed(result_status, RESULT_STATUSES, "tool.result_status"):
        return
    await _write(
        settings,
        store,
        "insert_tool_call",
        run_id=run_id,
        step_no=step_no,
        call_seq=call_seq,
        tool_code=tool_code,
        params_digest=digest_params(params),
        row_count=row_count,
        duration_ms=duration_ms,
        result_status=result_status,
        error_msg=error_msg,
    )


async def sweep_stale_runs(
    settings: Settings,
    store: RunStore | None,
    *,
    minutes: int | None = None,
    reason: str = STALE_RUN_REASON,
) -> int:
    """启动扫描：把遗留 running 改成 interrupted，返回被收尾的条数（失败返回 0）。"""
    if not settings.obs_enabled:
        return 0
    target: RunStore = store or SqlRunStore()
    effective_minutes = minutes if minutes is not None else settings.run_stale_minutes
    try:
        swept = await target.interrupt_stale_runs(
            older_than_minutes=effective_minutes,
            reason=reason,
        )
        return int(swept)
    except Exception as exc:  # noqa: BLE001 —— 观测是旁路，启动不能被它拖垮
        logger.warning(
            "启动扫描失败（不影响服务启动，页面可能残留 running）err=%s",
            type(exc).__name__,
            exc_info=True,
        )
        return 0
