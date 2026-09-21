"""运维接口（任务 0-12：成本报表与限额可观测）。

鉴权：服务间密钥（`verify_service_secret`），**不允许用户 JWT 直接访问**——
成本数据属运营内部信息，与用户会话接口是两套体系（AGENTS §7.5）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.audit import summarize_usage
from app.db import get_sessionmaker
from app.security import verify_service_secret

router = APIRouter(prefix="/agents", tags=["ops"], dependencies=[Depends(verify_service_secret)])
logger = logging.getLogger("dkd.agent.ops")


@router.get("/usage/summary", summary="LLM token 用量聚合（成本计量报表数据源）")
async def usage_summary(
    days: int = Query(default=1, ge=1, le=90, description="统计窗口（天）"),
    user_id: int | None = Query(default=None, description="按用户过滤"),
) -> dict[str, object]:
    """返回窗口内的调用次数与 token 消耗。

    口径：agent_message 的 token 字段（成本计量的唯一数据源），
    模型名为**服务端实际返回**的值（实测 deepseek-chat 会路由到 deepseek-flash）。
    """
    async with get_sessionmaker()() as session:
        summary = await summarize_usage(session, days=days, user_id=user_id)
    return {
        "days": days,
        "user_id": user_id,
        "total_calls": summary.total_calls,
        "tokens_in": summary.tokens_in,
        "tokens_out": summary.tokens_out,
        "tokens_total": summary.tokens_total,
    }
