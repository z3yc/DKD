"""智能体配置 seed 回读比对（排期 2-1 的「seed 回读比对报告」）。

## 用途

`docs/ddl/agent_config.sql` + `docs/ddl/agent_config_seed.sql` 执行后，用**只读账号**
把库里的三行内置配置与 `dkd-agent/app/agent_defaults.py` 的代码常量逐字比对：
- `system_prompt`：**逐字相等**（这是 2-7「配置=默认 ⇒ prompt 与硬编码相同」的根）；
- `params_json`：按 `normalize_params` 归一化后相等；
- `enabled / version / builtin / runtime / scene`：与 seed 口径一致；
- `agent_config_history` 每个 code 至少 1 条 v1 快照。

## 为什么用只读账号

AGENTS §7.3：本脚本**不写任何数据**，用 `dkd_agent`（只有 SELECT）即可，权限不足会
如实报 BLOCKED 而不是“想办法拿到 root”。生产/测试环境可把 `.env` 换成对应环境变量后运行。

## 退出码（AGENTS §10 脚本纪律）

- `0` 全绿；`1` 有真实不一致（FAIL）；`2` 未验到/环境拦截（BLOCKED，例如表未建、库不可达）。

用法（仓库根，Windows）：
    dkd-agent\\.venv\\Scripts\\python.exe docs/scripts/verify-agent-config-seed.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dkd-agent"))

from sqlalchemy import text  # noqa: E402 —— 必须先补 sys.path
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.agent_defaults import builtin_defaults, normalize_params  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, get_sessionmaker  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2


def _params_equal(expected: dict, actual_json: object) -> tuple[bool, str]:
    """比较 params_json（库里可能是 JSON 字符串或 dict，MySQL JSON 列由驱动决定）。"""
    if actual_json is None:
        actual = {}
    elif isinstance(actual_json, dict):
        actual = actual_json
    else:
        try:
            actual = json.loads(str(actual_json))
        except (TypeError, ValueError):
            return False, f"params_json 不是合法 JSON：{actual_json!r}"
    if not isinstance(actual, dict):
        return False, f"params_json 不是对象：{type(actual).__name__}"
    expected_norm = normalize_params(str(expected["runtime"]), expected["params_json"])
    actual_norm = normalize_params(str(expected["runtime"]), actual)
    if expected_norm != actual_norm:
        return False, f"params 不一致：expected={expected_norm} actual={actual_norm}"
    return True, ""


async def _load_rows() -> dict[str, dict]:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                """
                SELECT code, name, runtime, scene, builtin, system_prompt, tools, model,
                       temperature, max_tokens, timeout_s, max_retries, params_json,
                       enabled, version
                  FROM agent_config
                 WHERE del_flag = '0' AND code IN ('restock', 'fault', 'chat')
                """
            )
        )
        return {str(row._mapping["code"]): dict(row._mapping) for row in result}


async def _load_history_counts() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                """
                SELECT code, COUNT(*) AS cnt
                  FROM agent_config_history
                 WHERE code IN ('restock', 'fault', 'chat')
                 GROUP BY code
                """
            )
        )
        return {str(row._mapping["code"]): int(row._mapping["cnt"]) for row in result}


async def main() -> int:
    settings = get_settings()
    if not settings.db_password:
        print("BLOCKED: 未配置 DKD_AGENT_DB_PASSWORD（只读账号口令），无法回读比对")
        return EXIT_BLOCKED
    print(f"连接只读账号：{settings.db_user}@{settings.db_host}:{settings.db_port}/{settings.db_name}")

    try:
        rows = await _load_rows()
        history = await _load_history_counts()
    except SQLAlchemyError as exc:
        # 1146 = 表不存在；其余按环境拦截处理（不把“没验到”报成失败）
        msg = str(exc)
        if "1146" in msg or "doesn't exist" in msg:
            print(
                "BLOCKED: agent_config / agent_config_history 尚未建表，"
                "请先执行 docs/ddl/agent_config.sql + agent_config_seed.sql"
            )
        elif "1142" in msg or "command denied" in msg:
            print(
                "BLOCKED: 只读账号缺少 agent_config 的 SELECT 授权；"
                "请确认 create_agent_db_user.sql 的 2026-11-03 追加段已在目标库执行"
            )
        else:
            print(f"BLOCKED: 只读库访问失败（{type(exc).__name__}）：{msg.splitlines()[0][:200]}")
        return EXIT_BLOCKED
    finally:
        await dispose_engine()

    failures: list[str] = []
    for expected in builtin_defaults():
        code = str(expected["code"])
        row = rows.get(code)
        if row is None:
            failures.append(f"[{code}] 库中无该配置行（seed 未执行或 code 不一致）")
            continue
        prompt = row["system_prompt"]
        if prompt != expected["system_prompt"]:
            failures.append(
                f"[{code}] system_prompt 与代码常量不逐字一致："
                f"db_len={len(prompt or '')} code_len={len(expected['system_prompt'] or '')}"
            )
        ok, why = _params_equal(expected, row["params_json"])
        if not ok:
            failures.append(f"[{code}] {why}")
        for field in ("runtime", "scene", "builtin", "enabled", "version"):
            if int(row[field] or 0) != int(expected.get(field, 0) or 0):
                failures.append(f"[{code}] {field} 不一致：db={row[field]!r} 期望={expected.get(field)!r}")
        if history.get(code, 0) < 1:
            failures.append(f"[{code}] agent_config_history 缺少 v1 快照")

    if failures:
        print(f"FAIL: {len(failures)} 处不一致")
        for item in failures:
            print(f"  - {item}")
        return EXIT_FAIL

    print("PASS: 3 行内置配置与 agent_defaults.py 逐字一致，且均有 v1 历史快照")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 —— 脚本兜底：未知异常按 BLOCKED，不污染 PASS/FAIL
        print(f"BLOCKED: 未预期异常 {type(exc).__name__}: {str(exc)[:300]}")
        raise SystemExit(EXIT_BLOCKED) from exc
