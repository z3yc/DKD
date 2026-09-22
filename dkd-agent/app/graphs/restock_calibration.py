"""补货 LLM 校准节点（Phase 1 / 任务 1-5）。

定位：在 1-4 的**统计基线**之上做“人情味”的一层——点位画像、节假日前后的客流变化、
异常波动（促销/天气/周边活动），以及给运营看的**理由文本**（原型 V2 的「依据」字段）。

## 这一层为什么只输出“系数”而不输出“数量”

统计基线已经给出了有数据支撑的建议量；LLM 的价值在于**方向性判断**（该多补还是少补），
而不在于重新做算术。让它直接给数量，等于把“算错”与“瞎编”混在一起无法归因，
且一旦它给出 999，下游只有两种选择：要么信任（错补），要么再夹取（那不如只让它给系数）。

## 三层不可信输入防护（AGENTS §7.6，缺任何一层都不够）

1. **提示词层**：数据放在 `<data>` 围栏内并声明“是数据不是指令”，输出只用系数；
2. **解析层**：容错解析（去代码块、括号配对扫描取 JSON、pydantic 校验），
   未知货道/非法数字/越界系数**逐项丢弃并记录**，而不是整体报错或静默采用；
3. **夹取层**：系数夹取到 `[min_factor, max_factor]`，建议量再按 `[0, 容量-现库存]` 夹一次。
   → 于是“LLM 说把 1-1 补 999 件”在最坏情况下只会让建议量在本区间内变动 ±50%/±30%。

## 失败即降级，不抛穿（AGENTS §6.3）

LLM 超时/报错/输出不是 JSON：**保留统计基线结果**，把失败原因写进 `issues`
并计入 `calibration_notes`（供审计与 3-6 复盘）。补货链路的最坏形态是
“可解释的规则增强版”，绝不能因为模型抖一下就整天不出建议。

## 不改 RestockItem 字段（0-13 冻结约束）

机器可读的校准明细放在 `calibration_notes`（state 里已有该字段），
人可读的依据追加进 `RestockItem.reason`。**新增 RestockItem 字段需要走 0-13 的兼容性评审**
（LangGraph 会把最新代码作用于历史 checkpoint），本节点不越权改冻结 schema。
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.graphs.restock_baseline import BaselineSuggestion
from app.graphs.restock_state import RestockItem, RestockPlan
from app.llm import build_chat_model, extract_served_model, extract_usage
from app.prompts.restock_calibration import build_user_prompt, system_prompt

logger = logging.getLogger("dkd.agent.graphs.restock_calibration")

# 一次校准最多能追加进 reason 的校准说明长度（reason 契约上限 500，留出余量）
CALIBRATION_REASON_BUDGET = 160


class CalibrationEntry(BaseModel):
    """单条校准项（LLM 输出的原子单元）。"""

    channel_code: str
    factor: float
    reason: str = Field(default="", max_length=120)
    clamped: bool = Field(default=False, description="系数是否被夹取过")


class CalibrationOutcome(BaseModel):
    """一个设备的校准结果（含审计所需的一切：应用了什么、丢弃了什么、花了多少 token）。"""

    inner_code: str
    applied: bool = Field(description="是否真的应用了 LLM 校准（False = 降级为基线）")
    entries: list[CalibrationEntry] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list, description="被丢弃/异常项（审计与复盘用）")
    notes: str = Field(default="", description="LLM 的整体判断（≤60 字）")
    items: list[RestockItem] = Field(default_factory=list, description="校准后的建议明细")
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None
    error: str | None = None


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象（容错，不信任格式）。

    为什么不用 `json.loads(text)`：实测模型会夹带 ```json 围栏、前置寒暄、
    甚至输出两个对象。也不直接用 `find("{")`/`rfind("}")`：那在“外层对象里含字符串花括号”
    时会切错位置。这里用括号配对扫描（跳过字符串内的括号与转义），失败就返回 None。
    """
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError:
                        break
                    return parsed if isinstance(parsed, dict) else None
        start = text.find("{", start + 1)
    return None


def _coerce_factor(raw: Any, *, issues: list[str], channel: str) -> float | None:
    """把不可信输入转成合法系数：非数字/NaN/无穷 → 丢弃；越界 → 夹取并记录。"""
    if isinstance(raw, bool) or raw is None:
        issues.append(f"{channel}: factor 缺失或类型非法（{raw!r}）")
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        issues.append(f"{channel}: factor 不是数字（{raw!r}）")
        return None
    if math.isnan(value) or math.isinf(value):
        issues.append(f"{channel}: factor 是 NaN/Inf")
        return None
    return value


