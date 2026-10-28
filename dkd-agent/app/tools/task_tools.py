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


# 维修工单类型（与 Java `DkdContants.TASK_TYPE_REPAIR` 一致；Java 侧是权威枚举）
TASK_TYPE_REPAIR = 3


async def _post_task_payload(
    payload: dict[str, Any],
    *,
    inner_code: str,
    label: str,
    detail_count: int = 0,
    client: httpx.AsyncClient | None = None,
    request_id: str | None = None,
) -> CreatedTask:
    """把工单载荷提交给 Java（补货/维修共用同一条写通道与同一套失败分类）。

    @param payload 已组装好的 TaskDto（字段映射由调用方负责）
    @param label 日志里的动作名（"建单" / "报修建单"），只用于排障，不影响行为
    @param detail_count 明细条数（仅日志用）
    """
    settings = get_settings()
    secret = settings.service_secret
    if not secret:
        # 本地快速失败：Java 侧同样会回 503，但在这里拦掉能给出更明确的“配置缺失”信息
        raise CallbackAuthError("DKD_AGENT_SERVICE_SECRET 未配置，无法调用建单回调")

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
            "%s回调超时（%ss）inner_code=%s request_id=%s",
            label,
            settings.callback_timeout_s,
            inner_code,
            request_id,
        )
        # 为什么这条文案比“请勿盲目重试”长（FIX-16 演练产出，2026-10-01 实测）：
        #
        # 演练中真的停掉 Redis 后，回调在 Java 侧要等 Redis 连接超时，**超过了这里的 10s**，
        # 于是 Agent 先行放弃——Java 明明已经算出了精确原因（“服务器限流异常，请稍候再试”），
        # 却因为超时被整个丢弃。运营看到的是“超时、结果未知”，运维会去查网络/Java，
        # 而真因在 Redis（FIX-16 登记的就是“朝错误方向排查”）。
        #
        # 改不了 Java 侧的失败耗时（改它会动到 RuoYi 全局限流切面，影响所有接口），
        # 但可以让这句文案**自己带上下一步动作与可能原因**，把死胡同变成可行动。
        # 措辞上用“可能”而不是断言 Redis：Agent 看不到 Java 内部，不能替运维下结论。
        raise CallbackUnavailableError(
            f"{label}回调超时（>{settings.callback_timeout_s}s），未确认结果："
            "请先刷新状态再决定是否重试（若已变为「已建单」则勿重试）；"
            "回调经过 Redis 限流计数与数据库，可能原因包括 Java 或 Redis 不可用，建议一并检查"
        ) from exc
    except httpx.HTTPError as exc:
        # 只记异常类型：异常文本可能含 URL，而 URL 不含密钥（密钥只在头里），但仍避免贴原始报文
        logger.warning(
            "%s回调网络失败 inner_code=%s request_id=%s err=%s",
            label,
            inner_code,
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
        "%s回调成功 inner_code=%s task_id=%s task_code=%s details=%s request_id=%s",
        label,
        inner_code,
        created.task_id,
        created.task_code,
        detail_count,
        request_id,
    )
    return created


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
    payload = plan.to_task_dto()
    return await _post_task_payload(
        payload,
        inner_code=plan.inner_code,
        label="建单",
        detail_count=len(payload.get("details", [])),
        client=client,
        request_id=request_id,
    )


async def create_repair_task(
    *,
    inner_code: str,
    assignee_id: int,
    desc: str,
    client: httpx.AsyncClient | None = None,
    request_id: str | None = None,
) -> CreatedTask:
    """报修工单建单（故障场景 F-09 的唯一写通道，`productTypeId=3`）。

    <p>为什么维修单没有 details：Java `TaskDetailsMapper` 的明细是补货货道语义；
    维修单只带设备号/接单人/描述，`AgentCallbackController.validate()` 也只对补货强制校验明细。

    @param inner_code 设备编号
    @param assignee_id 接单人（`tb_emp.id`；Java 会校验与设备区域一致）
    @param desc 工单描述（来自派单提案，含证据依据）
    @raises CallbackAuthError / CallbackUnavailableError / CallbackBusinessError
    """
    payload = {
        "innerCode": inner_code,
        "userId": assignee_id,
        "productTypeId": TASK_TYPE_REPAIR,
        "desc": desc,
    }
    return await _post_task_payload(
        payload,
        inner_code=inner_code,
        label="报修建单",
        client=client,
        request_id=request_id,
    )
