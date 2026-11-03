"""运维接口（任务 0-12 成本报表；任务 1-9 定时分析触发）。

鉴权：整路由挂服务间密钥（`verify_service_secret`），**不允许用户 JWT 直接访问**——
成本数据与“触发分析”都是运营内部动作，与用户会话接口是两套体系（AGENTS §7.5）。

## 路径分组为什么是 `/agents/**` 而不是 `/agent/restock/**`

`/agent/restock/**`（1-7）是**用户经 Java 网关**调用的工作台接口，身份来自 `X-Agent-User`；
`/agents/**` 是**服务间**接口，身份来自 `X-Agent-Secret`。把两类鉴权体系放在不同前缀下，
边界在 URL 上就可见——这正是 1-9 的任务书要求 `/agents/restock/analyze` 而不是
`/agent/restock/analyze` 的原因（虽然只差一个 s，但鉴权方式完全不同）。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.audit import summarize_usage
from app.config_store import get_config_store
from app.db import get_sessionmaker
from app.security import verify_service_secret
from app.services.decision_audit import resolve_request_id
from app.services.restock_analyzer import RestockAnalyzeError, RestockAnalyzer

router = APIRouter(prefix="/agents", tags=["ops"], dependencies=[Depends(verify_service_secret)])
logger = logging.getLogger("dkd.agent.ops")


class AnalyzeRequest(BaseModel):
    """定时分析触发入参。

    `planDate` 显式传入（Java 定时任务把日期算好后传过来），而不是让两端各自 `today()`：
    跨时区/跨机部署时两边日期不同会造成「今天没有计划」这类无从查起的现象。
    缺省则用 Python 侧当天（本地脚本/测试方便）。
    """

    plan_date: str | None = Field(default=None, alias="planDate", description="YYYY-MM-DD")

    model_config = {"populate_by_name": True}


def get_analyzer(request: Request) -> RestockAnalyzer:
    """取分析执行器；测试可用 `app.state.restock_analyzer = FakeAnalyzer()` 整体替换。"""
    analyzer = getattr(request.app.state, "restock_analyzer", None)
    if analyzer is None:
        raise HTTPException(status_code=503, detail="补货分析执行器未初始化")
    return analyzer


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


@router.post("/restock/analyze", summary="触发补货分析（06:00 定时任务入口，任务 1-9）")
async def restock_analyze(
    request: Request, payload: AnalyzeRequest | None = None
) -> dict[str, object]:
    """跑一轮补货分析，在人工确认处中断返回。

    返回 `data.status`：
      - `analyzed` —— 已生成计划并落库（`deviceCount` / `channelCount` / `totalQuantity`）；
      - `skipped`  —— 自动分析被 `agent_restock_pause` 暂停（**不是错误**，不触发 Java 侧重试）。

    失败时映射 502：这是 Python 自身故障（取数/落库/编排），Java 侧应重试并在最终失败后告警。
    """
    analyzer = get_analyzer(request)
    target = _parse_date(payload.plan_date) if payload and payload.plan_date else None
    try:
        outcome = await analyzer.analyze(plan_date=target, request_id=resolve_request_id())
    except RestockAnalyzeError as exc:
        logger.error("定时分析失败：%s", exc)
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"code": 200, "msg": "ok", "data": outcome.model_dump(mode="json", by_alias=True)}


# ------------------------------------------------------------------------------------
# 智能体配置缓存（排期 2-3，契约 §5.1）
# ------------------------------------------------------------------------------------
@router.post("/config/reload", summary="清除智能体配置缓存（Java 保存后通知，排期 2-3）")
async def reload_config() -> dict[str, object]:
    """清空配置缓存，下一次读取回源 MySQL。

    为什么做成显式接口而不是只靠 TTL：Java 保存后希望**立即**生效，TTL 30s 只是
    reload 丢失/失败时的兜底（契约 §4.7）。本接口不返回配置内容，避免把排障接口
    变成第二份配置视图。Java 侧调用失败只 WARN，不阻塞保存。
    """
    store = await get_config_store()
    store.invalidate()
    logger.info("智能体配置缓存已清空（Java 保存后通知）")
    return {"code": 200, "msg": "ok", "data": {"invalidated": True}}


@router.get("/config/effective", summary="查看某智能体当前生效配置（只读排障，排期 2-3）")
async def effective_config(
    code: str = Query(..., min_length=1, max_length=64, description="智能体编码，如 restock"),
) -> dict[str, object]:
    """返回「当前实际生效」的配置快照，用于排障（不含任何密钥/连接信息）。

    `source=db` 表示读到了启用配置；`source=default` 表示三重回落生效
    （总开关关闭 / 表缺失 / 数据损坏），此时 `config=None`，调用方实际走代码默认值。
    这里**不**替调用方补默认值：补了就看不出“真实来源”，排障会误以为配置已生效。
    """
    snapshot = await (await get_config_store()).get(code)
    if snapshot is None:
        return {"code": 200, "msg": "ok", "data": {"source": "default", "config": None}}
    return {"code": 200, "msg": "ok", "data": {"source": "db", "config": asdict(snapshot)}}


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式必须是 YYYY-MM-DD：{text}") from exc
