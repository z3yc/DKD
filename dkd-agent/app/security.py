"""鉴权与身份解析（任务 0-9）。

两条鉴权链路的边界（方案 V1.1 §3.4，两套体系不得混用）：
1. 前端 → Java 网关 → 本服务：Python **不解析 JWT**，只信任网关注入的用户头
   （X-Agent-User / X-Agent-Roles / X-Agent-Region）。直接暴露时这些头可伪造，
   因此本服务只监听内网/回环地址，由 Java 网关对外。
2. Java 定时任务 / 前端触发写操作：使用服务间密钥（DKD_AGENT_SERVICE_SECRET）。
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings

# 网关注入的用户头（Java 侧 AgentTokenFilter 负责写入）
HEADER_USER = "X-Agent-User"
HEADER_USERNAME = "X-Agent-Username"
HEADER_ROLES = "X-Agent-Roles"
HEADER_REGION = "X-Agent-Region"
HEADER_SECRET = "X-Agent-Secret"
HEADER_REQUEST_ID = "X-Request-Id"


@dataclass(frozen=True, slots=True)
class UserContext:
    """当前请求身份，供工具层做权限对齐与审计留痕。"""

    user_id: int | None
    user_name: str | None
    roles: tuple[str, ...]
    region_id: int | None

    @property
    def is_system(self) -> bool:
        """定时任务/服务间调用（无用户上下文）。"""
        return self.user_id is None


def parse_user_context(request: Request) -> UserContext:
    """从网关透传的头解析身份。

    为什么不抛 401：定时任务触发的分析请求同样会走本服务，此时没有用户头。
    身份缺失由具体接口决定是否拒绝（写操作必须拒绝），而非在解析层一刀切。
    """
    headers = request.headers

    def _int(value: str | None) -> int | None:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    roles_raw = headers.get(HEADER_ROLES) or ""
    return UserContext(
        user_id=_int(headers.get(HEADER_USER)),
        user_name=headers.get(HEADER_USERNAME) or None,
        roles=tuple(r.strip() for r in roles_raw.split(",") if r.strip()),
        region_id=_int(headers.get(HEADER_REGION)),
    )


def require_login(context: UserContext) -> UserContext:
    """写操作/个性化查询的前置校验：必须有用户身份。"""
    if context.is_system:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少用户身份（网关注入头 X-Agent-User），拒绝执行",
        )
    return context


def verify_service_secret(
    x_agent_secret: str | None = Header(default=None, alias=HEADER_SECRET),
) -> None:
    """服务间密钥校验（Java 定时任务 / 内部调用）。

    用 hmac.compare_digest 做定时安全比较，避免按字符短路泄漏密钥长度信息。
    未配置密钥时一律拒绝——宁可不服务，也不要"默认放行"。
    """
    expected = get_settings().service_secret
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务间密钥未配置（DKD_AGENT_SERVICE_SECRET），拒绝服务",
        )
    if not x_agent_secret or not hmac.compare_digest(x_agent_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="服务间密钥校验失败")
