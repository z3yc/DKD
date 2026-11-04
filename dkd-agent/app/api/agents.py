"""可用智能体/场景下发接口（排期 3-3，前端助手抽屉的场景列表数据源）。

## 为什么需要它

3-2 的自定义智能体（scene≥101）由配置台动态注册；前端抽屉的场景列表若继续硬编码，
新建的智能体**永远不会出现**在用户面前（管理员启用了也没用）。本接口把「当前可用的
chat 智能体清单」下发给前端，前端拿不到（DB 关闭/表未建/本接口失败）时回落硬编码三项——
灰度友好：Python 侧不可用时抽屉照常可用（AGENTS §1 约束 3 的降级精神）。

## 为什么是 `/agent/agents` 而不是 `/agent/status` 扩展

排期 3-3 原写「/agent/status 扩展」，但本次实现（task-9）明确走独立路由：
`/agent/**` 是**用户经 Java 网关**的通道，复用同一套 `require_agent_gateway` 校验，
路径独立后 Java 网关白名单与前端调用点都更清晰，也不必把“健康状态”和“业务清单”耦合进
同一个响应形状。

## 返回约定（RuoYi 信封）

`{"code": 200, "msg": "ok", "data": {"agents": [...]}}`；每个元素只含前端渲染场景列表
需要的字段：`code/name/scene/runtime/builtin/tools/version`。
**不返回 `system_prompt`**：前端不需要它，返回大字段只会白占带宽并扩大 prompt 内容的暴露面。

## 失败即空列表

DB 总开关关闭（`DKD_AGENT_CONFIG_FROM_DB=0`）/ 表不存在 / 查询异常一律返回空 `agents`，
**不返回 5xx**：场景下发是增强能力，失败时前端回落硬编码即可；把抽屉打成错误页
会让“配置读不到”升级成“对话入口不可用”。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.agent_defaults import CUSTOM_SCENE_BASE, RUNTIME_CHAT
from app.config_store import get_config_store
from app.security import require_agent_gateway

router = APIRouter(prefix="/agent", tags=["agents"], dependencies=[Depends(require_agent_gateway)])
logger = logging.getLogger("dkd.agent.api.agents")


def _is_dispatchable(snapshot: Any) -> bool:
    """该配置是否应该下发给助手抽屉。

    - 只下发 **chat 载体**（restock/fault 不是抽屉里的对话智能体）；
    - 内置 chat 行：`builtin=True`（一张配置服务内置场景 1-4）；
    - 自定义：`scene≥101`（5~100 是保留空洞，配置台不会分配，防御性排除）。
    """
    if getattr(snapshot, "runtime", "") != RUNTIME_CHAT:
        return False
    if getattr(snapshot, "builtin", False):
        return True
    return int(getattr(snapshot, "scene", 0) or 0) >= CUSTOM_SCENE_BASE


def _to_agent(snapshot: Any) -> dict[str, Any]:
    """配置快照 → 下发字段（**不含 system_prompt**，见模块 docstring）。"""
    return {
        "code": snapshot.code,
        "name": snapshot.name,
        "scene": int(snapshot.scene),
        "runtime": snapshot.runtime,
        "builtin": bool(snapshot.builtin),
        "tools": list(snapshot.tools or ()),
        "version": int(snapshot.version),
    }


@router.get("/agents", summary="可用对话智能体/场景列表（3-3 场景下发）")
async def list_agents() -> dict[str, Any]:
    """返回**已启用**的 chat 智能体清单；读不到配置时返回空列表（前端回落硬编码）。"""
    try:
        store = await get_config_store()
        enabled = await store.list_enabled()
    except Exception as exc:  # noqa: BLE001 —— 下发失败必须可回落，不得把抽屉打成 500
        # 只记异常类型：配置读取失败的具体信息里可能带主机/账号片段（AGENTS §6.1）
        logger.warning(
            "智能体清单读取失败，返回空列表（前端回落硬编码场景）：%s",
            type(exc).__name__,
        )
        enabled = []
    agents = [_to_agent(snapshot) for snapshot in enabled if _is_dispatchable(snapshot)]
    return {"code": 200, "msg": "ok", "data": {"agents": agents}}


__all__ = ["router"]
