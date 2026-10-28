"""项目配置：只从环境变量读取，启动时 fail-fast。

DEPS: devkit.config

新增配置的步骤：① 在这里加字段（带 description）② 在 `.env.example` 加同名注释项
③ 若生产必填，不给默认值（pydantic 会在启动时报错，早于"跑起来才发现没配"）。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from devkit.config import BaseAppSettings

__all__ = ["Settings"]


class Settings(BaseAppSettings):
    """`__APP_NAME__` 的配置。环境变量前缀 `__APP_PREFIX__`。"""

    model_config = SettingsConfigDict(
        env_prefix="__APP_PREFIX__",
        env_file=".env",
        extra="ignore",
    )

    # --- 示例：外部依赖（按需增删）---
    # db_dsn: str = Field(..., description="数据库连接串，生产必填")
    downstream_base_url: str = Field(
        default="http://127.0.0.1:8080", description="下游服务地址"
    )

    # --- 示例：功能开关（命名用 ENABLED，不用 DISABLED，见契约 E7）---
    demo_feature_enabled: bool = Field(default=False, description="示例开关")
