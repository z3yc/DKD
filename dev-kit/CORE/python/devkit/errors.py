"""统一业务异常与错误码类别。

DEPS: 无（零依赖）。故意不依赖 FastAPI —— 框架适配放在模板里，本模块保持可摘取。

错误码契约见 CONTRACTS/error-codes.md：`<SYS><CC><NN>`，retryable 由类别决定。
"""

from __future__ import annotations

from typing import Any

__all__ = ["ErrorCategory", "CategorySpec", "DomainError"]


class CategorySpec:
    """错误类别的元数据：HTTP 状态 + 是否可重试。"""

    def __init__(self, code: str, http_status: int, retryable: bool, label: str) -> None:
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.label = label


class ErrorCategory:
    """类别常量。新增类别必须同步更新 CONTRACTS/error-codes.md 的登记表。"""

    OK = CategorySpec("00", 200, False, "成功")
    PARAM = CategorySpec("01", 400, False, "参数错误")
    UNAUTHORIZED = CategorySpec("02", 401, False, "鉴权失败")
    FORBIDDEN = CategorySpec("03", 403, False, "权限不足")
    NOT_FOUND = CategorySpec("04", 404, False, "资源不存在")
    CONFLICT = CategorySpec("05", 409, False, "状态冲突")
    RATE_LIMIT = CategorySpec("06", 429, True, "限流")
    DEPENDENCY = CategorySpec("07", 503, True, "依赖不可用")
    INTERNAL = CategorySpec("50", 500, False, "内部错误")


class DomainError(Exception):
    """业务异常。**所有**预期内的失败都必须用它，不允许裸抛 RuntimeError。

    为什么强制：内部错误码必须只由全局兜底产生。业务分支抛 RuntimeError 会让
    调用方看到 500，从而把"用户填错了"当成"系统挂了"，告警也会被污染。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        category: CategorySpec = ErrorCategory.INTERNAL,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
        self.category = category
        self.http_status = category.http_status
        self.retryable = category.retryable
        # 保留异常链：禁止 `raise X("失败")` 丢掉原始 cause
        self.__cause__ = cause

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    # --- 便捷构造器：避免各处手写类别导致归类不一致 ---
    @classmethod
    def param(cls, code: str, message: str, **kw: Any) -> DomainError:
        return cls(code, message, category=ErrorCategory.PARAM, **kw)

    @classmethod
    def not_found(cls, code: str, message: str, **kw: Any) -> DomainError:
        return cls(code, message, category=ErrorCategory.NOT_FOUND, **kw)

    @classmethod
    def conflict(cls, code: str, message: str, **kw: Any) -> DomainError:
        return cls(code, message, category=ErrorCategory.CONFLICT, **kw)

    @classmethod
    def unauthorized(cls, code: str, message: str, **kw: Any) -> DomainError:
        return cls(code, message, category=ErrorCategory.UNAUTHORIZED, **kw)

    @classmethod
    def forbidden(cls, code: str, message: str, **kw: Any) -> DomainError:
        return cls(code, message, category=ErrorCategory.FORBIDDEN, **kw)

    @classmethod
    def dependency(cls, code: str, message: str, **kw: Any) -> DomainError:
        """下游不可用。retryable=True，调用方应退避重试。"""
        return cls(code, message, category=ErrorCategory.DEPENDENCY, **kw)

    @classmethod
    def internal(cls, code: str, message: str, **kw: Any) -> DomainError:
        return cls(code, message, category=ErrorCategory.INTERNAL, **kw)