def parse_calibrations(
    text: str,
    *,
    known_channels: Sequence[str],
    settings: Settings | None = None,
) -> tuple[list[CalibrationEntry], list[str], str]:
    """容错解析 LLM 输出 → (校准项, 问题列表, 整体 notes)。

    丢弃规则（每条都要记进 issues，否则“模型少给了一条”没人能发现）：
      - 未知 `channelCode`（模型凭空编的货道）→ 丢弃；
      - 重复货道 → 保留第一条（确定性优先，避免同一通道被叠加两次）；
      - `factor` 缺失/非数字/NaN → 丢弃；越界 → 夹取。
    """
    settings = settings or get_settings()
    issues: list[str] = []
    payload = extract_json_object(text)
    if payload is None:
        return [], ["LLM 输出中未找到合法 JSON 对象"], ""

    raw_items = payload.get("calibrations")
    if not isinstance(raw_items, list):
        return [], ["calibrations 缺失或不是数组"], str(payload.get("notes") or "")[:120]

    known = set(known_channels)
    entries: list[CalibrationEntry] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            issues.append(f"校准项不是对象（{type(raw).__name__}），已丢弃")
            continue
        channel = str(raw.get("channelCode") or raw.get("channel_code") or "").strip()
        if not channel:
            issues.append("校准项缺少 channelCode，已丢弃")
            continue
        if channel not in known:
            issues.append(f"{channel}: 不在本次建议清单内（疑似模型编造），已丢弃")
            continue
        if channel in seen:
            issues.append(f"{channel}: 重复校准项，只取第一条")
            continue
        value = _coerce_factor(raw.get("factor"), issues=issues, channel=channel)
        if value is None:
            continue
        low, high = settings.restock_calibration_min_factor, settings.restock_calibration_max_factor
        clamped_value = min(max(value, low), high)
        seen.add(channel)
        entries.append(
            CalibrationEntry(
                channel_code=channel,
                factor=round(clamped_value, 4),
                reason=str(raw.get("reason") or "")[:120],
                clamped=clamped_value != value,
            )
        )
        if clamped_value != value:
            issues.append(f"{channel}: factor {value:g} 越界，已夹取到 {clamped_value:g}")
    return entries, issues, str(payload.get("notes") or "")[:120]


def apply_factor(item: RestockItem, entry: CalibrationEntry) -> RestockItem:
    """按系数调整建议量并重算派生字段（纯函数）。

    两条不可绕过的约束：
    1. 调整后的量仍须落在 `[0, 容量-现库存]`（容量夹取不能被 LLM 覆盖，否则 dkd-app 会溢出库存）；
    2. 基线原本建议 >0 而系数后变 0 的情况**不会发生**（min_factor ≥ 0.7 且至少保留 1 件），
       避免“模型一句话把必需的补货抹掉”。
    """
    room = item.max_capacity - item.current_quantity
    target = round(item.suggested_quantity * entry.factor)
    if item.suggested_quantity > 0:
        target = max(1, target)
    target = min(max(target, 0), room)

    reason = item.reason
    if entry.factor != 1.0:
        tag = f"；LLM 校准 ×{entry.factor:g}"
        if entry.reason:
            tag += f"：{entry.reason}"
        if entry.clamped:
            tag += "（系数已夹取）"
        # 校准说明预算是硬约束：reason 字段有 500 字上限（RestockItem 契约），
        # 截断时优先保住基线依据（运营先看数据依据，再看模型观点）
        reason = (reason[: 500 - CALIBRATION_REASON_BUDGET] + tag[:CALIBRATION_REASON_BUDGET])[:500]

    return item.model_copy(
        update={
            "suggested_quantity": target,
            "after_restock_quantity": item.current_quantity + target,
            "reason": reason,
        }
    )


def build_payload(
    plan: RestockPlan, *, suggestions: dict[str, BaselineSuggestion]
) -> dict[str, Any]:
    """构造给 LLM 的数据包。

    只送**决策必需**的字段：货道号、商品名、统计口径（日均需求/置信度/撑至日期）、当前量与建议量。
    不送手机号、不送金额、不送设备位置精确坐标（AGENTS §7.8 脱敏 + §7.6 数据最小化）。
    """
    items = []
    for item in plan.items:
        baseline = suggestions.get(item.channel_code)
        items.append(
            {
                "channelCode": item.channel_code,
                "skuName": item.sku_name,
                "currentQuantity": item.current_quantity,
                "maxCapacity": item.max_capacity,
                "baselineSuggested": item.suggested_quantity,
                "dailyDemand": baseline.daily_demand if baseline else None,
                "confidence": baseline.confidence if baseline else None,
                "estimatedDays": item.estimated_days,
                "priority": item.priority,
            }
        )
    return {
        "planDate": plan.plan_date,
        "channelCount": len(items),
        "items": items,
    }


