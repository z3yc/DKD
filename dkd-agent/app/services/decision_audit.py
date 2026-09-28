"""决策留痕的统一写入入口（AGENTS §6.3「每次写操作决策必须留痕」）。

为什么把留痕抽成独立模块，而不是让每个入口自己调 `record_decision`：
**纪律比“写一行 SQL”难得多**，而本项目的留痕有两条入口（1-7b 人工干预、1-9 定时分析），
纪律必须逐字一致，各写一遍必然出现“其中一条把异常吞了”“其中一条把人工参数写进
`llm_output`”这类漂移：

1. **审计失败不得中断业务**：审计是旁路，捕获后只记 WARN + 堆栈（绝不静默吞掉）——
   运营点了「确认」却什么都没发生，比留痕缺一条严重得多；
2. **审计关闭时不写**（`DKD_AGENT_AUDIT_ENABLED=0`）：无库环境（单测/演示）要能跑；
3. **`request_id` 取自全链路 contextvar**（网关或定时任务的 `X-Request-Id`），
   缺失时**写 NULL 而不是编一个像真的 ID**——编出来的 ID 会让 Java 日志按 rid 检索
   永远对不上，掩盖“这条留痕没有链路”这个事实；
4. **人工/定时入口不写 `llm_output`**：不得把人的动作或系统调度伪装成“模型建议的事”，
   否则 3-7 审计页会把两类事情渲染成同一类。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.audit import record_decision
from app.config import Settings
from app.db import get_sessionmaker
from app.logging_conf import current_request_id

logger = logging.getLogger("dkd.agent.services.audit")

# 留痕写入器签名（依赖注入点：单测传假写入器即可，不必连 MySQL，AGENTS §8）
AuditWriter = Callable[..., Awaitable[None]]

# 无网关注入 request_id 时 contextvar 的缺省哨兵值（见 app/logging_conf.py）
NO_REQUEST_ID = "-"


async def default_audit_writer(**kwargs: Any) -> None:
    """默认留痕写入器：自建会话写 `agent_decision_log`（与 `app/api/chat.py` 同口径）。"""
    async with get_sessionmaker()() as session:
        await record_decision(session, **kwargs)


def resolve_request_id() -> str | None:
    """取全链路 request_id；哨兵值/空值一律转 None（见模块 docstring 纪律 3）。"""
    rid = current_request_id()
    return None if rid in (None, "", NO_REQUEST_ID) else rid


async def write_decision(
    *,
    settings: Settings,
    writer: AuditWriter | None,
    scene: int,
    action: str,
    target_type: str,
    target_id: str,
    user_id: int | None,
    trigger_type: int,
    result: int,
    input_context: dict[str, Any],
    error_msg: str | None = None,
) -> None:
    """写一条决策留痕（纪律见模块 docstring，调用方**不需要**再包 try/except）。

    @param settings 取 `audit_enabled`（关闭时直接返回，不调用 writer）
    @param writer 留痕写入器（None 时用默认的 MySQL 实现）
    @param scene 场景码（`agent_decision_log.scene`，补货场景为 2）
    @param action 动作名（`restock.<动作>`，与 DDL 注释示例一致）
    @param target_type / target_id 留痕对象（计划行 / 开关行）
    @param user_id 操作人；定时任务为 None（`create_by` 落 `agent`）
    @param trigger_type 1-用户会话 2-定时任务 3-人工干预
    @param result 0-待处理 1-成功 2-失败 3-被拒（**失败与拒绝必须留痕**，只记成功等于藏问题）
    @param input_context 输入上下文（3-7 审计页的正文，字段要自解释）
    @param error_msg 失败/拒绝原因原文（脱敏由 `record_decision` 统一处理）
    """
    if not settings.audit_enabled:
        return
    effective_writer: AuditWriter = writer or default_audit_writer
    try:
        await effective_writer(
            scene=scene,
            action=action,
            request_id=resolve_request_id(),
            user_id=user_id,
            trigger_type=trigger_type,
            input_context=input_context,
            target_type=target_type,
            target_id=target_id,
            result=result,
            error_msg=error_msg,
        )
    except Exception as exc:  # noqa: BLE001 —— 故意宽捕获：见模块 docstring 纪律 1（WARN + 堆栈，非静默）
        logger.warning(
            "决策留痕写入失败（业务不受影响，需事后补录）action=%s target=%s err=%s",
            action,
            target_id,
            type(exc).__name__,
            exc_info=True,
        )
