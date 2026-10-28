"""配置基类：所有配置只从环境变量读取，启动时 fail-fast。

DEPS: pydantic, pydantic-settings

用法（项目内）：
    class Settings(BaseAppSettings):
        model_config = SettingsConfigDict(env_prefix="MYAPP_", env_file=".env", extra="ignore")
        db_dsn: str = Field(..., description="只读库 DSN")   # 必填，缺失即启动失败

    settings = Settings()   # pydantic 会在缺必填项时抛错 —— 这就是 E2 的 fail-fast
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["BaseAppSettings", "is_truthy"]


class BaseAppSettings(BaseSettings):
    """所有服务共享的最小配置集。子类按模块扩展，不要往这里塞业务字段。

    字段命名即环境变量名（去前缀后小写），例：`http_timeout_ms` ← `MYAPP_HTTP_TIMEOUT_MS`。
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 通用 ---
    service_name: str = Field(default="app", description="服务名，写入日志 service 字段")
    env: str = Field(default="local", description="local/dev/test/prod")
    timezone: str = Field(default="Asia/Shanghai", description="进程时区")

    # --- 日志（契约见 CONTRACTS/log-fields.md）---
    log_level: str = Field(default="INFO", description="DEBUG/INFO/WARN/ERROR")
    log_format: str = Field(default="json", description="json=生产；console=本地")

    # --- 出站 HTTP（契约见 CONTRACTS/env-naming.md）---
    http_timeout_ms: int = Field(default=10_000, description="出站 HTTP 超时（毫秒）")
    http_max_retries: int = Field(default=2, description="幂等请求的最大重试次数")

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def http_timeout_seconds(self) -> float:
        return self.http_timeout_ms / 1000.0


def is_truthy(raw: str | None, default: bool = False) -> bool:
    """统一解析布尔配置，避免 `1/0`、`yes/no`、`true/false` 混用（契约 E6）。

    只接受 `true/false`（大小写不敏感），其他值一律回退到 default 而不是猜。
    """
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("true", "false"):
        return normalized == "true"
    return default
