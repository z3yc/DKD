"""故障诊断接口（M3 / F-09b）。

调用链：前端 →（JWT）→ Java 网关 `/agent/fault/**`（白名单 + 权限点）→ 本模块。

## 鉴权分层

- 路由级 `require_agent_gateway`：只接受持有服务间密钥（或本机回环）的调用；
- 确认/驳回额外要求 `X-Agent-User`（网关注入）：**人工确认必须能追溯到人**，
  没有身份的直连调用一律 401（AGENTS §7.9 human-in-the-loop）。

## 返回约定

- 成功与业务结果都走 RuoYi 信封 `{code,msg,data}`；`data.status` 表达业务结局
  （confirmed/duplicate/rejected/in_progress/...），前端按它提示，而不是把业务结局当异常；
- 请求级问题用 HTTP 状态码：400 参数非法 / 401 缺身份 / 404 运行不存在 / 409 状态冲突。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.restock import require_user_id
from app.config import get_settings
from app.graphs.fault_confirm import ConfirmResult, confirm_fault_dispatch
from app.graphs.fault_graph import FaultDeps, run_fault_diagnosis
from app.security import require_agent_gateway

router = APIRouter(
    prefix="/agent/fault", tags=["fault"], dependencies=[Depends(require_agent_gateway)]
)

logger = logging.getLogger("dkd.agent.api.fault")

DEFAULT_WINDOW_HOURS = 24


class StartRunRequest(BaseModel):
    """发起诊断入参（camelCase 对外，内部 snake_case）。"""

    model_config = ConfigDict(populate_by_name=True)

    inner_code: str = Field(alias="innerCode", min_length=1, max_length=64)
    window_hours: int = Field(default=DEFAULT_WINDOW_HOURS, alias="windowHours", ge=1, le=87600)


class ConfirmRequest(BaseModel):
    """确认/驳回入参（驳回必须带原因）。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["confirm", "reject"] = "confirm"
    assignee_id: int | None = Field(default=None, alias="assigneeId", ge=1)
    priority: int | None = Field(default=None, ge=1, le=4)
    remark: str | None = Field(default=None, max_length=500)


def _deps(request: Request) -> FaultDeps:
    """取图依赖；测试可整体替换 `app.state.fault_deps`（不连库/不调模型）。"""
    deps = getattr(request.app.state, "fault_deps", None)
    if deps is not None:
        return deps
    return FaultDeps(settings=get_settings())


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 200, "msg": "ok", "data": data}


@router.post("/runs", summary="发起一次故障诊断（同步执行；返回 runId 与派单提案摘要）")
async def start_run(payload: StartRunRequest, request: Request) -> dict[str, Any]:
    """跑一次诊断（M2+M3 前半段）：取数 → 证据回溯 → 派单提案。

    <p>为什么同步执行而不是队列：单台设备诊断实测 1~2 秒（6 次只读工具），
    为它引入任务队列会让"谁在跑、跑到哪"多一套状态；等批量巡检（F-15）再考虑异步。
    """
    deps = _deps(request)
    try:
        state = await run_fault_diagnosis(
            deps, inner_code=payload.inner_code, window_hours=payload.window_hours
        )
    except Exception as exc:  # noqa: BLE001 —— 上游/DB 故障统一 502，不把堆栈透给前端
        logger.warning(
            "故障诊断执行失败 inner_code=%s err=%s",
            payload.inner_code,
            type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="故障诊断执行失败，请稍后重试") from exc

    proposal = state.get("dispatch_proposal") or {}
    if state.get("error"):
        status = "failed"
    elif proposal.get("dispatchable"):
        status = "waiting_confirm"
    else:
        status = "done"
    return _ok(
        {
            "runId": state.get("run_id"),
            "status": status,
            "innerCode": payload.inner_code,
            "evidenceCount": state.get("evidence_count") or 0,
            "droppedCount": state.get("dropped_count") or 0,
            "dispatchAllowed": bool(state.get("dispatch_allowed")),
            "proposal": proposal or None,
            "stopReason": state.get("stop_reason"),
        }
    )


@router.post("/runs/{run_id}/confirm", summary="确认派单提案建维修工单 / 驳回提案（幂等）")
async def confirm(
    run_id: str, payload: ConfirmRequest, request: Request
) -> dict[str, Any]:
    """人工确认（human-in-the-loop）：确认才经 Java 回调建单；重复确认返回同一工单。"""
    user_id = require_user_id(request)
    deps = _deps(request)
    result = await confirm_fault_dispatch(
        deps,
        run_id=run_id,
        action=payload.action,
        assignee_id=payload.assignee_id,
        priority=payload.priority,
        remark=payload.remark,
        operator=f"user:{user_id}",
        user_id=user_id,
    )
    return _ok(_mapped(result))


def _mapped(result: ConfirmResult) -> dict[str, Any]:
    """业务结局 → 信封；请求级问题抛 HTTPException（前端拦截器统一处理）。"""
    if result.status == "invalid":
        raise HTTPException(status_code=400, detail=result.message or "参数非法")
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=result.message or "运行不存在")
    if result.status in ("in_progress", "not_waiting"):
        raise HTTPException(status_code=409, detail=result.message or "当前状态不可操作")
    return result.to_dict()


__all__ = ["ConfirmRequest", "StartRunRequest", "router"]
