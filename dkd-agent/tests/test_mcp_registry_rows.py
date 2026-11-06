"""MCP 工具行（source=mcp）在工具注册表中的边界（3-7 集成回归）。

为什么需要：`agent_tool` 的本地行必须命中代码里的工具白名单，否则启动即拒绝；
但 MCP 工具是**运行期同步**进来的（`mcp.<server>.<tool>`），代码里没有同名实现，
若仍按本地白名单校验，一接 MCP 服务就启动失败。这里锁住“只放行 source=mcp、本地仍严格”。
"""

from __future__ import annotations

from typing import Any

from app.services.tool_registry import CHAT_SCOPE, ToolRegistry


def _mcp_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "code": "mcp.mock.read_machine_manual",
        "name": "read_machine_manual",
        "scope": CHAT_SCOPE,
        "source": "mcp",
        "read_only": True,
        "enabled": True,
        "timeout_ms": 5000,
        "row_limit": 200,
        "builtin": False,
    }
    row.update(overrides)
    return row


def test_mcp_row_is_not_treated_as_unknown_and_is_enabled() -> None:
    registry = ToolRegistry(db_enabled=False)
    registry._accept_rows([_mcp_row()])  # noqa: SLF001 —— 单测直接喂行，避免连库

    stats = registry.stats()
    assert stats["enabled"] == 1
    assert stats["unknown"] == 0
    assert registry._unknown_codes == ()  # noqa: SLF001


def test_local_unknown_row_still_rejected() -> None:
    registry = ToolRegistry(db_enabled=False)
    registry._accept_rows(
        [
            _mcp_row(
                code="not_a_real_local_tool",
                name="not_a_real_local_tool",
                source="local",
            )
        ]
    )
    assert registry._unknown_codes == ("not_a_real_local_tool",)  # noqa: SLF001


def test_disabled_mcp_row_not_in_enabled_set() -> None:
    registry = ToolRegistry(db_enabled=False)
    registry._accept_rows([_mcp_row(enabled=False)])

    assert registry.stats()["enabled"] == 0
