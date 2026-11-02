"""SQL 表名解析与「声明 / 实际」交叉校验（P0-7）的单元测试。

AGENTS §8：校验/风控逻辑**必须覆盖拒绝路径**——"该拒绝的没拒绝"比"该通过的没通过"严重得多。
这批用例的重点不是"能解析出表"，而是：

1. 常见写法的解析正确性（JOIN 变体、子查询、逗号多表、库名前缀、反引号、UNION）；
2. **不该被当成表的东西**：字符串字面量、注释里的 `from tb_xxx`；
3. **拒绝路径**：漏声明、多声明、解析不出、引号/注释未闭合、空 SQL；
4. 与 `_fetch_all` 的接线：越权表名在 SQL 到达数据库前就被拦（真实调用路径）。
"""

from __future__ import annotations

import pytest

from app.sql_guard import (
    SqlGuardError,
    assert_declared_tables_match,
    extract_tables,
)
from app.tools import read_tools


def test_simple_from_and_where():
    sql = "select id from tb_order where status = 2"
    assert extract_tables(sql) == {"tb_order"}


def test_join_variants():
    sql = """
        select o.id, i.current_stock
        from tb_order o
        left join tb_inventory i on i.vm_id = o.id
        inner join `tb_channel` c on c.id = i.channel_id
        cross join tb_vending_machine v
        where o.status = 2
    """
    assert extract_tables(sql) == {
        "tb_order",
        "tb_inventory",
        "tb_channel",
        "tb_vending_machine",
    }


def test_subquery_in_where_is_collected():
    sql = """
        select * from tb_task t
        where t.inner_code in (select inner_code from tb_vending_machine where vm_status = 1)
    """
    assert extract_tables(sql) == {"tb_task", "tb_vending_machine"}


def test_from_subquery_alias_is_not_a_table():
    sql = """
        select * from (
            select inner_code from tb_order where create_time >= :begin
        ) recent
        where recent.inner_code = :code
    """
    assert extract_tables(sql) == {"tb_order"}


def test_comma_separated_tables():
    sql = "select * from tb_inventory a, tb_channel b where a.channel_id = b.id"
    assert extract_tables(sql) == {"tb_inventory", "tb_channel"}


def test_schema_qualified_and_backticked_names_normalized():
    sql = "select * from `dkd`.`TB_Order` o join `tb_task` t on t.id = o.id"
    assert extract_tables(sql) == {"tb_order", "tb_task"}


def test_union_collects_both_sides():
    sql = "select id from tb_order union select id from tb_task"
    assert extract_tables(sql) == {"tb_order", "tb_task"}


def test_string_literal_is_not_treated_as_table():
    """`'from tb_hacker'` 是数据不是表名——否则任何带该文本的查询都会被误判。"""
    sql = "select * from tb_order where remark = 'from tb_hacker join tb_nope'"
    assert extract_tables(sql) == {"tb_order"}


def test_comments_are_ignored():
    sql = """
        -- from tb_hacker
        select * from tb_order /* join tb_nope */ where id = 1
    """
    assert extract_tables(sql) == {"tb_order"}


def test_no_table_is_rejected_fail_closed():
    for sql in ("select 1", "select now()", ""):
        with pytest.raises(SqlGuardError):
            extract_tables(sql)


def test_unclosed_quote_or_comment_rejected():
    for sql in (
        "select * from tb_order where remark = 'abc",
        "select * from tb_order /* 未闭合",
        "select * from tb_order where x = `abc",
    ):
        with pytest.raises(SqlGuardError):
            extract_tables(sql)


def test_declared_match_passes_and_returns_tables():
    actual = assert_declared_tables_match(
        "select * from tb_order o join tb_task t on t.id = o.id",
        ("tb_order", "tb_task"),
    )
    assert actual == {"tb_order", "tb_task"}


def test_undeclared_table_is_rejected():
    """少声明 = 可能越权读取（本模块存在的理由）。"""
    with pytest.raises(SqlGuardError, match="未声明"):
        assert_declared_tables_match(
            "select * from tb_order join tb_report r on r.id = tb_order.id",
            ("tb_order",),
        )


def test_unused_declaration_is_rejected():
    """多声明 = 工具写错了表名；放行会让白名单逐渐失真。"""
    with pytest.raises(SqlGuardError, match="未读取"):
        assert_declared_tables_match("select * from tb_order", ("tb_order", "tb_task"))


def test_normalization_applies_to_declared_side_too():
    """声明侧的大小写/反引号/库名前缀也要归一化，否则会误报不一致。"""
    assert_declared_tables_match("select * from tb_order", ("`TB_Order`", "dkd.tb_order"))


async def test_fetch_all_rejects_undeclared_table_before_db():
    """接线验证：SQL 读了未声明的表 → 在到达数据库之前就拒绝（不做任何 IO）。"""
    with pytest.raises(SqlGuardError):
        await read_tools._fetch_all(
            object(),  # 走到校验就该抛错，根本不会调用 session
            "select * from tb_order",
            {},
            tables=("tb_task",),
        )


async def test_fetch_all_rejects_wrong_declaration_before_db():
    """接线验证：声明了但 SQL 没读 → 同样拒绝（声明写错也是缺陷）。"""
    with pytest.raises(SqlGuardError):
        await read_tools._fetch_all(
            object(),
            "select * from tb_order",
            {},
            tables=("tb_order", "tb_task"),
        )
