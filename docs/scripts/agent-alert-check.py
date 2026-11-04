"""Agent 告警检查（排期 6-2）：只读巡检 + 可选 webhook 触达。

## 为什么是一个脚本而不是“再建一套监控”

本项目的监控底座是现有 `agent_log` / `agent_run` / `agent_llm_probe` / `agent_mcp_probe` /
`agent_restock_plan`。6-2 要求的是**把“该告警的条件”变成可执行、可演练的检查**，
并给出触达方式；真正的值班通道（钉钉/飞书/企微 webhook）由运维配置
`DKD_ALERT_WEBHOOK` 后由本脚本 POST。

## 检查项（阈值可调）

1. ERROR 日志在窗口内 ≥ error-threshold；
2. 最近连续失败 run ≥ failure-threshold；
3. 06:00 补货分析失败（当日 restock run failed）；
4. 默认模型档案最近一次探测 ok=0；
5. MCP server 最近探测失败（任一台 enabled 且 ok=0）；
6. 当日计划未校准率 ≥ uncalibrated-rate（分母只含 llm_calibrated 非 NULL 的计划）；
7. KB 检索回退率（近 24h）≥ kb-fallback-rate；
8. 待指派计划中等待超阈值数量 >0。

## 用法 / 退出码

    python docs/scripts/agent-alert-check.py --json --out .run/alerts.json
    $env:DKD_ALERT_WEBHOOK='https://...'; python ... --notify

退出码：0=无告警；1=有告警（需触达）；2=检查本身失败（DB 不可达/配置错误）。
`--dry-run-notify` 只打印 webhook payload，不真正 POST（演练/联调用）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pymysql

DEFAULT_THRESHOLDS = {
    "error_threshold": 5,
    "failure_threshold": 3,
    "uncalibrated_rate": 0.5,
    "kb_fallback_rate": 0.15,
    "pending_hours": 24,
}


def _conn(args: argparse.Namespace):
    return pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.db, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def collect(conn, args: argparse.Namespace) -> dict:
    window_min = args.window_min
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM agent_log WHERE create_time >= NOW() - INTERVAL %s MINUTE"
            " AND (level='ERROR' OR status='error')",
            (window_min,),
        )
        out["errorLogs"] = int((cur.fetchone() or {}).get("n") or 0)

        cur.execute(
            "SELECT status FROM agent_run ORDER BY started_at DESC LIMIT %s",
            (args.failure_threshold,),
        )
        recent = [str(r["status"]) for r in cur.fetchall()]
        out["recentRuns"] = recent
        out["consecutiveFailures"] = len(recent) if recent and all(s == "failed" for s in recent) else 0

        cur.execute(
            "SELECT COUNT(*) AS n FROM agent_run WHERE scene_code='restock' AND status='failed'"
            " AND started_at >= CURDATE() AND HOUR(started_at) BETWEEN 5 AND 7"
        )
        out["restockMorningFailures"] = int((cur.fetchone() or {}).get("n") or 0)

        cur.execute(
            "SELECT ok, profile_code, create_time FROM agent_llm_probe"
            " ORDER BY create_time DESC, id DESC LIMIT 1"
        )
        probe = cur.fetchone()
        out["llmProbe"] = {
            "ok": None if not probe else bool(probe["ok"]),
            "profile": (probe or {}).get("profile_code"),
            "at": str((probe or {}).get("create_time") or ""),
        }

        cur.execute(
            "SELECT p.ok, s.code FROM agent_mcp_probe p LEFT JOIN agent_mcp_server s ON s.id=p.server_id"
            " WHERE s.enabled=1 ORDER BY p.create_time DESC, p.id DESC LIMIT 5"
        )
        mcp = cur.fetchall()
        out["mcpProbeFailures"] = sum(1 for r in mcp if not bool(r["ok"]))

        cur.execute(
            "SELECT SUM(llm_calibrated=0) AS uncal, SUM(llm_calibrated IS NOT NULL) AS known,"
            " COUNT(*) AS total FROM agent_restock_plan WHERE plan_date=CURDATE() AND del_flag='0'"
        )
        row = cur.fetchone() or {}
        known = int(row.get("known") or 0)
        uncal = int(row.get("uncal") or 0)
        out["uncalibrated"] = {
            "uncalibrated": uncal, "known": known, "total": int(row.get("total") or 0),
            "rate": round(uncal / known, 4) if known else None,
        }

        cur.execute(
            "SELECT COUNT(*) AS n, SUM(fallback_reason IS NOT NULL) AS fb FROM agent_kb_retrieval"
            " WHERE create_time >= NOW() - INTERVAL 1 DAY"
        )
        kb = cur.fetchone() or {}
        total = int(kb.get("n") or 0)
        out["kbFallback"] = {
            "retrievals": total, "fallbacks": int(kb.get("fb") or 0),
            "rate": round(int(kb.get("fb") or 0) / total, 4) if total else None,
        }

        cur.execute(
            "SELECT COUNT(*) AS n, MIN(create_time) AS oldest FROM agent_restock_plan"
            " WHERE plan_date=CURDATE() AND status=6 AND del_flag='0'"
            "   AND create_time < NOW() - INTERVAL %s HOUR",
            (args.pending_hours,),
        )
        pending = cur.fetchone() or {}
        out["pendingOverdue"] = {"count": int(pending.get("n") or 0),
                                 "oldest": str(pending.get("oldest") or "")}
    out["generatedAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return out


def evaluate(metrics: dict, thresholds: dict) -> list[dict]:
    alerts: list[dict] = []
    if metrics["errorLogs"] >= thresholds["error_threshold"]:
        alerts.append({"level": "critical", "code": "ERROR_LOG_BURST",
                       "msg": f"近 {thresholds.get('_window', 15)} 分钟 ERROR 日志 {metrics['errorLogs']} 条"
                              f"（阈值 {thresholds['error_threshold']}）"})
    if metrics["consecutiveFailures"] >= thresholds["failure_threshold"]:
        alerts.append({"level": "critical", "code": "CONSECUTIVE_FAILURES",
                       "msg": f"最近连续 {metrics['consecutiveFailures']} 个 run 失败"
                              f"（阈值 {thresholds['failure_threshold']}）"})
    if metrics["restockMorningFailures"] > 0:
        alerts.append({"level": "critical", "code": "RESTOCK_0600_FAILED",
                       "msg": f"06:00 补货分析失败 {metrics['restockMorningFailures']} 次"})
    if metrics["llmProbe"]["ok"] is False:
        alerts.append({"level": "warning", "code": "LLM_PROBE_FAILED",
                       "msg": f"模型档案 {metrics['llmProbe']['profile']} 最近探测失败"})
    if metrics["mcpProbeFailures"] > 0:
        alerts.append({"level": "warning", "code": "MCP_PROBE_FAILED",
                       "msg": f"启用中的 MCP server 最近探测失败 {metrics['mcpProbeFailures']} 次"})
    uncal = metrics["uncalibrated"]["rate"]
    if uncal is not None and uncal >= thresholds["uncalibrated_rate"]:
        alerts.append({"level": "warning", "code": "UNCALIBRATED_RATE_HIGH",
                       "msg": f"当日计划未校准率 {uncal:.0%}（阈值 {thresholds['uncalibrated_rate']:.0%}）"})
    kb = metrics["kbFallback"]["rate"]
    if kb is not None and kb >= thresholds["kb_fallback_rate"]:
        alerts.append({"level": "warning", "code": "KB_FALLBACK_RATE_HIGH",
                       "msg": f"近 24h 知识库回退率 {kb:.0%}（目标 <{thresholds['kb_fallback_rate']:.0%}）"})
    if metrics["pendingOverdue"]["count"] > 0:
        alerts.append({"level": "warning", "code": "PENDING_OVERDUE",
                       "msg": f"待指派计划超 {thresholds['pending_hours']}h 共 {metrics['pendingOverdue']['count']} 台"})
    return alerts


def notify(webhook: str, payload: dict) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 —— 运维显式配置的 webhook
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 —— 触达失败必须报告，但脚本本身不崩
        print(f"WARN: webhook 触达失败（{type(exc).__name__}）")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-min", type=int, default=15)
    parser.add_argument("--error-threshold", type=int, default=DEFAULT_THRESHOLDS["error_threshold"])
    parser.add_argument("--failure-threshold", type=int, default=DEFAULT_THRESHOLDS["failure_threshold"])
    parser.add_argument("--uncalibrated-rate", type=float, default=DEFAULT_THRESHOLDS["uncalibrated_rate"])
    parser.add_argument("--kb-fallback-rate", type=float, default=DEFAULT_THRESHOLDS["kb_fallback_rate"])
    parser.add_argument("--pending-hours", type=int, default=DEFAULT_THRESHOLDS["pending_hours"])
    parser.add_argument("--host", default=os.environ.get("DKD_CLEANUP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DKD_CLEANUP_PORT", "3306")))
    parser.add_argument("--user", default=os.environ.get("DKD_CLEANUP_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("DKD_CLEANUP_PASSWORD", ""))
    parser.add_argument("--db", default=os.environ.get("DKD_CLEANUP_DB", "dkd"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--notify", action="store_true", help="有告警时 POST 到 DKD_ALERT_WEBHOOK")
    parser.add_argument("--dry-run-notify", action="store_true", help="只打印 webhook payload，不发送")
    args = parser.parse_args()

    thresholds = {
        "error_threshold": args.error_threshold,
        "failure_threshold": args.failure_threshold,
        "uncalibrated_rate": args.uncalibrated_rate,
        "kb_fallback_rate": args.kb_fallback_rate,
        "pending_hours": args.pending_hours,
        "_window": args.window_min,
    }
    try:
        conn = _conn(args)
        try:
            metrics = collect(conn, args)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: 告警检查失败（{type(exc).__name__}）")
        return 2

    alerts = evaluate(metrics, thresholds)
    payload = {
        "generatedAt": metrics["generatedAt"],
        "alertCount": len(alerts),
        "alerts": alerts,
        "metrics": metrics,
        "thresholds": thresholds,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"alert report written: {out}")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"alerts={len(alerts)}")
        for alert in alerts:
            print(f"  [{alert['level']}] {alert['code']}: {alert['msg']}")

    if alerts and (args.notify or args.dry_run_notify):
        webhook = os.environ.get("DKD_ALERT_WEBHOOK", "")
        if args.dry_run_notify or not webhook:
            print("DRY-RUN webhook payload:", json.dumps(payload, ensure_ascii=False)[:800])
        else:
            print("webhook delivered" if notify(webhook, payload) else "webhook failed")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
