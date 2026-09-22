"""DKD 智能体服务 · 配置加载（任务 0-2/0-9）。

为什么单独一个模块：AGENTS.md §2.3 要求 pydantic 模型承载一切对外契约，
凭据只从环境变量读（§7.1 密钥永不入库），代码里不得出现任何默认密钥值。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """服务配置。

    环境变量命名（与仓库根 `.env` 对齐）：
      DKD_AGENT_*          —— 智能体服务自身参数
      DKD_DEEPSEEK_API_KEY —— 复用 Java 侧同一 LLM 账号（方案 §5.1）
      DKD_AGENT_DB_*       —— MySQL **只读账号**（绝不使用 Java 的 root 凭据）
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 运行环境 ---
    app_env: str = Field(default="dev", validation_alias="DKD_AGENT_ENV")
    app_port: int = Field(default=8090, validation_alias="DKD_AGENT_PORT")
    app_host: str = Field(default="127.0.0.1", validation_alias="DKD_AGENT_HOST")

    # --- 服务间密钥（Python -> Java 回调鉴权；与 Java AgentProperties.secret 对应）---
    # 空值即拒绝所有回调类请求，避免"忘了配就用默认值裸奔"
    service_secret: str = Field(default="", validation_alias="DKD_AGENT_SERVICE_SECRET")

    # --- LLM（DeepSeek，OpenAI 兼容）---
    llm_api_key: str = Field(default="", validation_alias="DKD_DEEPSEEK_API_KEY")
    llm_base_url: str = Field(
        default="https://api.deepseek.com", validation_alias="DKD_AGENT_LLM_BASE_URL"
    )
    llm_model: str = Field(default="deepseek-chat", validation_alias="DKD_AGENT_LLM_MODEL")
    llm_timeout_s: float = Field(default=60.0, validation_alias="DKD_AGENT_LLM_TIMEOUT")

    # --- 会话持久化（SQLite saver；Redis 3.2 不可用的结论见方案 V1.1 §5.1）---
    sqlite_path: Path = Field(
        default=Path("var/checkpoints.db"), validation_alias="DKD_AGENT_SQLITE_PATH"
    )

    # --- 对话模式与限额（任务 0-12：成本计量与熔断）---
    use_llm: bool = Field(default=False, validation_alias="DKD_AGENT_USE_LLM")
    audit_enabled: bool = Field(default=True, validation_alias="DKD_AGENT_AUDIT_ENABLED")
    token_daily_limit_per_user: int = Field(
        default=200_000, validation_alias="DKD_AGENT_TOKEN_LIMIT_PER_USER"
    )
    token_daily_limit_global: int = Field(
        default=2_000_000, validation_alias="DKD_AGENT_TOKEN_LIMIT_GLOBAL"
    )

    # --- MySQL 只读账号 ---
    db_host: str = Field(default="127.0.0.1", validation_alias="DKD_AGENT_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="DKD_AGENT_DB_PORT")
    db_name: str = Field(default="dkd", validation_alias="DKD_AGENT_DB_NAME")
    db_user: str = Field(default="dkd_agent", validation_alias="DKD_AGENT_DB_USER")
    db_password: str = Field(default="", validation_alias="DKD_AGENT_DB_PASSWORD")

    # --- Java 侧回调地址（写操作只能走这里，AGENTS §7.3）---
    java_base_url: str = Field(
        default="http://127.0.0.1:8080", validation_alias="DKD_AGENT_JAVA_BASE_URL"
    )

    # --- 只读表白名单（防越权；表名必须带 tb_ 前缀，AGENTS §1 约束 5）---
    table_whitelist: str = Field(
        default=(
            "tb_inventory,tb_inventory_log,tb_order,tb_task,tb_task_details,"
            "tb_channel,tb_sku,tb_sku_class,tb_vending_machine,tb_vm_type,"
            "tb_node,tb_region,tb_emp,tb_job,tb_policy,tb_partner,tb_task_type"
        ),
        validation_alias="DKD_AGENT_TABLE_WHITELIST",
    )

    @property
    def whitelist_tables(self) -> frozenset[str]:
        """解析为集合，便于 O(1) 校验工具层 SQL 的取表名。"""
        return frozenset(t.strip() for t in self.table_whitelist.split(",") if t.strip())

    # --- 只读查询护栏（Phase 1 / 任务 1-2：时分窗口 + 分批 + 超时熔断）---
    # 为什么需要这三个：本机只有一个主库（无只读从库，application-druid.yml slave.enabled=false），
    # 分析类查询会和业务抢同一台 MySQL，所以任何聚合都必须能“限时、限批、限行”。
    read_query_timeout_s: float = Field(
        default=5.0, validation_alias="DKD_AGENT_READ_QUERY_TIMEOUT_S"
    )
    read_batch_size: int = Field(default=50, validation_alias="DKD_AGENT_READ_BATCH_SIZE")
    read_row_limit: int = Field(default=2000, validation_alias="DKD_AGENT_READ_ROW_LIMIT")

    # 订单“销量”口径：事实依据 ReportServiceImpl.java:28 的 ORDER_STATUS_SUCCESS=2
    sales_order_status: int = Field(default=2, validation_alias="DKD_AGENT_SALES_ORDER_STATUS")

    # --- Java 回调（写操作唯一通道，任务 1-3）---
    # 为什么给 10s：建单是非幂等动作，超时宁可显式失败也不盲目重试
    # （见 app/tools/task_tools.py）
    callback_timeout_s: float = Field(default=10.0, validation_alias="DKD_AGENT_CALLBACK_TIMEOUT_S")

    # --- 补货基线引擎参数（Phase 1 / 任务 1-4）---
    # 为什么全部做成配置而不是常量：这些是**业务参数**（服务水平/补货周期随运营策略变），
    # 排期 3-6/5-2 要按复盘结果迭代它们；硬编码会让“调参”变成“改代码+发版”。
    # 每个默认值的来历都写在 restock_baseline.py 模块 docstring 里（含“待业务确认”标记）。
    restock_quantile: float = Field(default=0.75, validation_alias="DKD_AGENT_RESTOCK_QUANTILE")
    restock_coverage_days: float = Field(
        default=2.0, validation_alias="DKD_AGENT_RESTOCK_COVERAGE_DAYS"
    )
    restock_service_factor: float = Field(
        default=1.2, validation_alias="DKD_AGENT_RESTOCK_SERVICE_FACTOR"
    )
    restock_min_stock_multiple: float = Field(
        default=2.0, validation_alias="DKD_AGENT_RESTOCK_MIN_STOCK_MULTIPLE"
    )
    restock_fill_ratio: float = Field(default=0.85, validation_alias="DKD_AGENT_RESTOCK_FILL_RATIO")
    # 多窗口权重：近窗更敏感（能跟上一周的趋势），长窗更稳（抗单日爆量）
    restock_window_weights: str = Field(
        default="7:0.5,14:0.3,30:0.2", validation_alias="DKD_AGENT_RESTOCK_WINDOW_WEIGHTS"
    )

    # --- LLM 校准（Phase 1 / 任务 1-5）---
    # 开关的意义：DeepSeek 不可用时补货链路必须能退化成“纯统计基线”
    # （最坏退化为可解释的规则增强版），
    # 而不是整个定时任务失败（见方案 §8 风险表“LLM 建议质量不达标”的兜底设计）。
    restock_calibration_enabled: bool = Field(
        default=True, validation_alias="DKD_AGENT_RESTOCK_CALIBRATION_ENABLED"
    )
    # 系数夹取区间：LLM 输出属**不可信输入**（AGENTS §7.6），不能允许它把建议量放大 10 倍或清零
    restock_calibration_min_factor: float = Field(
        default=0.7, validation_alias="DKD_AGENT_RESTOCK_CALIBRATION_MIN_FACTOR"
    )
    restock_calibration_max_factor: float = Field(
        default=1.5, validation_alias="DKD_AGENT_RESTOCK_CALIBRATION_MAX_FACTOR"
    )
    # 单次请求最多送多少条货道（控制 prompt 体积与 token 成本；超出的分批调用）
    restock_calibration_batch_size: int = Field(
        default=20, validation_alias="DKD_AGENT_RESTOCK_CALIBRATION_BATCH_SIZE"
    )

    @property
    def restock_weights(self) -> dict[int, float]:
        """解析 `7:0.5,14:0.3,30:0.2` → {7: 0.5, 14: 0.3, 30: 0.2}。

        非法格式直接抛错，不静默退回默认值——否则改错了配置没人发现，建议量却悄悄变了。
        """
        weights: dict[int, float] = {}
        for chunk in self.restock_window_weights.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            days_text, _, weight_text = chunk.partition(":")
            weights[int(days_text.strip())] = float(weight_text.strip())
        if not weights:
            raise ValueError("DKD_AGENT_RESTOCK_WINDOW_WEIGHTS 不能为空")
        return weights

    @property
    def dsn(self) -> str:
        """SQLAlchemy async DSN。

        账号 `dkd_agent` 采用两级授权：业务表（tb_*）只读白名单，
        智能体自有表（agent_*）可 SELECT/INSERT/UPDATE（不授 DELETE/DDL）。
        详见 `app/db.py` 模块 docstring。
        """
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    """进程内单例（lru_cache 代替全局变量，便于测试注入）。"""
    return Settings()
