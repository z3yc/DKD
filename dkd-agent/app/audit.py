"""决策留痕与成本计量（任务 0-12）。

三条纪律：
1. **每次写操作决策必留痕**（AGENTS §6.3）——agent_decision_log 是不可选项；
2. **脱敏后落库**（AGENTS §7.8）——手机号/订单号/支付信息在写入前掩码；
3. **留痕失败不得中断主流程**——审计是旁路，不能因为审计库抖动让用户对话失败；
   但要打 WARN + 带 request_id（§6.4），便于事后补录。

成本计量口径：按 `agent_message` 的 token 字段聚合（用户/场景/日），
模型名一律记**服务端实际返回的 model**（实测 deepseek-chat 会路由到 deepseek-flash）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import CHAR, JSON, BigInteger, DateTime, Integer, Numeric, String, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger("dkd.agent.audit")

# SQLite 兼容：BigInteger 主键在 SQLite 上无法自增，降到 Integer（仅影响测试环境）
_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")

# JSON 列统一用 `none_as_null=True`：SQLAlchemy 默认会把 Python `None` 序列化成 JSON 文本 `null`，
# 于是「没有 LLM 输出」在库里是 JSON null 而不是 SQL NULL——`IS NULL` 查不到，
# `json_extract` 还会返回字符串 'null'。DDL 声明的是 `DEFAULT NULL`，这里对齐它：
# 人工干预（1-7b）没有 llm_output，落库必须是 NULL，否则审计 SQL 会把「人做的事」
# 当成「模型输出过 null」（AGENTS §9.3 不隐瞒）。
_JSON = JSON(none_as_null=True)

# 脱敏规则（AGENTS §7.8）：手机号、16~19 位卡号/订单号连续数字
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(\d{4})(\d{8,11})(\d{4})(?!\d)")


def _mask_middle(m: re.Match[str]) -> str:
    """保留首尾，中间换成**等长**星号。

    为什么不用固定长度星号：卡号 16~19 位、订单号长度更长，
    固定星号会让掩码后的串长度与原文不符，下游核对长度时会误判。
    """
    return f"{m.group(1)}{'*' * len(m.group(2))}{m.group(3)}"


def mask_sensitive(text: str | None) -> str | None:
    """手机号/长数字（卡号、订单号）脱敏，其余内容原样保留。"""
    if not text:
        return text
    masked = _PHONE_RE.sub(_mask_middle, text)
    return _CARD_RE.sub(_mask_middle, masked)


def mask_deep(value: Any) -> Any:
    """递归脱敏 dict/list/str，用于 input_context / llm_output 落库前处理。"""
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {k: mask_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_deep(v) for v in value]
    return value


class Base(DeclarativeBase):
    """仅映射智能体自有表；业务表一律裸 SQL 只读查询（避免误写）。"""


class DecisionLog(Base):
    __tablename__ = "agent_decision_log"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    scene: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger_type: Mapped[int] = mapped_column(Integer, default=1)
    input_context: Mapped[Any | None] = mapped_column(_JSON)
    llm_output: Mapped[Any | None] = mapped_column(_JSON)
    action: Mapped[str | None] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0)
    create_by: Mapped[str | None] = mapped_column(String(64))
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64))
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    del_flag: Mapped[str] = mapped_column(CHAR(1), default="0")


class Message(Base):
    __tablename__ = "agent_message"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(Integer)
    msg_role: Mapped[int] = mapped_column(Integer)
    content: Mapped[str | None] = mapped_column(String(65535))
    tool_calls: Mapped[Any | None] = mapped_column(_JSON)
    model: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(64))
    create_by: Mapped[str | None] = mapped_column(String(64))
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64))
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    del_flag: Mapped[str] = mapped_column(CHAR(1), default="0")


# --- 结果码（与 agent_decision_log.result 注释一致）---
RESULT_PENDING, RESULT_OK, RESULT_FAIL, RESULT_REJECTED = 0, 1, 2, 3
TRIGGER_USER, TRIGGER_SCHEDULE, TRIGGER_MANUAL = 1, 2, 3


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """成本计量聚合结果。"""

    total_calls: int
    tokens_in: int
    tokens_out: int
    tokens_total: int


class QuotaExceededError(RuntimeError):
    """超出 token 限额——API 层应映射 429（AGENTS §7 成本失控对策）。"""


async def record_decision(
    session: AsyncSession,
    *,
    scene: int,
    action: str,
    request_id: str | None = None,
    user_id: int | None = None,
    trigger_type: int = TRIGGER_USER,
    input_context: Any = None,
    llm_output: Any = None,
    target_type: str | None = None,
    target_id: str | None = None,
    result: int = RESULT_PENDING,
    error_msg: str | None = None,
    confidence: float | None = None,
    cost_tokens: int = 0,
) -> None:
    """写一条决策留痕。

    **调用方不要让它抛异常**：留痕失败只记 WARN（审计旁路不得中断业务），
    因此这里捕获 SQLAlchemyError 后仅告警并返回。
    """
    now = datetime.now()
    row = DecisionLog(
        request_id=request_id,
        scene=scene,
        user_id=user_id,
        trigger_type=trigger_type,
        input_context=mask_deep(input_context),
        llm_output=mask_deep(llm_output),
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        error_msg=mask_sensitive(error_msg),
        confidence=confidence,
        cost_tokens=cost_tokens,
        create_by="agent" if user_id is None else str(user_id),
        create_time=now,
        update_time=now,
        del_flag="0",
    )
    session.add(row)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.warning(
            "决策留痕写入失败（业务不受影响，需事后补录）action=%s err=%s",
            action,
            type(exc).__name__,
            exc_info=True,
        )


async def record_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    seq: int,
    msg_role: int,
    user_id: int | None = None,
    content: str | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    request_id: str | None = None,
    tool_calls: Any = None,
) -> None:
    """写一条消息投影 + token 计量数据（agent_message 是成本报表的数据源）。"""
    now = datetime.now()
    session.add(
        Message(
            conversation_id=conversation_id,
            user_id=user_id,
            seq=seq,
            msg_role=msg_role,
            content=mask_sensitive(content),
            tool_calls=mask_deep(tool_calls),
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            request_id=request_id,
            create_by="agent",
            create_time=now,
            update_time=now,
            del_flag="0",
        )
    )
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.warning(
            "消息投影写入失败 conversation_id=%s err=%s",
            conversation_id,
            type(exc).__name__,
            exc_info=True,
        )


async def summarize_usage(
    session: AsyncSession,
    *,
    days: int = 1,
    scene: int | None = None,
    user_id: int | None = None,
) -> UsageSummary:
    """按日/场景/用户聚合 token 消耗（限额判断与报表共用同一口径）。"""
    since = datetime.now() - timedelta(days=max(days, 1))
    stmt = select(
        func.count(Message.id),
        func.coalesce(func.sum(Message.tokens_in), 0),
        func.coalesce(func.sum(Message.tokens_out), 0),
    ).where(Message.create_time >= since, Message.del_flag == "0")
    if scene is not None:
        stmt = stmt.where(Message.msg_role == scene)  # scene 由调用方按需扩展
    if user_id is not None:
        stmt = stmt.where(Message.user_id == user_id)
    count, tin, tout = (await session.execute(stmt)).one()
    return UsageSummary(
        total_calls=int(count or 0),
        tokens_in=int(tin or 0),
        tokens_out=int(tout or 0),
        tokens_total=int(tin or 0) + int(tout or 0),
    )


async def enforce_daily_quota(
    session: AsyncSession,
    *,
    user_id: int | None,
    per_user_limit: int,
    global_limit: int,
) -> None:
    """限额熔断：超限抛 QuotaExceededError。

    为什么在请求前判断：LLM 调用一旦发出就会产生费用，事后告警已经晚了。
    """
    today = date.today()
    if per_user_limit > 0 and user_id is not None:
        used = await summarize_usage(session, days=1, user_id=user_id)
        if used.tokens_total >= per_user_limit:
            raise QuotaExceededError(
                f"用户 {today} 当日 token 已达上限（{used.tokens_total}/{per_user_limit}）"
            )
    if global_limit > 0:
        used_all = await summarize_usage(session, days=1)
        if used_all.tokens_total >= global_limit:
            raise QuotaExceededError(
                f"全局 {today} 当日 token 已达上限（{used_all.tokens_total}/{global_limit}）"
            )
