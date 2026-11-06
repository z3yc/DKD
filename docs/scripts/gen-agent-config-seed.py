"""生成 `docs/ddl/agent_config_seed.sql`（排期 2-1）。

## 为什么用脚本生成而不是手写 seed

排期 2-7 的硬验收是「配置值 = 代码默认常量时，prompt 与硬编码**逐字相同**」；
而 2-1 要求 seed「从现有代码常量逐字导出」。手写 SQL 只要漏掉一个空格/引号，
线上行为就已经和改造前不同——这种差异在页面上看不出来，只有逐字比对能发现。

因此：**唯一事实源是 `dkd-agent/app/agent_defaults.py`**（它又直接引用
`app/prompts/*.py` 的常量），本脚本只做序列化。生成物入库后，任何人改默认值都必须
重跑本脚本；`--check` 供 CI/本地校验生成物与常量是否漂移。

用法（在仓库根执行，Windows 二选一）：
    dkd-agent\\.venv\\Scripts\\python.exe docs/scripts/gen-agent-config-seed.py --write
    dkd-agent\\.venv\\Scripts\\python.exe docs/scripts/gen-agent-config-seed.py --check

退出码：0 = 一致/已写入；1 = 漂移（--check）或参数契约自检失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dkd-agent"))

from app.agent_defaults import (  # noqa: E402 —— 必须先补 sys.path
    ALLOWED_PLACEHOLDERS,
    MODEL_WHITELIST,
    RUNTIMES,
    builtin_defaults,
    default_system_prompt,
)

OUTPUT = ROOT / "docs" / "ddl" / "agent_config_seed.sql"


def sql_str(value: object) -> str:
    """把 Python 值转成 MySQL 字符串字面量（NULL 安全、转义反斜杠与单引号）。

    为什么连反斜杠也转义：MySQL 默认把 `\\n` 解释为换行，而现有 prompt 里**真的有**
    字面的 `\\n` 字样（提示词在教模型“不要输出转义字符”）。不转义会让 DB 里存的是换行，
    与代码常量不再逐字相同——这正是本脚本要防的那类静默漂移。
    """
    if value is None:
        return "NULL"
    text = str(value)
    # 顺序不能反：先转义反斜杠，再转义单引号
    text = text.replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def snapshot_of(row: dict) -> str:
    """整行快照 JSON（字段名与 agent_config 列名一致，version 固定为 1）。"""
    snapshot = {
        "code": row["code"],
        "name": row["name"],
        "description": row["description"],
        "runtime": row["runtime"],
        "scene": row["scene"],
        "builtin": row["builtin"],
        "system_prompt": row["system_prompt"],
        "tools": row["tools"],
        "model": row["model"],
        "temperature": row["temperature"],
        "max_tokens": row["max_tokens"],
        "timeout_s": row["timeout_s"],
        "max_retries": row["max_retries"],
        "params_json": row["params_json"],
        "enabled": row["enabled"],
        "disabled_reason": None,
        "version": 1,
    }
    # ensure_ascii=False：中文 prompt 要原样入库，便于 DBA/审计直接读；
    # sort_keys=True：生成物稳定，--check 才不会因键序变化误报漂移。
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def _placeholders(template: str) -> set[str]:
    """解析 prompt 模板里的占位符集合（忽略字典/位置格式化之外的复杂语法）。"""
    return {field for _, field, _, _ in Formatter().parse(template) if field}


def assert_contract() -> None:
    """生成前自检：占位符白名单与默认 prompt 必须严格一致。

    这是“防漂移”的另一半：脚本能逐字导出，但如果 prompt 里新增了 `{xxx}` 而白名单没更新，
    前端校验会放行一个运行期渲染不了的模板（KeyError/花括号外泄），所以直接失败。
    """
    problems: list[str] = []
    for runtime in RUNTIMES:
        prompt = default_system_prompt(runtime)
        allowed = ALLOWED_PLACEHOLDERS[runtime]
        if prompt is None:
            if allowed:
                problems.append(f"{runtime}: 无默认 prompt 但声明了占位符白名单 {sorted(allowed)}")
            continue
        found = _placeholders(prompt)
        if found != set(allowed):
            problems.append(
                f"{runtime}: prompt 占位符 {sorted(found)} 与白名单 {sorted(allowed)} 不一致"
            )
    if not MODEL_WHITELIST:
        problems.append("模型白名单为空：配置里无法填任何模型")
    if problems:
        raise SystemExit("参数契约自检失败：\n  - " + "\n  - ".join(problems))


def build_sql() -> str:
    """生成完整 seed SQL（只 INSERT IGNORE，绝不覆盖人工修改过的配置）。"""
    lines: list[str] = [
        "-- =============================================================================",
        "-- ⚠️  本文件由 docs/scripts/gen-agent-config-seed.py 自动生成，请勿手改。",
        "--     修改默认值：改 dkd-agent/app/agent_defaults.py（或它引用的 prompts/*.py）后重跑：",
        "--       dkd-agent\\.venv\\Scripts\\python.exe docs/scripts/gen-agent-config-seed.py --write",
        "--     校验漂移：同脚本 --check（退出码 1 = 生成物落后于默认常量）",
        "-- =============================================================================",
        "-- 智能体配置 seed（排期 2-1）",
        "-- 执行环境：MySQL 8.0（库 dkd），需先执行 agent_config.sql；生产执行前需 DBA 评审。",
        "-- 幂等性：INSERT IGNORE —— 已存在的 code（或 code+version）不会覆盖，",
        "--         因此**重跑 seed 不会回滚运营改过的配置**。",
        "-- 回滚：DELETE FROM agent_config_history WHERE code IN ('restock','fault','chat');",
        "--       DELETE FROM agent_config WHERE code IN ('restock','fault','chat');",
        "--       （仅限未上线/未人工修改的环境；生产回滚前先备份）",
        "-- =============================================================================",
        "SET NAMES utf8mb4;",
        "",
    ]
    for row in builtin_defaults():
        row_values = {
            "code": row["code"],
            "name": row["name"],
            "description": row["description"],
            "runtime": row["runtime"],
            "scene": row["scene"],
            "builtin": row["builtin"],
            "system_prompt": row["system_prompt"],
            "tools": row["tools"],
            "model": row["model"],
            "temperature": row["temperature"],
            "max_tokens": row["max_tokens"],
            "timeout_s": row["timeout_s"],
            "max_retries": row["max_retries"],
            "params_json": json.dumps(row["params_json"], ensure_ascii=False, sort_keys=True),
            "enabled": row["enabled"],
        }
        lines.extend(
            [
                f"-- 内置智能体：{row['code']}（runtime={row['runtime']} scene={row['scene']}）",
                "INSERT IGNORE INTO `agent_config` (",
                "  `code`, `name`, `description`, `runtime`, `scene`, `builtin`,",
                "  `system_prompt`, `tools`, `model`, `temperature`, `max_tokens`,",
                "  `timeout_s`, `max_retries`, `params_json`, `enabled`, `change_note`,",
                "  `create_by`, `create_time`, `update_time`, `del_flag`",
                ") VALUES (",
                "  {code}, {name}, {description}, {runtime}, {scene}, {builtin},".format(
                    code=sql_str(row_values["code"]),
                    name=sql_str(row_values["name"]),
                    description=sql_str(row_values["description"]),
                    runtime=sql_str(row_values["runtime"]),
                    scene=row_values["scene"],
                    builtin=row_values["builtin"],
                ),
                "  {prompt}, {tools}, {model}, {temperature}, {max_tokens},".format(
                    prompt=sql_str(row_values["system_prompt"]),
                    tools=sql_str(row_values["tools"]),
                    model=sql_str(row_values["model"]),
                    temperature=sql_str(row_values["temperature"]),
                    max_tokens=sql_str(row_values["max_tokens"]),
                ),
                "  {timeout}, {retries}, CAST({params} AS JSON), {enabled},".format(
                    timeout=row_values["timeout_s"],
                    retries=row_values["max_retries"],
                    params=sql_str(row_values["params_json"]),
                    enabled=row_values["enabled"],
                ),
                "  '初始 seed：与代码常量逐字一致', 'system', NOW(), NOW(), '0'",
                ");",
                "",
                "INSERT IGNORE INTO `agent_config_history`",
                "  (`code`, `version`, `snapshot`, `change_note`, `create_by`, `create_time`)",
                "VALUES ({code}, 1, CAST({snapshot} AS JSON), '初始 seed（与代码常量逐字一致）',"
                " 'system', NOW());".format(
                    code=sql_str(row_values["code"]),
                    snapshot=sql_str(snapshot_of(row)),
                ),
                "",
            ]
        )
    lines.extend(
        [
            "-- ----------------------------------------------------------------------------",
            "-- 校验（应返回 3 行 config + 3 行 history；system_prompt 与代码常量逐字一致）",
            "-- ----------------------------------------------------------------------------",
            "-- SELECT code, version, enabled, runtime, scene, CHAR_LENGTH(system_prompt) FROM agent_config;",
            "-- SELECT code, version, create_time FROM agent_config_history ORDER BY code, version;",
            "-- SELECT code, SHA2(system_prompt, 256) FROM agent_config WHERE code IN ('restock','chat');",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/校验智能体配置 seed SQL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="写入 docs/ddl/agent_config_seed.sql")
    group.add_argument("--check", action="store_true", help="只校验生成物是否与默认常量一致")
    args = parser.parse_args()

    assert_contract()
    content = build_sql()
    if args.write:
        OUTPUT.write_text(content, encoding="utf-8", newline="\n")
        print(f"written: {OUTPUT.relative_to(ROOT)} ({len(content)} bytes)")
        return 0

    if not OUTPUT.exists():
        print(f"MISSING: {OUTPUT.relative_to(ROOT)}（先运行 --write）", file=sys.stderr)
        return 1
    current = OUTPUT.read_text(encoding="utf-8")
    if current != content:
        print(
            f"DRIFT: {OUTPUT.relative_to(ROOT)} 与默认常量不一致，请重跑 --write 并 review diff",
            file=sys.stderr,
        )
        return 1
    print(f"ok: {OUTPUT.relative_to(ROOT)} 与默认常量逐字一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
