"""成本与延迟分析报告（排期 5-5）。

## 用途

按「场景 / 用户 / 日」统计 token 成本与运行耗时，为调 `max_tokens`、缓存策略提供数据；
同时输出 429 频次（若日志表有 code=429）作为限额设置依据。只读，不写库。

## 口径（与指标字典一致）

- token 成本：`agent_message.tokens_in + tokens_out`（唯一计量源，见 docs/agent-metrics-dictionary.md）；
- 场景维度：`agent_decision_log.scene`（会话消息本身不带 scene）；
- 延迟：`agent_run.started_at → finished_at` 的毫秒差（仅已结束 run；运行中不计）；
- 429：`agent_log` 中 message/code 含 429 或 quota 的行（只作趋势，不精确到用户）。

## 用法

    $env:DKD_CLEANUP_PASSWORD='***'
    python docs/scripts/agent-cost-report.py --days 7 --out docs/agent-eval-set/reports/cost-7d.json

**注意**：当前本机数据量极小（历史测试消息），报告只能验证口径；“单次成本下降 ≥20%”
需要真实流量基线（灰度前后对比），本脚本输出 `baselineAvailable=false`，不伪造降幅。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymysql


def _connect(args: argparse.Namespace) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.db, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def build_report(conn, days: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout,"
            " COUNT(*) AS calls, COALESCE(AVG(latency_ms),0) AS avg_latency"
            " FROM agent_message WHERE create_time >= NOW() - INTERVAL %s DAY",
            (days,),
        )
        overall = cur.fetchone() or {}
        cur.execute(
            "SELECT DATE(create_time) AS d, COUNT(*) AS calls,"
            " COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout"
            " FROM agent_message WHERE create_time >= NOW() - INTERVAL %s DAY"
            " GROUP BY DATE(create_time) ORDER BY d",
            (days,),
        )
        daily = cur.fetchall()
        cur.execute(
            "SELECT scene_code AS scene, COUNT(*) AS runs,"
            " COALESCE(AVG(TIMESTAMPDIFF(MICROSECOND, started_at, finished_at))/1000,0) AS avg_ms"
            " FROM agent_run WHERE started_at >= NOW() - INTERVAL %s DAY"
            "   AND finished_at IS NOT NULL GROUP BY scene_code ORDER BY scene_code",
            (days,),
        )
        by_scene_runs = cur.fetchall()
        cur.execute(
            "SELECT COALESCE(status, level) AS type, COUNT(*) AS n FROM agent_log"
            " WHERE create_time >= NOW() - INTERVAL %s DAY"
            "   AND (message LIKE %s OR message LIKE %s) GROUP BY COALESCE(status, level)",
            (days, "%429%", "%quota%"),
        )
        quota = cur.fetchall()

    calls = int(overall.get("calls") or 0)
    tin, tout = int(overall.get("tin") or 0), int(overall.get("tout") or 0)
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "days": days,
        "baselineAvailable": False,
        "note": "无灰度前后真实流量基线；不得据此声称成本下降 ≥20%（5-5 目标 🧑）",
        "overall": {
            "calls": calls,
            "tokensIn": tin,
            "tokensOut": tout,
            "tokensTotal": tin + tout,
            "avgTokensPerCall": round((tin + tout) / calls, 1) if calls else 0,
            "avgLatencyMs": round(float(overall.get("avg_latency") or 0), 1),
        },
        "daily": [
            {
                "date": str(row["d"]),
                "calls": int(row["calls"]),
                "tokensTotal": int(row["tin"]) + int(row["tout"]),
            }
            for row in daily
        ],
        "bySceneRuns": [
            {"scene": row["scene"], "runs": int(row["runs"]),
             "avgLatencyMs": round(float(row["avg_ms"] or 0), 1)}
            for row in by_scene_runs
        ],
        "quotaHits": [{"type": row["type"], "n": int(row["n"])} for row in quota],
        "recommendations": [
            "对短问答场景设置更低 max_tokens（当前按模型默认）；成本下降需灰度前后对比验证",
            "对重复问题启用检索缓存前，先看 bySceneRuns 的调用分布，避免缓存住错误答案",
            "429 自助提示已包含‘已用/限额/重置时间’（5-5）；限额调整前先看真实调用量",
        ],
    }


def to_markdown(report: dict) -> str:
    o = report["overall"]
    lines = [
        "# Agent 成本与延迟报告",
        "",
        f"- 窗口：近 {report['days']} 天；生成时间：{report['generatedAt']}",
        f"- 调用数：{o['calls']}；token：{o['tokensTotal']}（in {o['tokensIn']} / out {o['tokensOut']}）",
        f"- 单次平均 token：{o['avgTokensPerCall']}；平均延迟：{o['avgLatencyMs']} ms",
        f"- 基线可用：**否**（{report['note']}）",
        "",
        "## 按日",
        "| 日期 | 调用 | token |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {d['date']} | {d['calls']} | {d['tokensTotal']} |" for d in report["daily"]]
    lines += ["", "## 429/限额命中", "| 类型 | 次数 |", "| --- | --- |"]
    lines += [f"| {q['type']} | {q['n']} |" for q in report["quotaHits"]] or ["| — | 0 |"]
    lines += ["", "## 建议", *[f"- {r}" for r in report["recommendations"]]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--host", default=os.environ.get("DKD_CLEANUP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DKD_CLEANUP_PORT", "3306")))
    parser.add_argument("--user", default=os.environ.get("DKD_CLEANUP_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("DKD_CLEANUP_PASSWORD", ""))
    parser.add_argument("--db", default=os.environ.get("DKD_CLEANUP_DB", "dkd"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    conn = _connect(args)
    try:
        report = build_report(conn, args.days)
    finally:
        conn.close()
    out = Path(args.out or "docs/agent-eval-set/reports/cost-report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
    print(f"cost report written: {out} calls={report['overall']['calls']} "
          f"tokens={report['overall']['tokensTotal']} baselineAvailable=False")
    return 0


if __name__ == "__main__":
    sys.exit(main())
