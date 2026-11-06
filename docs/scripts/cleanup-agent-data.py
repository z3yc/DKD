"""智能体数据保留清理（排期 4-4）：agent_log 默认 30 天、会话/消息 90 天、检索留痕 90 天。

## 为什么需要独立脚本而不是让 Python 服务自己删

`dkd_agent` 账号**没有 DELETE 权限**（AGENTS §7.3/§7.4）：保留期清理由运维/DB 流程执行，
且必须可审计、可 dry-run。本脚本默认 **dry-run（只统计不删）**，显式 `--execute --confirm`
才真正删除；删除按批（1000 行/批）避免长事务锁表。

## 用法（本机示例，密码走环境变量，不写进命令历史）

    set DKD_CLEANUP_PASSWORD=***            # PowerShell: $env:DKD_CLEANUP_PASSWORD='***'
    python docs/scripts/cleanup-agent-data.py                     # dry-run 报告
    python docs/scripts/cleanup-agent-data.py --execute --confirm # 真删（需备份/低峰）

可用环境变量：DKD_CLEANUP_HOST/USER/PASSWORD/DB；默认 127.0.0.1/root/(空)/dkd。
生产建议使用具备 DELETE 的运维账号，并先跑 dry-run 把数字发给负责人确认。

## 向量库重建（3-6）

知识库增量重建不双写由 `kb_store.ingest_document` 保证（同 content_hash → skipped；
变更多余切片 enabled=0，无 DELETE）；语料更新后重新调用入库接口即可。本脚本不重建向量，
dry-run 会顺带报告 `agent_kb_document/chunk` 的启用/停用规模，供 5-3 评测前核对。
"""

from __future__ import annotations

import argparse
import os
import sys

import pymysql

BATCH = 1000


def _dsn(args: argparse.Namespace) -> dict:
    return {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "database": args.db,
        "charset": "utf8mb4",
        "autocommit": True,
    }


def _count(conn, sql: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def _delete_batched(conn, sql: str, params: tuple = ()) -> int:
    """按批删除（LIMIT 在 SQL 里），返回实际删除行数。"""
    total = 0
    while True:
        with conn.cursor() as cur:
            affected = cur.execute(sql, params)
        total += affected
        if affected < BATCH:
            return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("DKD_CLEANUP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DKD_CLEANUP_PORT", "3306")))
    parser.add_argument("--user", default=os.environ.get("DKD_CLEANUP_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("DKD_CLEANUP_PASSWORD", ""))
    parser.add_argument("--db", default=os.environ.get("DKD_CLEANUP_DB", "dkd"))
    parser.add_argument("--log-days", type=int, default=30)
    parser.add_argument("--conversation-days", type=int, default=90)
    parser.add_argument("--retrieval-days", type=int, default=90)
    parser.add_argument("--execute", action="store_true", help="真正执行删除（默认 dry-run）")
    parser.add_argument("--confirm", action="store_true", help="与 --execute 同用，确认已备份/低峰")
    args = parser.parse_args()

    if args.execute and not args.confirm:
        print("拒绝执行：--execute 必须同时带 --confirm（先 dry-run 并备份）")
        return 2
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"[{mode}] db={args.user}@{args.host}:{args.port}/{args.db}")

    conn = pymysql.connect(**_dsn(args))
    try:
        report: dict[str, int] = {}
        report["agent_log"] = _count(
            conn,
            "SELECT COUNT(1) FROM agent_log WHERE create_time < NOW() - INTERVAL %s DAY",
            (args.log_days,),
        )
        report["agent_conversation"] = _count(
            conn,
            "SELECT COUNT(1) FROM agent_conversation "
            "WHERE update_time < NOW() - INTERVAL %s DAY OR create_time < NOW() - INTERVAL %s DAY",
            (args.conversation_days, args.conversation_days),
        )
        report["agent_message(孤儿/随会话)"] = _count(
            conn,
            "SELECT COUNT(1) FROM agent_message m WHERE m.conversation_id IN ("
            " SELECT conversation_id FROM agent_conversation"
            " WHERE update_time < NOW() - INTERVAL %s DAY OR create_time < NOW() - INTERVAL %s DAY)",
            (args.conversation_days, args.conversation_days),
        )
        # 无会话头的孤儿消息（历史 B1/B2 期间只有 assistant 消息、agent_conversation 尚未写入）
        # 也必须按保留期清理，否则“会话保留 90 天”只清了有头的部分。
        report["agent_message(孤儿超期)"] = _count(
            conn,
            "SELECT COUNT(1) FROM agent_message m LEFT JOIN agent_conversation c"
            " ON c.conversation_id = m.conversation_id"
            " WHERE c.id IS NULL AND m.create_time < NOW() - INTERVAL %s DAY",
            (args.conversation_days,),
        )
        report["agent_kb_retrieval"] = _count(
            conn,
            "SELECT COUNT(1) FROM agent_kb_retrieval WHERE create_time < NOW() - INTERVAL %s DAY",
            (args.retrieval_days,),
        )
        report["kb_documents_enabled"] = _count(
            conn, "SELECT COUNT(1) FROM agent_kb_document WHERE enabled=1 AND del_flag='0'"
        )
        report["kb_chunks_enabled"] = _count(
            conn, "SELECT COUNT(1) FROM agent_kb_chunk WHERE enabled=1"
        )
        for name, num in report.items():
            print(f"  {name}: {num}")

        if not args.execute:
            print("dry-run 结束：未删除任何行。确认数字后加 --execute --confirm 执行。")
            return 0

        deleted: dict[str, int] = {}
        deleted["agent_message"] = _delete_batched(
            conn,
            "DELETE FROM agent_message WHERE conversation_id IN ("
            " SELECT conversation_id FROM ("
            "  SELECT conversation_id FROM agent_conversation"
            "  WHERE update_time < NOW() - INTERVAL %s DAY OR create_time < NOW() - INTERVAL %s DAY"
            " ) tmp) LIMIT %s",
            (args.conversation_days, args.conversation_days, BATCH),
        )
        deleted["agent_conversation"] = _delete_batched(
            conn,
            "DELETE FROM agent_conversation "
            "WHERE update_time < NOW() - INTERVAL %s DAY OR create_time < NOW() - INTERVAL %s DAY LIMIT %s",
            (args.conversation_days, args.conversation_days, BATCH),
        )
        deleted["agent_message_orphan"] = _delete_batched(
            conn,
            "DELETE m FROM agent_message m LEFT JOIN agent_conversation c"
            " ON c.conversation_id = m.conversation_id"
            " WHERE c.id IS NULL AND m.create_time < NOW() - INTERVAL %s DAY LIMIT %s",
            (args.conversation_days, BATCH),
        )
        deleted["agent_kb_retrieval"] = _delete_batched(
            conn,
            "DELETE FROM agent_kb_retrieval WHERE create_time < NOW() - INTERVAL %s DAY LIMIT %s",
            (args.retrieval_days, BATCH),
        )
        deleted["agent_log"] = _delete_batched(
            conn,
            "DELETE FROM agent_log WHERE create_time < NOW() - INTERVAL %s DAY LIMIT %s",
            (args.log_days, BATCH),
        )
        for name, num in deleted.items():
            print(f"  deleted {name}: {num}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
