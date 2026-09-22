"""补货工作台接口（Phase 1 / 任务 1-7）。

调用链：前端 →（JWT）→ Java 网关 `/agent/**` 注入身份头 → 本模块 → RestockService。

## 路径与鉴权的取舍

- 读接口（清单）允许无用户身份，写接口（干预/开关）**必须**有 `X-Agent-User`：
  网关只对已认证用户注入该头并会剥掉客户端自带的同名头（0-6 实测），所以“有头 = 已认证”。
  本地直连 Python（跳过网关）时没有头 → 写操作直接被拒，等于给红线加了一道锁。
- 全部返回 RuoYi 风格信封 `{code,msg,data}`：与 Java 侧一致，前端一套拦截器处理到底。
  错误路径由 `app.main` 的 HTTPException 处理器统一改造，因此这里直接 `raise HTTPException`。
- **不做 plan_date 之外的批量操作**：原型有「全部确认建单」，但那是前端逐条调本接口的组合，
  不在后端造一个“批量”端点——批量端点会把“一台失败是否整批回滚”变成没人说得清的问题。
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.security import parse_user_context
from app.services.restock_service import (
    AdjustmentRequest,
    RestockService,
    RestockServiceError,
)

router = APIRouter(prefix="/agent/restock", tags=["restock"])
logger = logging.getLogger("dkd.agent.api.restock")


class SkipRequest(BaseModel):
    """跳过/恢复入参（原因必填，原型 V2 的强制项）。"""

    reason: str = Field(min_length=1, max_length=500)


class PauseRequest(BaseModel):
    """暂停/恢复自动分析入参。"""

    reason: str = Field(min_length=1, max_length=500)


def get_service(request: Request) -> RestockService:
    """取服务实例。

    测试与本地脚本可用 `app.state.restock_service = FakeService()` 整体替换；
    生产由 `app.main` 的 lifespan 装配（真实 store + 真实只读依赖）。
    """
    service = getattr(request.app.state, "restock_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="补货服务未初始化")
    return service


def require_user_id(request: Request) -> int:
    """写操作必须带网关注入的用户身份（AGENTS §7.5：鉴权不裸奔）。"""
    ctx = parse_user_context(request)
    if ctx.user_id is None:
        raise HTTPException(
            status_code=401,
            detail="缺少用户身份（X-Agent-User）；补货干预操作必须经 Java 网关携带用户 JWT 调用",
        )
    return int(ctx.user_id)


def _today() -> date:
    return date.today()


@router.get("/plans", summary="补货计划清单（工作台数据源）")
async def list_plans(request: Request, plan_date: str | None = None) -> dict[str, object]:
    """按日期取清单；缺省为今天（06:00 定时任务写入的那份）。"""
    service = get_service(request)
    target = _parse_date(plan_date) if plan_date else _today()
    return await _guard(service.list_plans(target))


@router.post("/plans/{plan_id}/confirm", summary="确认建单（幂等：已建单再点会返回“已建单”）")
async def confirm(
    plan_id: int, request: Request, plan_date: str | None = None
) -> dict[str, object]:
    service = get_service(request)
    user_id = require_user_id(request)
    target = _parse_date(plan_date) if plan_date else _today()
    return await _guard(service.confirm(plan_id, plan_date=target, user_id=user_id))


@router.post(
    "/plans/{plan_id}/adjust",
    summary="调整建议（数量 / 预测窗口 / 服务水平，原因必填）",
)
async def adjust(
    plan_id: int, payload: AdjustmentRequest, request: Request, plan_date: str | None = None
) -> dict[str, object]:
    service = get_service(request)
    user_id = require_user_id(request)
    target = _parse_date(plan_date) if plan_date else _today()
    return await _guard(service.adjust(plan_id, plan_date=target, user_id=user_id, payload=payload))


@router.post("/plans/{plan_id}/skip", summary="跳过建议（原因必填，终态）")
async def skip(
    plan_id: int, payload: SkipRequest, request: Request, plan_date: str | None = None
) -> dict[str, object]:
    service = get_service(request)
    user_id = require_user_id(request)
    target = _parse_date(plan_date) if plan_date else _today()
    return await _guard(
        service.skip(plan_id, plan_date=target, user_id=user_id, reason=payload.reason)
    )


@router.post("/plans/{plan_id}/restore", summary="恢复建议（仅限当天、原因必填）")
async def restore(
    plan_id: int, payload: SkipRequest, request: Request, plan_date: str | None = None
) -> dict[str, object]:
    service = get_service(request)
    user_id = require_user_id(request)
    target = _parse_date(plan_date) if plan_date else _today()
    return await _guard(
        service.restore(
            plan_id, plan_date=target, user_id=user_id, reason=payload.reason, today=_today()
        )
    )


@router.get("/pause", summary="查询自动分析开关状态（原型 V2 的暂停横幅）")
async def get_pause(request: Request) -> dict[str, object]:
    service = get_service(request)
    state = await service.pause_state()
    return {"code": 200, "msg": "ok", "data": state.model_dump(mode="json")}


@router.post("/pause", summary="暂停自动分析（原因必填；次日 06:00 不再生成建议）")
async def pause(payload: PauseRequest, request: Request) -> dict[str, object]:
    service = get_service(request)
    user_id = require_user_id(request)
    return await _guard(service.set_pause(paused=True, user_id=user_id, reason=payload.reason))


@router.post("/resume", summary="恢复自动分析（原因必填）")
async def resume(payload: PauseRequest, request: Request) -> dict[str, object]:
    service = get_service(request)
    user_id = require_user_id(request)
    return await _guard(service.set_pause(paused=False, user_id=user_id, reason=payload.reason))


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式必须是 YYYY-MM-DD：{text}") from exc


async def _guard(coro: object) -> dict[str, object]:
    """把服务层的业务异常映射成信封 + 合适的状态码（409=状态机拒绝，502=建单失败）。"""
    try:
        data = await coro  # type: ignore[misc]
    except RestockServiceError as exc:
        logger.warning("补货接口业务拒绝：%s（status=%s）", exc, exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    return {"code": 200, "msg": "ok", "data": data}


def build_default_service() -> RestockService:
    """生产装配（lifespan 调用）：真实 store + 真实只读依赖。"""
    from app.graphs.restock_graph import SqlRestockDeps
    from app.graphs.restock_plan_store import SqlPlanStore

    settings = get_settings()
    return RestockService(
        store=SqlPlanStore(), deps=SqlRestockDeps(settings=settings), settings=settings
    )
