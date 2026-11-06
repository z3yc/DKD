"""智能体配置的「代码内置默认值」唯一事实源（排期 2-1 / 2-3 / 2-7）。

## 为什么单独一个模块

排期 2-7 的验收是「**配置值 = 默认常量时，prompt 与硬编码逐字相同**」，而 2-1 的
seed 要求「从现有代码常量**逐字导出**」。如果默认值在 seed 脚本、config_store、
各载体里各写一份，三份迟早漂移——那时线上看到的行为差异根本无从归因。

因此约定：**默认值只能定义在本模块**，且必须直接引用现有源码常量
（`app.prompts.restock_calibration.SYSTEM_PROMPT`、
`app.prompts.assistant_chat.BASE_PROMPT`、`app.config.Settings` 的同名默认值），
**不得复制粘贴字符串**。seed 脚本（`docs/scripts/gen-agent-config-seed.py`）
和 config_store 的三重回落都从这里取值。

## 边界

- 本模块是**纯常量/纯函数**：不读环境变量、不连数据库、不依赖 FastAPI，
  否则 seed 脚本与单测都得先把服务环境搭起来；
- 运行期可变的参数（业务阈值）以 `params_json` 承载，区间也在本模块声明；
- 提示词模板保留在各 `app/prompts/*.py`（AGENTS §2.3 要求按 Agent 分文件），
  这里只做引用与导出。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from app.prompts.assistant_chat import BASE_PROMPT, TOOL_NAMES_BY_SCENE
from app.prompts.restock_calibration import FACTOR_MAX, FACTOR_MIN, SYSTEM_PROMPT

logger = logging.getLogger("dkd.agent.agent_defaults")

# ------------------------------------------------------------------------------------
# 运行载体与内置智能体
# ------------------------------------------------------------------------------------
RUNTIME_CHAT = "chat"
RUNTIME_RESTOCK = "restock"
RUNTIME_FAULT = "fault"
RUNTIMES: tuple[str, ...] = (RUNTIME_CHAT, RUNTIME_RESTOCK, RUNTIME_FAULT)

#: 内置智能体编码（固定，不允许新增/删除/改 runtime；见配置化方案 §2.1）
BUILTIN_CODES: tuple[str, ...] = ("restock", "fault", "chat")

#: 编码规则（配置化方案 §3）
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,31}$")

#: 自定义智能体场景码起始值（1-4 为内置场景，见 docs/ddl/agent_tables.sql 列注释）
CUSTOM_SCENE_BASE = 101

#: 模型白名单：前端可选项 + Java 保存校验共用口径。
#: 注意：这里只约束**配置里能填什么**，不改 `DKD_AGENT_LLM_MODEL` 的 env 默认值——
#: 配置留空（NULL/空串）时仍走 env，保证「未配置 = 现状」。
#: deepseek-chat / deepseek-reasoner 是 DeepSeek 官方当前可用模型名。
MODEL_WHITELIST: tuple[str, ...] = ("deepseek-chat", "deepseek-reasoner")

# ------------------------------------------------------------------------------------
# 业务参数（params_json）的默认值与合法区间
# ------------------------------------------------------------------------------------
# 区间的业务依据：配置化方案 §2.3；上下界都刻意“窄”：LLM/人工手滑不应当能把建议量
# 放大 10 倍或清零（AGENTS §7.6）。
PARAM_SPECS: dict[str, dict[str, dict[str, Any]]] = {
    RUNTIME_RESTOCK: {
        "quantile": {"default": 0.75, "min": 0.50, "max": 0.99, "type": "float"},
        "coverage_days": {"default": 2.0, "min": 1.0, "max": 14.0, "type": "float"},
        "service_factor": {"default": 1.2, "min": 0.5, "max": 3.0, "type": "float"},
        "factor_min": {
            "default": FACTOR_MIN,
            "min": 0.50,
            "max": 2.00,
            "type": "float",
        },
        "factor_max": {
            "default": FACTOR_MAX,
            "min": 0.50,
            "max": 2.00,
            "type": "float",
        },
        "batch_size": {"default": 20, "min": 5, "max": 100, "type": "int"},
    },
    RUNTIME_FAULT: {
        "window_hours": {"default": 24, "min": 1, "max": 168, "type": "int"},
        "confidence_order_threshold": {"default": 2, "min": 1, "max": 10, "type": "int"},
    },
    RUNTIME_CHAT: {},
}

#: prompt 中允许出现的占位符白名单（配置化方案 §3）。
#: ⚠️ 与方案文档的差异（有意）：chat 用的是现有 `_BASE_PROMPT` 里**真实存在**的占位符
#: `{focus} {tool_list} {write_guidance}`；`{window_days}` 在代码里是**追加的**窗口说明行，
#: 不在 `_BASE_PROMPT` 模板内。若把 `{window_days}` 也列进白名单，用户写进模板后代码
#: 无处渲染，只会得到 KeyError 或原样花括号——比直接拒绝更糟。
ALLOWED_PLACEHOLDERS: dict[str, frozenset[str]] = {
    RUNTIME_CHAT: frozenset({"focus", "tool_list", "write_guidance"}),
    RUNTIME_RESTOCK: frozenset({"fmin", "fmax"}),
    RUNTIME_FAULT: frozenset(),
}

#: 载体的工具白名单（配置化方案 §2.3/§5.3）。
#: - chat：`app/prompts/assistant_chat.py::TOOL_NAMES_BY_SCENE` 的**并集**（唯一事实源，
#:   避免“提示词里能提、配置里选不到”或反向漂移）；
#: - restock/fault：首期不开放工具子集（载体内部流程不是 LLM 工具），留空。
TOOL_WHITELIST_BY_RUNTIME: dict[str, tuple[str, ...]] = {
    RUNTIME_CHAT: tuple(sorted({name for names in TOOL_NAMES_BY_SCENE.values() for name in names})),
    RUNTIME_RESTOCK: (),
    RUNTIME_FAULT: (),
}


# ------------------------------------------------------------------------------------
# 默认值导出
# ------------------------------------------------------------------------------------
def default_params(runtime: str) -> dict[str, float | int]:
    """该载体业务参数的默认值（深拷贝为普通 dict，防止调用方改坏常量）。"""
    specs = PARAM_SPECS.get(runtime, {})
    return {key: spec["default"] for key, spec in specs.items()}


def default_system_prompt(runtime: str) -> str | None:
    """该载体的默认提示词（`None` = 该载体不调用 LLM，没有提示词）。

    为什么 fault 是 None：诊断结论由 `app/services/diagnosis.py` 的纯规则合成，
    不调用 LLM（配置化方案 §0.1）。给一个“解释性提示词”只会制造
    “配置里有、运行时不生效”的假象。
    """
    if runtime == RUNTIME_RESTOCK:
        return SYSTEM_PROMPT
    if runtime == RUNTIME_CHAT:
        return BASE_PROMPT
    return None


def default_tools(runtime: str) -> str:
    """该载体默认工具集（逗号分隔；空串 = 无工具子集）。"""
    return ",".join(TOOL_WHITELIST_BY_RUNTIME.get(runtime, ()))


def builtin_defaults() -> list[dict[str, Any]]:
    """内置三智能体的 seed 行（顺序稳定：restock / fault / chat）。

    字段与 `docs/ddl/agent_config.sql` 一一对应；`version=1`、`enabled=1`。
    `model` / `temperature` / `max_tokens` 留空是**刻意的**：空 = 走 env/模型默认，
    与改造前完全一致（配置化方案 §2.1、§5.5）。
    """
    return [
        {
            "code": "restock",
            "name": "智能补货 Agent",
            "description": "按分位销量与容量约束生成补货建议，LLM 只校准系数（不改数量）",
            "runtime": RUNTIME_RESTOCK,
            "scene": 2,
            "builtin": 1,
            "system_prompt": default_system_prompt(RUNTIME_RESTOCK),
            "tools": default_tools(RUNTIME_RESTOCK),
            "model": "",
            "temperature": None,
            "max_tokens": None,
            "timeout_s": 60,
            "max_retries": 2,
            "params_json": default_params(RUNTIME_RESTOCK),
            "enabled": 1,
        },
        {
            "code": "fault",
            "name": "设备故障诊断 Agent",
            "description": "规则化异常检测 + 多轮问诊 + 派单提案；结论不含病因，写操作人工确认",
            "runtime": RUNTIME_FAULT,
            "scene": 3,
            "builtin": 1,
            "system_prompt": default_system_prompt(RUNTIME_FAULT),
            "tools": default_tools(RUNTIME_FAULT),
            "model": "",
            "temperature": None,
            "max_tokens": None,
            "timeout_s": 60,
            "max_retries": 2,
            "params_json": default_params(RUNTIME_FAULT),
            "enabled": 1,
        },
        {
            "code": "chat",
            "name": "AI 助手（对话载体）",
            "description": "后台右侧助手抽屉的对话载体：通用/补货/诊断/运营分析四场景共用一张图",
            "runtime": RUNTIME_CHAT,
            "scene": 1,
            "builtin": 1,
            "system_prompt": default_system_prompt(RUNTIME_CHAT),
            "tools": default_tools(RUNTIME_CHAT),
            "model": "",
            "temperature": None,
            "max_tokens": None,
            "timeout_s": 60,
            "max_retries": 2,
            "params_json": default_params(RUNTIME_CHAT),
            "enabled": 1,
        },
    ]


# ------------------------------------------------------------------------------------
# 夹取（LLM 输出与人工配置都按不可信输入处理，AGENTS §7.6）
# ------------------------------------------------------------------------------------
def _coerce_number(spec: Mapping[str, Any], value: Any, *, key: str, runtime: str) -> float | int:
    """把单个参数转成合法数值；非法/缺省一律回落默认值（只 WARN，不抛异常）。"""
    expected = spec["type"]
    default = spec["default"]
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        logger.warning("配置参数非法，回落默认值：runtime=%s key=%s value=%r", runtime, key, value)
        return default
    if expected == "int":
        number = int(round(number))
    low, high = spec["min"], spec["max"]
    clamped = min(max(number, low), high)
    if clamped != number:
        logger.warning(
            "配置参数越界，已夹取：runtime=%s key=%s value=%r → %r", runtime, key, number, clamped
        )
    return clamped


def normalize_params(runtime: str, raw: Mapping[str, Any] | None) -> dict[str, float | int]:
    """把 `agent_config.params_json` 归一化为**该载体完整、合法**的参数表。

    三重回落里的“超范围→默认/夹取”落实在这里：
      1. 缺失/空 → 默认值；
      2. 非数值 → 默认值 + WARN；
      3. 越界 → 夹取到区间 + WARN。

    额外业务规则（配置化方案 §3）：
      - `factor_min < factor_max`，否则两者一起回落默认值（只修一个会让语义更怪）；
      - restock 的 `factor_min/factor_max` 与模块级 `FACTOR_MIN/FACTOR_MAX` 同源，
        夹取后仍要保证 `min < max`。

    @param runtime 运行载体
    @param raw 来自 DB 的原始 JSON（可为 None/损坏后的 dict）
    @return 完整参数表（未知键丢弃）
    """
    specs = PARAM_SPECS.get(runtime, {})
    source: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    out: dict[str, float | int] = {}
    for key, spec in specs.items():
        out[key] = _coerce_number(spec, source.get(key), key=key, runtime=runtime)

    if runtime == RUNTIME_RESTOCK:
        factor_min = float(out["factor_min"])
        factor_max = float(out["factor_max"])
        if factor_min >= factor_max:
            logger.warning(
                "配置参数自相矛盾（factor_min>=factor_max），回落默认值：%r >= %r",
                factor_min,
                factor_max,
            )
            out["factor_min"] = specs["factor_min"]["default"]
            out["factor_max"] = specs["factor_max"]["default"]
    return out


__all__ = [
    "ALLOWED_PLACEHOLDERS",
    "BUILTIN_CODES",
    "CODE_PATTERN",
    "CUSTOM_SCENE_BASE",
    "MODEL_WHITELIST",
    "PARAM_SPECS",
    "RUNTIMES",
    "RUNTIME_CHAT",
    "RUNTIME_FAULT",
    "RUNTIME_RESTOCK",
    "TOOL_WHITELIST_BY_RUNTIME",
    "builtin_defaults",
    "default_params",
    "default_system_prompt",
    "default_tools",
    "normalize_params",
]
