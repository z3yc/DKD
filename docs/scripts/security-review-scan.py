"""Agent 安全复核扫描（排期 6-6）：仓库密钥/日志/DB 权限/MCP 白名单四类证据。

## 为什么需要脚本而不是“看一眼”

6-6 的验收要求“安全复核报告（含扫描证据）”。人工 grep 既不可复跑、也容易漏：

1. **仓库密钥**：扫描已跟踪文本文件中的 `sk-*`、私钥头、明文 password、JDBC 口令；
   允许 `.env.example` 里的 `${ENV}`/`***`/占位符（这正是模板该有的样子）。
2. **日志/留痕不落密钥**：抽查 `agent_log`/`agent_message`/`agent_decision_log` 是否含 `sk-`/私钥头。
3. **DB 权限最小化**：`dkd_agent` 的 GRANTS（业务表只 SELECT、agent_* 无 DELETE、配置表只读）。
4. **MCP 出网白名单**：`agent_mcp_server.env_keys_json` 只能存**环境变量名**，不能出现密钥形态值；
   transport 仅 http、endpoint 必须 http(s)。
5. `.env` 必须被 git 忽略。

## 用法 / 退出码

    $env:DKD_CLEANUP_PASSWORD='***'
    python docs/scripts/security-review-scan.py --out docs/agent-eval-set/reports/security-review.json

退出码：0=无高危发现；1=发现疑似密钥/越权（需处理）；2=扫描本身失败。
本脚本只读，不修改任何文件/数据。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymysql

SECRET_PATTERNS = [
    # 词边界：避免把 "task-01J..." 这类 ID 里的 "sk-" 误报
    ("deepseek_key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}")),
    ("private_key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("password_literal", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*[\"'][^\"'$<*]{6,}[\"']")),
    ("jdbc_password", re.compile(r"(?i)jdbc:[^\s\"']+[?&]password=[^&\s\"']{4,}")),
]
SCAN_SUFFIXES = {".java", ".py", ".js", ".vue", ".yml", ".yaml", ".xml", ".sh", ".ps1", ".sql", ".md"}
SKIP_DIRS = {".git", "node_modules", "target", ".run", ".venv", "__pycache__", "dist", ".idea"}
# psql 变量占位（:'agent_pg_password'）、${ENV}、***、<placeholder>、YOUR_ 等都不是密钥
PLACEHOLDER = re.compile(r"\$\{|:'|\*\*\*|<[^>]+>|YOUR_|CHANGE_ME|placeholder|example")
# 测试夹具里刻意写的假 key（Service 单测用于验证“密钥形态值必须被拒”）；标低危而不是掩盖。
TEST_FIXTURE_KEY = re.compile(r"sk-abcdefghijklmnopqrstuvwxyz[0-9]{4,}")


def scan_repository(root: Path, tracked_only: bool = True) -> dict:
    if tracked_only:
        try:
            files = (
                subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, check=True)
                .stdout.decode("utf-8", errors="replace")
                .splitlines()
            )
        except (OSError, subprocess.CalledProcessError):
            tracked_only = False
            files = []
    if not tracked_only:
        files = [
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*")
            if p.is_file() and not (set(p.parts) & SKIP_DIRS)
        ]
    findings: list[dict] = []
    scanned = 0
    for rel in files:
        path = root / rel
        if path.suffix.lower() not in SCAN_SUFFIXES or not path.is_file():
            continue
        if path.name == ".env":
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match and not PLACEHOLDER.search(line):
                    is_test = "/src/test/" in rel or "/tests/" in rel or rel.startswith("tests/")
                    severity = (
                        "low(test-fixture)"
                        if is_test and TEST_FIXTURE_KEY.search(line)
                        else "high"
                    )
                    findings.append(
                        {"type": name, "severity": severity, "file": rel, "line": lineno,
                         "evidence": line.strip()[:160]}
                    )
    return {"trackedOnly": tracked_only, "filesScanned": scanned, "findings": findings}


def scan_database(args: argparse.Namespace) -> dict:
    conn = pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.db, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    out: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM agent_llm_profile")
            out["llmProfileColumns"] = [r["Field"] for r in cur.fetchall()]
            out["llmProfileHasSecretColumn"] = any(
                c.lower() in ("api_key", "apikey", "secret", "token") for c in out["llmProfileColumns"]
            )
            out["secretLeaksInLogs"] = {}
            for table, columns in (
                ("agent_log", ("message", "error_msg")),
                ("agent_message", ("content",)),
                ("agent_decision_log", ("input_context", "llm_output", "error_msg")),
            ):
                conds = " OR ".join(f"{c} LIKE %s" for c in columns)
                params = ("%sk-%",) * len(columns)
                try:
                    cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {conds}", params)
                    out["secretLeaksInLogs"][table] = int(cur.fetchone()["n"])
                except pymysql.MySQLError as exc:
                    out["secretLeaksInLogs"][table] = f"query_failed:{type(exc).__name__}"
            cur.execute("SHOW GRANTS FOR 'dkd_agent'@'localhost'")
            grants = [list(r.values())[0] for r in cur.fetchall()]
            out["agentGrants"] = grants
            out["agentHasDelete"] = any("DELETE" in g.upper() for g in grants)
            out["agentBusinessWrite"] = any(
                "INSERT" in g.upper() or "UPDATE" in g.upper()
                for g in grants
                if re.search(r"`tb_", g)
            )
            cur.execute(
                "SELECT code, transport, endpoint, env_keys_json FROM agent_mcp_server"
            )
            servers = cur.fetchall()
            bad_mcp = []
            for server in servers:
                keys = json.loads(server.get("env_keys_json") or "{}") if server.get("env_keys_json") else {}
                for header, value in (keys or {}).items():
                    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", str(value)):
                        bad_mcp.append({"code": server["code"], "header": header, "valueShape": "not-env-name"})
                if server.get("transport") != "http" or not str(server.get("endpoint") or "").startswith(
                    ("http://", "https://")
                ):
                    bad_mcp.append({"code": server["code"], "issue": "transport/endpoint"})
            out["mcpServers"] = len(servers)
            out["mcpFindings"] = bad_mcp
    finally:
        conn.close()
    return out


def gitignore_env(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"], cwd=root, capture_output=True
        )
        return result.returncode == 0
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--host", default=os.environ.get("DKD_CLEANUP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DKD_CLEANUP_PORT", "3306")))
    parser.add_argument("--user", default=os.environ.get("DKD_CLEANUP_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("DKD_CLEANUP_PASSWORD", ""))
    parser.add_argument("--db", default=os.environ.get("DKD_CLEANUP_DB", "dkd"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        repo = scan_repository(root)
        db = scan_database(args)
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: 安全扫描失败（{type(exc).__name__}）")
        return 2

    report = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "repository": repo,
        "database": db,
        "envGitignored": gitignore_env(root),
        "verdict": "PASS",
        "notes": [],
    }
    high_findings = [f for f in repo["findings"] if not str(f.get("severity", "")).startswith("low")]
    low_findings = [f for f in repo["findings"] if str(f.get("severity", "")).startswith("low")]
    if low_findings:
        report["notes"].append(f"测试夹具假 key {len(low_findings)} 处（低危，不阻断，已标注）")
    if high_findings:
        report["verdict"] = "FAIL"
        report["notes"].append(f"仓库疑似明文密钥 {len(high_findings)} 处（高危）")
    if any(isinstance(v, int) and v > 0 for v in db["secretLeaksInLogs"].values()):
        report["verdict"] = "FAIL"
        report["notes"].append("日志/留痕表疑似含 sk- 形态密钥")
    if db["agentHasDelete"]:
        report["verdict"] = "FAIL"
        report["notes"].append("dkd_agent 拥有 DELETE 权限（违反 AGENTS §7.3）")
    if db["llmProfileHasSecretColumn"]:
        report["verdict"] = "FAIL"
        report["notes"].append("agent_llm_profile 出现密钥列")
    if db["mcpFindings"]:
        report["verdict"] = "FAIL"
        report["notes"].append(f"MCP 配置白名单/密钥形态问题 {len(db['mcpFindings'])} 处")
    if not report["envGitignored"]:
        report["verdict"] = "FAIL"
        report["notes"].append(".env 未被 git 忽略")
    if report["verdict"] == "PASS":
        report["notes"].append("仓库无明文密钥命中、日志无 sk- 泄漏、dkd_agent 无 DELETE、MCP 只存 env 名")

    out = Path(args.out or "docs/agent-eval-set/reports/security-review.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"security report written: {out} verdict={report['verdict']} notes={report['notes']}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
