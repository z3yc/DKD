"""只读 SQL 的表名解析与「声明 / 实际」交叉校验（P0-7）。

## 为什么需要这个模块

`read_tools._fetch_all` 的白名单校验原本是**按调用方传入的 `tables=(...)`** 做的：

```python
for table in tables:
    assert_table_allowed(table)
```

SQL 文本本身不参与校验，于是「漏传 / 写错表名」就能绕过 `assert_table_allowed`——
越权读取不会报错，只会安静地多读一张表。梳理报告 P0-7 把这列为 🔴。

## 本模块的规则（fail-closed）

1. 从 SQL 里解析真正被读的表（`FROM` / `JOIN`，含子查询、括号、别名、`db.table`、反引号）；
2. 与调用方声明的表集合做**双向**交叉校验，不相等即拒绝：
   - 少声明 = 可能越权读取；
   - 多声明 = 工具声明写错（不拒绝的话白名单会被慢慢写成摆设）；
3. 解析不出任何表、或 SQL 有未闭合的引号/注释 → 直接拒绝，**不允许"解析不出来就当通过"**。

## 已知边界（如实登记，不假装支持）

- 不支持 CTE（`WITH x AS (...) SELECT ... FROM x`）：`x` 会被当成表名 → 与声明不一致而拒绝。
  当前所有工具的 SQL 都没有 CTE；将来若要用，应先扩展本模块并补测试，而不是放宽校验。
- 不做完整 SQL 语法分析（没有引入 `sqlglot` 这类新依赖）：关键词集合是显式白名单，
  遇到未覆盖的写法会走"解析不出/不一致"的拒绝路径，而不会静默放行。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# 标识符：字母/下划线开头；`$` 是 MySQL 允许的标识符字符
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")

# 引入表名的关键词（JOIN 的各种前缀 LEFT/RIGHT/INNER/OUTER/CROSS 由主循环自然跳过）
_TABLE_INTRO = frozenset({"from", "join"})

# 表名列表的终止关键词：遇到它们说明 FROM/JOIN 后的表已经读完
_STOP = frozenset(
    {
        "where",
        "group",
        "order",
        "limit",
        "having",
        "union",
        "on",
        "using",
        "set",
        "values",
        "left",
        "right",
        "inner",
        "outer",
        "cross",
        "join",
        "select",
        "and",
        "or",
    }
)


class SqlGuardError(PermissionError):
    """SQL 表名校验失败：解析不出表、或声明与实际不一致。"""


def _tokenize(sql: str) -> list[tuple[str, str]]:
    """把 SQL 切成 (kind, value) 记号；跳过注释与字符串字面量。

    跳过字符串字面量很重要：`WHERE remark = 'from tb_hacker'` 里的 from 不是表名。
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in " \t\r\n":
            i += 1
            continue
        # -- 行注释
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            newline = sql.find("\n", i)
            i = n if newline < 0 else newline + 1
            continue
        # /* */ 块注释
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end < 0:
                raise SqlGuardError("SQL 块注释未闭合，拒绝执行")
            i = end + 2
            continue
        # '...' 字符串（'' 为转义）
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise SqlGuardError("SQL 字符串未闭合，拒绝执行")
            i = j + 1
            continue
        # `...` 反引号标识符
        if ch == "`":
            end = sql.find("`", i + 1)
            if end < 0:
                raise SqlGuardError("SQL 反引号未闭合，拒绝执行")
            tokens.append(("ident", sql[i + 1 : end]))
            i = end + 1
            continue
        if ch.isalpha() or ch == "_":
            match = _IDENT.match(sql, i)
            assert match is not None  # ch 已保证是标识符首字符
            tokens.append(("ident", match.group(0)))
            i = match.end()
            continue
        if ch.isdigit():
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] in "._"):
                j += 1
            tokens.append(("number", sql[i:j]))
            i = j
            continue
        tokens.append(("punct", ch))
        i += 1
    return tokens


