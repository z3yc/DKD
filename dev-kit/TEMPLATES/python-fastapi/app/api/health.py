"""健康检查路由：演示统一响应包装的写法。

DEPS: devkit, fastapi

新增路由时照此结构：只做参数校验 → 调 service → 用 `envelope()` 包装返回。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..errors import envelope

__all__ = ["router"]

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict[str, Any]:
    """存活探针。K8s / 网关 / 本地冒烟都用它。"""
    return envelope({"status": "ok"})