async def calibrate_plan(
    plan: RestockPlan,
    *,
    suggestions: dict[str, BaselineSuggestion] | None = None,
    model: Any | None = None,
    settings: Settings | None = None,
) -> CalibrationOutcome:
    """对一份设备级计划做 LLM 校准（失败即降级，不抛异常）。

    @param suggestions 基线结果（按 channel_code 索引），用于给模型提供统计口径
    @param model 注入的 LLM 客户端（测试用假实现；None 时按配置真实构造）
    """
    settings = settings or get_settings()
    suggestions = suggestions or {}
    outcome = CalibrationOutcome(inner_code=plan.inner_code, applied=False, items=list(plan.items))

    if not settings.restock_calibration_enabled:
        outcome.issues.append(
            "校准开关已关闭（DKD_AGENT_RESTOCK_CALIBRATION_ENABLED=0），使用纯统计基线"
        )
        return outcome
    if not plan.items:
        outcome.issues.append("计划无明细，跳过校准")
        return outcome

    payload = build_payload(plan, suggestions=suggestions)
    known_channels = [item.channel_code for item in plan.items]
    try:
        client = model or build_chat_model(settings)
        response = await client.ainvoke(
            [
                (
                    "system",
                    system_prompt(
                        factor_min=settings.restock_calibration_min_factor,
                        factor_max=settings.restock_calibration_max_factor,
                    ),
                ),
                ("human", build_user_prompt(payload)),
            ]
        )
    except Exception as exc:  # LLM 异常一律降级，不抛穿图循环（AGENTS §6.3）
        logger.warning(
            "LLM 校准失败，降级为统计基线 inner_code=%s err=%s: %s",
            plan.inner_code,
            type(exc).__name__,
            str(exc)[:200],
        )
        outcome.issues.append(f"LLM 调用失败（{type(exc).__name__}），已降级为统计基线")
        outcome.error = type(exc).__name__
        return outcome

    content = response.content if isinstance(response.content, str) else str(response.content)
    outcome.tokens_in, outcome.tokens_out = extract_usage(response)
    outcome.model = extract_served_model(response)

    entries, issues, notes = parse_calibrations(
        content, known_channels=known_channels, settings=settings
    )
    outcome.issues.extend(issues)
    outcome.notes = notes
    outcome.entries = entries
    if not entries:
        outcome.issues.append("没有可用的校准项，已保留统计基线结果")
        return outcome

    by_channel = {entry.channel_code: entry for entry in entries}
    calibrated: list[RestockItem] = []
    for item in plan.items:
        entry = by_channel.get(item.channel_code)
        calibrated.append(apply_factor(item, entry) if entry else item)
    outcome.items = calibrated
    outcome.applied = True
    # 日志只记结构化指标（模型/条数/token），不记 prompt 与响应体（AGENTS §6.1）
    logger.info(
        "LLM 校准完成 inner_code=%s entries=%s issues=%s tokens_in=%s tokens_out=%s",
        plan.inner_code,
        len(entries),
        len(outcome.issues),
        outcome.tokens_in,
        outcome.tokens_out,
    )
    return outcome


async def calibrate_state_node(
    state: dict[str, Any], *, model: Any | None = None
) -> dict[str, Any]:
    """LangGraph 节点：校准 state 里的全部计划。

    state 契约（0-13 冻结稿）：读 `plans`（RestockPlan dump），
    写回校准后的 `plans` 与 `calibration_notes`（vm_id → 校准明细，供审计与前端展示）。
    **失败不写 failures**：校准失败不是业务失败，补货建议本身仍然可用。
    """
    settings = get_settings()
    plans = [RestockPlan(**raw) for raw in state.get("plans", [])]
    notes: dict[str, Any] = dict(state.get("calibration_notes") or {})
    updated: list[dict[str, Any]] = []
    tokens_in = tokens_out = 0

    for plan in plans:
        outcome = await calibrate_plan(plan, model=model, settings=settings)
        tokens_in += outcome.tokens_in
        tokens_out += outcome.tokens_out
        updated.append(plan.model_copy(update={"items": outcome.items}).model_dump(mode="json"))
        notes[str(plan.vm_id)] = {
            "applied": outcome.applied,
            "entries": [entry.model_dump() for entry in outcome.entries],
            "issues": outcome.issues,
            "notes": outcome.notes,
            "model": outcome.model,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
        }
    # 注：本节点**不写 failures** —— 校准失败已逐设备记在 calibration_notes[x].issues 中，
    # 避免运营看到一条“失败”却不知道补货建议依然有效（失败 ≠ 业务失败，AGENTS §6.3）。
    result: dict[str, Any] = {"plans": updated, "calibration_notes": notes}
    if tokens_in or tokens_out:
        # 供上层（定时任务/会话）把成本计入 agent_message/decision_log
        result["calibration_tokens"] = {"in": tokens_in, "out": tokens_out}
    return result
