"""载体读取智能体配置的薄封装（排期 2-7，契约 §5.2）。

## 为什么单独一个模块

三个载体（chat / restock / fault）都要做同一件事：**读配置 → 失败回落代码默认**。
落回的边界必须只有一处定义：

1. `app/config_store.py`（python-store）负责**三重回落**（总开关关闭 / 表缺失或 DB 不可达 /
   JSON 损坏或 runtime 非法）并返回 `None`；
2. 本模块只再加一层「读配置这条链路本身出任何异常都不得抛穿业务」
   （AGENTS §6.3：编排层兜底，不把基础设施故障变成用户可见的 500）——**且只 WARN 不静默**。

若各载体各写一份 try/except，迟早出现「chat 回落了、restock 抛 500」这类偏差，
排查时会先怀疑“配置写坏了”，实际却是三条路径行为不一致。

## 为什么不做缓存

TTL 缓存 / 版本比对 / reload 失效都是 `config_store` 的职责（契约 §5.1）。
这里每轮都调 `get_*`，是为了「配置改后**下一次调用**生效」——缓存命中时它只是一次
内存读，不构成额外数据库压力。
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent_defaults import RUNTIME_FAULT, RUNTIME_RESTOCK
from app.config import Settings
from app.config_store import AgentConfigSnapshot, get_config_store, resolve_chat_scene

logger = logging.getLogger("dkd.agent.services.carrier_config")

#: 内置载体的配置编码（= agent_config.code，见 docs/ddl/agent_config.sql）
RESTOCK_CONFIG_CODE = RUNTIME_RESTOCK
FAULT_CONFIG_CODE = RUNTIME_FAULT

#: restock 的 `params_json` 键 → `Settings` 字段（契约 §3.1 / §5.2）。
#:
#: 为什么是「覆盖」而不是「新造一份运行参数对象」：补货基线（`restock_baseline.py`）
#: 与 LLM 校准（`restock_calibration.py`）都只认 `Settings`；把配置换算成一份 Settings
#: 副本，两处就自动同口径，不必在几十个签名里各加一个参数。
#: 也正因如此，**配置缺失时返回原对象**——保证「未配置 = 现状」。
RESTOCK_PARAM_TO_SETTING: dict[str, str] = {
    "quantile": "restock_quantile",
    "coverage_days": "restock_coverage_days",
    "service_factor": "restock_service_factor",
    "factor_min": "restock_calibration_min_factor",
    "factor_max": "restock_calibration_max_factor",
    "batch_size": "restock_calibration_batch_size",
}


async def load_config(code: str, *, store: Any | None = None) -> AgentConfigSnapshot | None:
    """按 code 读配置快照；配置不可读 → `None` + WARN（调用方回代码默认）。

    @param store 测试注入的假 store（需实现 `get(code)`）；None = 进程单例真 store。
        **只有测试会传**：生产路径永远走 `get_config_store()`，避免出现“生产用了另一份缓存”。
    """
    try:
        effective = store if store is not None else await get_config_store()
        return await effective.get(code)
    except Exception as exc:  # noqa: BLE001 —— 兜底：配置不可读不得抛穿业务（AGENTS §6.3）
        logger.warning("读取智能体配置失败，回落代码默认：code=%s err=%s", code, type(exc).__name__)
        return None


async def load_chat_config(scene: int, *, store: Any | None = None) -> AgentConfigSnapshot | None:
    """按场景读 chat 配置（内置 1-4 → chat 行且**包含停用**；自定义 ≥101 → 仅启用行）。

    语义以契约 §5.1 的 `resolve_chat_scene` 为准：
      - 场景 1-4：返回 chat 行快照（`enabled=False` 时调用方必须拒绝，见 `api/chat.py`）；
      - 场景 ≥101：只返回已启用的自定义 chat 配置，查不到 → None（调用方拒绝）。
    """
    try:
        if store is not None:
            return await store.get_by_scene(scene)
        return await resolve_chat_scene(scene)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "读取 chat 场景配置失败，按未配置处理：scene=%s err=%s", scene, type(exc).__name__
        )
        return None


async def load_restock_config(*, store: Any | None = None) -> AgentConfigSnapshot | None:
    """读 restock 载体配置（缺失 → None = 用 Settings 现状）。"""
    return await load_config(RESTOCK_CONFIG_CODE, store=store)


async def load_fault_config(*, store: Any | None = None) -> AgentConfigSnapshot | None:
    """读 fault 载体配置（缺失 → None = 用代码默认 24h / 阈值 2）。"""
    return await load_config(FAULT_CONFIG_CODE, store=store)


def effective_restock_settings(
    settings: Settings, snapshot: AgentConfigSnapshot | None
) -> Settings:
    """把 restock 配置的业务参数覆盖到 Settings 上（**缺失字段一律保持原值**）。

    数值合法性由 `agent_defaults.normalize_params` 在 config_store 侧夹取/回落，
    这里不再做第二套区间——两套夹取规则迟早漂移（契约 §5.1 明确该项归 config_store）。

    @return 新 Settings（`model_copy`；不改动传入对象，避免污染进程级单例设置）
    """
    if snapshot is None:
        return settings
    params = getattr(snapshot, "params", None) or {}
    updates = {
        field: params[param]
        for param, field in RESTOCK_PARAM_TO_SETTING.items()
        if param in params and params[param] is not None
    }
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def llm_options(snapshot: AgentConfigSnapshot | None) -> dict[str, Any]:
    """从配置快照构造 `build_chat_model` 的可选覆盖参数。

    **None/空串不进 dict**：`build_chat_model` 的约定是「不传 = 现状」，
    这里多余的键会让“未配置”变成“显式传 None”，两者对第三方库并不等价（契约 §1 Q3）。
    """
    if snapshot is None:
        return {}
    options: dict[str, Any] = {}
    model = getattr(snapshot, "model", None)
    if model:
        options["model"] = str(model)
    for attr in ("temperature", "max_tokens", "max_retries", "timeout_s"):
        value = getattr(snapshot, attr, None)
        if value is not None:
            options[attr] = value
    return options


__all__ = [
    "FAULT_CONFIG_CODE",
    "RESTOCK_CONFIG_CODE",
    "RESTOCK_PARAM_TO_SETTING",
    "effective_restock_settings",
    "llm_options",
    "load_chat_config",
    "load_config",
    "load_fault_config",
    "load_restock_config",
]
