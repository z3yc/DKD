"""devkit · 个人开发体系核心模块（Python 部分）。

分层与依赖（禁止反向依赖）：

    config  ← 配置（pydantic-settings）
       ↑
    logging ← 结构化日志（依赖 ids）
       ↑
    errors   ← 统一异常与错误码（零依赖）
       ↑
    http     ← HTTP 客户端（依赖 ids + errors）

`ids` 为叶子模块，任何层可直接引用。
每个模块文件头都有 `DEPS:` 声明，摘取时按声明检查即可。
"""

from __future__ import annotations

from . import config, errors, http, ids, logging_conf

__all__ = ["config", "errors", "http", "ids", "logging_conf"]
__version__ = "1.0.0"
