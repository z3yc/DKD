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

    # --- MySQL 只读账号 ---
    db_host: str = Field(default="127.0.0.1", validation_alias="DKD_AGENT_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="DKD_AGENT_DB_PORT")
    db_name: str = Field(default="dkd", validation_alias="DKD_AGENT_DB_NAME")
    db_user: str = Field(default="dkd_agent_ro", validation_alias="DKD_AGENT_DB_USER")
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

    @property
    def readonly_dsn(self) -> str:
        """SQLAlchemy async DSN（只读账号）。"""
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    """进程内单例（lru_cache 代替全局变量，便于测试注入）。"""
    return Settings()
