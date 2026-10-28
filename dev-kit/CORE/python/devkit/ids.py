"""ID 生成与请求上下文。

DEPS: 无（仅标准库）。可单独摘取到任何项目，不需要 devkit 其他模块。

为什么自己实现而不用 uuid4：
- uuid4 完全无序，作为 DB 主键会造成索引页分裂；
- 排障时需要"看到 id 就知道大概什么时候"，时间序前缀能省掉一次查库；
- 用 Crockford Base32（去掉 I/L/O/U）避免手抄时的视觉歧义。
"""

from __future__ import annotations

import secrets
import time
from contextvars import ContextVar

# Crockford Base32：剔除 I/L/O/U，避免人工抄写与 OCR 歧义
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ALPHABET_LEN = len(_ALPHABET)

# 时间序前缀长度：10 字符 Base32 ≈ 50 bit，覆盖到公元 5000 年毫秒级
_TS_LEN = 10
_RAND_LEN = 10


def _encode(value: int, length: int) -> str:
    """把非负整数编码为固定长度的 Base32 字符串（高位在前，保证字典序即时间序）。"""
    out = []
    remaining = value
    for _ in range(length):
        remaining, idx = divmod(remaining, _ALPHABET_LEN)
        out.append(_ALPHABET[idx])
    return "".join(reversed(out))


def new_id(prefix: str = "") -> str:
    """生成时间序 ID。`new_id("task")` → `task-01J9Z8Y7X6W5V4U3T2S1R0`。

    前 10 位为毫秒时间戳（可排序），后 10 位为随机数（防碰撞）。
    """
    ts_ms = time.time_ns() // 1_000_000
    rand = secrets.randbits(_RAND_LEN * 5)
    body = f"{_encode(ts_ms, _TS_LEN)}{_encode(rand, _RAND_LEN)}"
    return f"{prefix}-{body}" if prefix else body


def new_request_id() -> str:
    """生成贯穿全链路的 request_id（不带前缀，便于日志检索）。"""
    return new_id()


# --- 请求上下文 ---------------------------------------------------------------
# ContextVar 而非全局变量：async 并发下不会串号，也不需要把 request_id 层层传参。
REQUEST_ID_VAR: ContextVar[str] = ContextVar("devkit_request_id", default="-")


def bind_request_id(request_id: str | None = None) -> str:
    """把 request_id 绑定到当前上下文；不传则新生成一个。返回实际使用的值。"""
    rid = request_id or new_request_id()
    REQUEST_ID_VAR.set(rid)
    return rid


def get_request_id() -> str:
    """读取当前上下文的 request_id，未绑定时返回 `-`（而非抛错，避免日志本身成为故障源）。"""
    return REQUEST_ID_VAR.get()