def _normalize(table: str) -> str:
    """表名归一化：去空白/反引号、转小写、去掉库名前缀（`dkd`.`tb_order` → tb_order）。"""
    name = table.strip().strip("`").strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1].strip("`").strip()
    return name.lower()


def _read_qualified(tokens: list[tuple[str, str]], i: int) -> tuple[str, int]:
    """读取一个可能带库名前缀的标识符，返回 (表名, 下一个位置)。"""
    kind, value = tokens[i]
    if kind != "ident":
        raise SqlGuardError(f"FROM/JOIN 之后期望表名，实际拿到 `{value}`，拒绝执行")
    parts = [value]
    i += 1
    while i + 1 < len(tokens) and tokens[i] == ("punct", ".") and tokens[i + 1][0] == "ident":
        parts.append(tokens[i + 1][1])
        i += 2
    return _normalize(".".join(parts)), i


def _collect_table_list(tokens: list[tuple[str, str]], i: int) -> tuple[list[str], int]:
    """收集 FROM/JOIN 后面的表名列表（支持逗号分隔与括号子查询）。"""
    names: list[str] = []
    n = len(tokens)
    expect_table = True
    while i < n:
        kind, value = tokens[i]
        if kind == "punct" and value == "(":
            # 括号子查询：跳过配对括号，里面的 FROM/JOIN 由主扫描继续收集
            depth = 1
            i += 1
            while i < n and depth > 0:
                if tokens[i] == ("punct", "("):
                    depth += 1
                elif tokens[i] == ("punct", ")"):
                    depth -= 1
                i += 1
            expect_table = False
            continue
        if kind == "punct" and value == ",":
            expect_table = True
            i += 1
            continue
        if kind == "ident":
            if value.lower() in _STOP:
                break
            if expect_table:
                name, i = _read_qualified(tokens, i)
                names.append(name)
                expect_table = False
                continue
            # 表之后的别名（`t` 或 `AS t`）：跳过
            i += 1
            continue
        # 其它记号（数字、比较符等）说明表名列表结束
        break
    return names, i


def extract_tables(sql: str) -> frozenset[str]:
    """解析 SQL 中实际读取的表名集合（已归一化为小写）。

    :raises SqlGuardError: SQL 里解析不出任何表（按 fail-closed 拒绝）
    """
    if not sql or not sql.strip():
        raise SqlGuardError("SQL 为空，拒绝执行")
    tokens = _tokenize(sql)
    tables: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        kind, value = tokens[i]
        if kind == "ident" and value.lower() in _TABLE_INTRO:
            i += 1
            if i < n and tokens[i] == ("punct", "("):
                # FROM ( ... )：子查询由主循环继续扫描
                continue
            found, i = _collect_table_list(tokens, i)
            tables.extend(found)
            continue
        i += 1
    if not tables:
        raise SqlGuardError(
            "无法从 SQL 中解析出表名，拒绝执行（fail-closed，不做'解析不出就放行'）"
        )
    return frozenset(tables)


def assert_declared_tables_match(sql: str, declared: Sequence[str]) -> frozenset[str]:
    """校验「SQL 实际读的表」与「调用方声明的表」完全一致，不一致即拒绝。

    :return: 实际读取的表集合（供调用方继续做白名单校验）
    :raises SqlGuardError: 不一致或无法解析
    """
    actual = extract_tables(sql)
    declared_set = frozenset(_normalize(table) for table in declared)
    if actual != declared_set:
        undeclared = sorted(actual - declared_set)
        unused = sorted(declared_set - actual)
        problems: list[str] = []
        if undeclared:
            problems.append(f"SQL 读取了未声明的表 {undeclared}（可能是漏传表名绕过白名单）")
        if unused:
            problems.append(f"声明了但 SQL 未读取的表 {unused}（声明写错会让白名单逐渐失真）")
        raise SqlGuardError("；".join(problems))
    return actual
