#!/usr/bin/env bash
# ============================================================================
# 备份：业务库（含 agent_* 自有表）+ 智能体会话 checkpoint
#
# 为什么 checkpoint 必须一起备份（0-10 的运维要求）：
#   会话状态存在 SQLite 文件里（本机 Redis 3.2 不支持 RedisJSON/Streams，见排期 FIX-14），
#   文件丢了 = 所有“停在待确认”的补货计划与多轮对话断掉，且**无法从 MySQL 重建**。
#   因此这里用 Python 的 sqlite3 在线备份 API（`.backup()`），而不是 `cp` ——
#   直接 cp 正在写入的库可能拿到撕裂的快照（表现为恢复后库损坏）。
#
# 用法：./scripts/backup.sh [输出目录]     默认 deploy/docker/backups/
#   KEEP_DAYS=30 ./scripts/backup.sh        改保留天数（默认 14）
# 建议加 crontab：  0 3 * * *  cd /opt/dkd/deploy/docker && ./scripts/backup.sh >> backups/cron.log 2>&1
# ============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

require_docker
compose_setup

DB="$(env_get DKD_DB_NAME dkd)"
ROOT_PW="$(env_get DKD_DB_PASSWORD)"
OUT="${1:-$DKDDIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT"
info "备份目录：$OUT（保留 ${KEEP_DAYS} 天）"

# ---- 1) MySQL ----
# --single-transaction：InnoDB 一致性快照，不锁表（生产库不能因为备份阻塞业务）
# --databases：带上 CREATE DATABASE/USE，恢复时一条命令即可
info "① mysqldump $DB"
compose exec -T mysql mysqldump -uroot -p"$ROOT_PW" \
  --single-transaction --routines --triggers --databases "$DB" \
  2>"$OUT/.mysqldump.err" | gzip > "$OUT/dkd-$STAMP.sql.gz"
if [ -s "$OUT/dkd-$STAMP.sql.gz" ]; then
  ok "已写出 dkd-$STAMP.sql.gz（$(du -h "$OUT/dkd-$STAMP.sql.gz" | cut -f1)）"
else
  bad "dump 为空，请查看 $OUT/.mysqldump.err"
fi
rm -f "$OUT/.mysqldump.err"

# ---- 2) agent checkpoint（SQLite 在线备份）----
if [ -n "$(compose ps -q dkd-agent 2>/dev/null || true)" ]; then
  info "② 智能体 SQLite checkpoint（在线备份 API）"
  compose exec -T dkd-agent python - <<'PY' || warn "checkpoint 备份跳过（文件可能尚不存在）"
import os
import sqlite3

src = os.environ.get("DKD_AGENT_SQLITE_PATH", "/app/var/checkpoints.db")
if not os.path.exists(src):
    raise SystemExit("checkpoint 文件不存在，首次会话前属正常")
dst = sqlite3.connect("/tmp/checkpoints-backup.db")
with sqlite3.connect(src) as s:
    s.backup(dst)          # 官方在线备份：允许并发写入，不会拿到撕裂快照
dst.close()
print("checkpoint 备份完成")
PY
  compose cp "dkd-agent:/tmp/checkpoints-backup.db" "$OUT/checkpoints-$STAMP.db" >/dev/null 2>&1 \
    && ok "已写出 checkpoints-$STAMP.db" || warn "checkpoint 拷贝失败"
  compose exec -T dkd-agent rm -f /tmp/checkpoints-backup.db >/dev/null 2>&1 || true
else
  warn "dkd-agent 未运行，跳过 checkpoint 备份"
fi

# ---- 3) 轮转 ----
info "③ 清理 ${KEEP_DAYS} 天前的备份"
find "$OUT" -maxdepth 1 -type f \( -name 'dkd-*.sql.gz' -o -name 'checkpoints-*.db' \) -mtime "+$KEEP_DAYS" -print -delete || true

echo
echo "== 恢复方法（务必在演练环境先验一次）=="
echo "  库：   gunzip -c dkd-$STAMP.sql.gz | docker compose -f docker-compose.infra.yml -f docker-compose.yml exec -T mysql mysql -uroot -p"
echo "  会话：docker compose cp checkpoints-$STAMP.db dkd-agent:/app/var/checkpoints.db && docker compose restart dkd-agent"
echo "  ⚠️ 备份请落到异地/对象存储：本机磁盘与业务库同时损坏时本地备份没有意义。"
