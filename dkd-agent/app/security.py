"""鉴权与身份解析（任务 0-9）。

两条鉴权链路的边界（方案 V1.1 §3.4，两套体系不得混用）：
1. 前端 → Java 网关 → 本服务：Python **不解析 JWT**，只信任网关注入的用户头
   （X-Agent-User / X-Agent-Roles / X-Agent-Region）。直接暴露时这些头可伪造，
   因此本服务只监听内网/回环地址，由 Java 网关对外。
2. Java 定时任务 / 前端触发写操作：使用服务间密钥（DKD_AGENT_SERVICE_SECRET）。

## /agent/** 的服务间密钥校验（P0 安全修复，2026-10-21）

审计发现：本服务若以 0.0.0.0 监听（容器部署就这样），同一 docker 网络内任何容器
只要带一个 `X-Agent-User: 任意值` 头就能直连 `/agent/**` 冒充任意用户（确认补货单、
暂停全局自动分析）。网关注入头的前提是“Python 只被网关访问”，而网络隔离不是代码能保证的。

修复策略（fail-closed，见 `require_agent_gateway`）：
- **回环来源（127.0.0.1/::1）放行**：本机部署时 Java 网关与 Python 同机，这是既有可用路径；
  测试客户端也显式以回环身份发起（见 tests/conftest.py），不在测试里开“非回环放行”的后门；
- **非回环来源必须携带正确的 `X-Agent-Secret`**（与 Java `agent.secret` 一致，常量时间比较）；
- **密钥未配置时非回环一律 503**：宁可不服务，也不要“没配密钥默认放行”。

注意：这里校验的是**调用方身份**（服务间密钥），不改变 `X-Agent-*` 用户头的解析方式——
用户身份仍由 Java 网关解析并重建头，Python 不解析 JWT（两套体系不混用，AGENTS §7.5）。
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger("dkd.agent.security")

# 网关注入的用户头（Java 侧 AgentTokenFilter 负责写入）
HEADER_USER = "X-Agent-User"
HEADER_USERNAME = "X-Agent-Username"
HEADER_ROLES = "X-Agent-Roles"
HEADER_REGION = "X-Agent-Region"
HEADER_SECRET = "X-Agent-Secret"
HEADER_REQUEST_ID = "X-Request-Id"

# 视为“本机来源”的回环地址（IPv4/IPv6/映射地址在 _is_loopback 里归一化）
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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


def _is_loopback(host: str | None) -> bool:
    """判断来源地址是否为回环（127.0.0.0/8、::1、::ffff:127.x 映射地址）。

    为什么把整个 127.0.0.0/8 都算回环：本机访问时客户端地址可能是 127.0.0.2 等，
    但全部不会离开本机协议栈；只认 127.0.0.1 会让“本机直连”在某类网络配置下误判成外部来源。
    为什么 `::ffff:127.0.0.1` 要归一化：Windows/双栈环境下 IPv4 回环会以 IPv6 映射形式出现
    （实测 uvicorn 在双栈监听时会给出这种 client host）。
    """
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized.startswith("::ffff:"):
        normalized = normalized[len("::ffff:") :]
    return normalized in _LOOPBACK_HOSTS or normalized.startswith("127.")


def _client_host(request: Request) -> str | None:
    """取请求来源地址；ASGI 未提供 client（如 in-process 调用）时返回 None。"""
    client = request.client
    return client.host if client is not None else None


def require_agent_gateway(
    request: Request,
    x_agent_secret: str | None = Header(default=None, alias=HEADER_SECRET),
) -> None:
    """/agent/** 的调用方校验：回环放行，非回环必须带正确服务间密钥（fail-closed）。

    为什么挂在 `/agent/**` 而 `/agents/**` 仍用 `verify_service_secret`：
      - `/agent/**` 是**用户经 Java 网关**的通道，身份主要靠 `X-Agent-User` 头，而该头可伪造，
        因此必须额外证明“调用方是本机网关，或持有服务间密钥”；
      - `/agents/**` 本来就是纯服务间接口，没有任何回环豁免，继续用严格版校验。
    两者共用同一个密钥与常量时间比较，不新增第二套凭据。

    为什么密钥未配置时对非回环返回 503 而不是 401：这是**服务端配置缺失**，不是调用方凭据错误。
    401 会让运维去查“密钥为什么不一致”，503 的 msg 直接指出去配 DKD_AGENT_SERVICE_SECRET；
    无论哪种都不会放行（fail-closed）。

    为什么回环不做密钥校验：本机部署（Java 网关与 Python 同机、网关默认 base-url 指向 127.0.0.1）
    是既有可用路径，强制密钥会让未配置密钥的本地开发/单机部署整体不可用；而回环来源在操作系统层面
    已经排除了“同网络其他容器直连”这一审计发现的攻击面。容器/跨机部署必须配密钥（否则非回环 503），
    这一约束与 Java 侧 `agent.secret` 的配置要求一一对应。
    """
    host = _client_host(request)
    if _is_loopback(host):
        return

    expected = get_settings().service_secret
    if not expected:
        logger.warning(
            "拒绝非回环 /agent 调用：服务间密钥未配置 client=%s path=%s", host, request.url.path
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务间密钥未配置（DKD_AGENT_SERVICE_SECRET），非回环调用拒绝服务",
        )
    if not x_agent_secret or not hmac.compare_digest(
        x_agent_secret.encode("utf-8"), expected.encode("utf-8")
    ):
        # 只记来源与路径，不记密钥值（AGENTS §6.1）
        logger.warning(
            "拒绝非回环 /agent 调用：服务间密钥缺失或不匹配 client=%s path=%s",
            host,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="服务间密钥校验失败（/agent/** 仅允许回环或持密钥的服务间调用）",
        )

