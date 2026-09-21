"""表白名单与写权限校验（任务 0-4 的代码侧护栏）。

AGENTS §8：校验/风控逻辑**必须覆盖拒绝路径**——"该拒绝的没拒绝"比"该通过的没通过"严重得多。
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import AGENT_OWNED_TABLES, TableNotAllowedError, assert_table_allowed


def test_read_whitelisted_business_table_allowed():
    for table in ("tb_inventory", "tb_order", "tb_task", "tb_inventory_log", "tb_emp"):
        assert_table_allowed(table)  # 不抛异常即通过


def test_read_non_whitelisted_table_rejected():
    with pytest.raises(TableNotAllowedError, match="不在只读白名单"):
        assert_table_allowed("tb_report")  # 报表表不在 Agent 白名单内


def test_read_system_table_rejected():
    with pytest.raises(TableNotAllowedError, match="不在只读白名单"):
        assert_table_allowed("sys_user")


def test_read_table_without_prefix_rejected():
    """V1 方案里的错误写法（无 tb_ 前缀）必须被拦下，而不是等到 MySQL 报错。"""
    for wrong in ("inventory", "order", "task", "user", "information_schema.tables"):
        with pytest.raises(TableNotAllowedError):
            assert_table_allowed(wrong)


def test_write_to_business_table_rejected():
    """最高优先级红线：业务表写操作必须回调 Java，禁止直连（AGENTS §7.3）。"""
    for table in ("tb_inventory", "tb_task", "tb_order", "tb_vending_machine"):
        with pytest.raises(TableNotAllowedError, match="必须回调 Java REST"):
            assert_table_allowed(table, writable=True)


def test_write_to_agent_owned_tables_allowed():
    for table in sorted(AGENT_OWNED_TABLES):
        assert_table_allowed(table, writable=True)


def test_table_name_normalization():
    """大小写与反引号归一化：`TB_Inventory` 与 `tb_inventory` 等价。"""
    assert_table_allowed("  `TB_Inventory`  ")
    assert_table_allowed("AGENT_DECISION_LOG", writable=True)


def test_whitelist_comes_from_configuration_not_hardcoded():
    """白名单必须可配置（运维可收窄），而不是写死在代码里。"""
    settings = get_settings()
    assert "tb_inventory" in settings.whitelist_tables
    assert settings.whitelist_tables.isdisjoint(AGENT_OWNED_TABLES)
