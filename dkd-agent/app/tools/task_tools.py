"""工单创建回调封装（Phase 1 / 任务 1-3）。

**写操作的唯一通道**：`POST {DKD_AGENT_JAVA_BASE_URL}/agent/callback/task`
（Java 侧 `AgentCallbackController`，服务间密钥 `X-Agent-Secret` 鉴权）。
Python 端**永远不直连写业务表**（AGENTS §1 约束 1 / §7.3）——这是本项目的第一红线。

为什么不在工具里做重试（no retry）：
    建单是**非幂等**动作。回调超时/网络抖动时重试可能造成重复建单；Java 侧虽有“同设备未完成工单”
    防重，但那会返回业务异常（用户看到的是失败，实际却建了单）。因此这里：**失败就如实上报**，
    由上层（排期 1-6/1-8 的计划状态机 + 幂等键）决定是否重试，而不是在传输层偷偷重试。

失败分类（决定上层怎么反应，不是一回事）：
    - `CallbackAuthError`：密钥错/未配置 → 运维问题，重试无意义；
    - `CallbackUnavailableError`：Java 不可达/超时/5xx → 可稍后重试；
    - `CallbackBusinessError`：业务校验链拒绝（设备有未完成工单 / 员工区域不一致 / 售货机不存在）
      → **message 原样来自 Java**，要展示给运营人员，绝不改写、绝不吞掉。

契约来源：`docs/DKD智能体接入方案-LangChain-LangGraph.md` §3.3.1（0-6/0-7 实测口径）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.graphs.restock_state import RestockPlan

logger = logging.getLogger("dkd.agent.tools.task")

# 回调路径（Java AgentCallbackController 的映射；白名单内唯一允许的写端点）
CALLBACK_TASK_PATH = "/agent/callback/task"
# 服务间密钥请求头（与 Java AgentHeaders.SECRET 一致）
HEADER_SECRET = "X-Agent-Secret"
HEADER_REQUEST_ID = "X-Request-Id"


class CallbackError(RuntimeError):
    """回调失败的基类。"""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class CallbackAuthError(CallbackError):
    """密钥未配置 / 校验失败（401/503）——配置或运维问题，重试无意义。"""


class CallbackUnavailableError(CallbackError):
    """Java 不可达、超时或自身 5xx——可稍后重试。"""


class CallbackBusinessError(CallbackError):
    """业务校验链拒绝：message 为 Java 侧原文，需原样展示。"""


class CreatedTask(BaseModel):
    """回调成功后的工单标识（回写 `agent_restock_plan.task_id/task_code` 用）。"""

    task_id: int = Field(description="tb_task.task_id")
    task_code: str | None = Field(default=None, description="tb_task.task_code")


def _callback_url() -> str:
    return get_settings().java_base_url.rstrip("/") + CALLBACK_TASK_PATH


def _build_httpx_client(*, timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), trust_env=False)


def _raise_for_envelope(response: httpx.Response) -> CreatedTask:
    """解析 RuoYi 信封。**必须同时看 HTTP 状态码与信封 code**。

    为什么要看两层：RuoYi 的业务失败是 `HTTP 200 + code=500`（全局异常处理器），
    只看 HTTP 状态码会把“业务拒绝”当成成功——这正是 0-11 在前端踩过的坑（坑 14）。
    """
    if response.status_code in (401, 403):
        raise CallbackAuthError(
            _safe_msg(response) or "服务间密钥校验失败", code=response.status_code
        )
    if response.status_code == 503:
        raise CallbackUnavailableError(
            _safe_msg(response) or "Java 侧回调未就绪（密钥未配置或智能体未启用）",
            code=response.status_code,
        )
    if response.status_code >= 500:
        raise CallbackUnavailableError(
            f"Java 侧返回 {response.status_code}", code=response.status_code
        )
    if response.status_code != 200:
        raise CallbackUnavailableError(
            f"回调返回异常状态码 {response.status_code}", code=response.status_code
        )

    payload = _safe_json(response)
    code = payload.get("code")
    message = str(payload.get("msg") or "")
    if code != 200:
        # 业务拒绝：message 原样保留（设备有未完成工单 / 员工区域不一致 / 售货机不存在）
        raise CallbackBusinessError(message or f"回调业务失败（code={code}）", code=code)
    data = payload.get("data") or {}
    task_id = data.get("taskId")
    if task_id is None:
        # 1-3 之前的老版本回调不回传 taskId；显式报错避免上层静默拿到 None 却以为成功
        raise CallbackUnavailableError("回调成功但未返回 taskId（Java 侧版本过旧？）")
    return CreatedTask(task_id=int(task_id), task_code=data.get("taskCode"))


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_msg(response: httpx.Response) -> str:
    return str(_safe_json(response).get("msg") or "")


async def create_restock_task(
    plan: RestockPlan, *, client: httpx.AsyncClient | None = None, request_id: str | None = None
) -> CreatedTask:
    """把补货计划提交给 Java 建单（唯一写通道）。

    @param plan 已通过状态机卡口、且已有接单人的补货计划（内部用 `to_task_dto()` 做字段映射）
    @param client 可选注入的 httpx 客户端（单测用 MockTransport，生产传 None）
    @param request_id 全链路追踪 ID（与网关/日志一致，便于跨进程对账）
    @return 建单结果（taskId/taskCode）
    @raises CallbackAuthError / CallbackUnavailableError / CallbackBusinessError 见模块 docstring
    """
    settings = get_settings()
    secret = settings.service_secret
    if not secret:
        # 本地快速失败：Java 侧同样会回 503，但在这里拦掉能给出更明确的“配置缺失”信息
        raise CallbackAuthError("DKD_AGENT_SERVICE_SECRET 未配置，无法调用建单回调")

    payload = plan.to_task_dto()
    url = _callback_url()
    headers = {
        HEADER_SECRET: secret,
        "Content-Type": "application/json",
    }
    if request_id:
        headers[HEADER_REQUEST_ID] = request_id

    owns_client = client is None
    http = client or _build_httpx_client(timeout_s=settings.callback_timeout_s)
    try:
        response = await http.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning(
            "建单回调超时（%ss）inner_code=%s request_id=%s",
            settings.callback_timeout_s,
            plan.inner_code,
            request_id,
        )
        raise CallbackUnavailableError(
            f"建单回调超时（>{settings.callback_timeout_s}s），结果未知，请勿盲目重试"
        ) from exc
    except httpx.HTTPError as exc:
        # 只记异常类型：异常文本可能含 URL，而 URL 不含密钥（密钥只在头里），但仍避免贴原始报文
        logger.warning(
            "建单回调网络失败 inner_code=%s request_id=%s err=%s",
            plan.inner_code,
            request_id,
            type(exc).__name__,
        )
        raise CallbackUnavailableError("Java 服务不可达，建单未完成") from exc
    finally:
        if owns_client:
            await http.aclose()

    created = _raise_for_envelope(response)
    # 只记结果与标识，不记密钥、不记完整请求体（AGENTS §6.1 日志规范）
    logger.info(
        "建单回调成功 inner_code=%s task_id=%s task_code=%s channels=%s request_id=%s",
        plan.inner_code,
        created.task_id,
        created.task_code,
        len(payload.get("details", [])),
        request_id,
    )
    return created
