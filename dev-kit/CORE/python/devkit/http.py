"""出站 HTTP 客户端封装：超时、幂等重试、request_id 传播、统一异常转换。

DEPS: httpx, ids, errors

三条关键约定：
1. **非幂等请求绝不重试** —— POST 重试一次可能产生两笔订单，后果比重试失败严重得多；
2. 出站必须带 `X-Request-Id`，否则下游日志串不起来（契约 log-fields.md §4）；
3. 所有网络失败转成 `DomainError.dependency`（retryable=True），调用方统一处理。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .errors import DomainError
from .ids import get_request_id

__all__ = ["HttpClient"]

logger = logging.getLogger(__name__)

# 哪些异常值得重试：连接层失败与超时。业务 4xx 不在此列（重试一万次还是错）。
_RETRYABLE_EXC = (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)


class HttpClient:
    """轻量封装，**不是**通用 HTTP 库替代品 —— 只做三件事：超时、幂等重试、异常归一。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_ms: int = 10_000,
        max_retries: int = 2,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_ms / 1000.0
        self.max_retries = max_retries
        self.default_headers = default_headers or {}
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                headers=self.default_headers,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        idempotent: bool = True,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """发起请求。失败抛 `DomainError.dependency`，不返回 None —— 避免调用方忘了判断。"""
        client = await self._ensure_client()
        merged_headers = {"X-Request-Id": get_request_id(), **(headers or {})}
        attempts = (self.max_retries + 1) if idempotent else 1

        last_exc: BaseException | None = None
        for attempt in range(attempts):
            try:
                response = await client.request(
                    method, path, json=json_body, params=params, headers=merged_headers
                )
                if response.status_code >= 500 and idempotent and attempt < attempts - 1:
                    # 下游 5xx 多为瞬时故障，幂等时可退避重试
                    await self._backoff(attempt)
                    continue
                return response
            except _RETRYABLE_EXC as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    logger.warning(
                        "下游调用失败，准备重试",
                        extra={"method": method, "path": path, "attempt": attempt + 1},
                    )
                    await self._backoff(attempt)
                    continue
                break
            except httpx.HTTPError as exc:
                # 非重试类 HTTP 错误（如协议错误）直接转业务异常
                last_exc = exc
                break

        raise DomainError.dependency(
            "DK0701",
            f"下游服务不可用：{self.base_url}{path}",
            data={"method": method, "path": path, "reason": str(last_exc)},
            cause=last_exc,
        )

    async def get_json(self, path: str, **kw: Any) -> Any:
        """GET 并解析 JSON。非 2xx 抛 `DomainError.dependency`。"""
        response = await self.request("GET", path, **kw)
        return self._parse(response, path)

    async def post_json(self, path: str, *, json_body: dict[str, Any], idempotent: bool = False) -> Any:
        """POST。**默认 idempotent=False**（见类注释第 1 条）—— 需要幂等请显式声明。"""
        response = await self.request("POST", path, json_body=json_body, idempotent=idempotent)
        return self._parse(response, path)

    def _parse(self, response: httpx.Response, path: str) -> Any:
        if response.status_code >= 400:
            raise DomainError.dependency(
                "DK0702",
                f"下游返回错误状态 {response.status_code}",
                data={"path": path, "body": response.text[:500]},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DomainError.dependency(
                "DK0703", f"下游返回非 JSON 内容：{path}", cause=exc
            ) from exc

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """指数退避 0.5s / 1s / 2s… 固定上限避免雪崩时把下游压垮。"""
        await asyncio.sleep(min(0.5 * (2**attempt), 4.0))
