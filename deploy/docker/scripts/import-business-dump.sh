#!/usr/bin/env bash
# ============================================================================
# 导入现网业务库 dump
#
# 为什么必须人工导入（AGENTS §1 约束 5）：仓库里只有 RuoYi 系统表、quartz、tb_report 与
# agent_* 的 DDL；**业务表（tb_inventory/tb_order/tb_task/tb_channel/tb_emp ...）DDL 未入库**，
# 所以自动初始化只能建出“半个库”。管理后台与智能体的读工具都依赖这些业务表。
#
# 用法：./scripts/import-business-dump.sh /path/to/dkd_prod_20260923.sql[.gz]
# 之后：./scripts/install-agent-db.sh —— 业务表到位后重跑，补上白名单授权
# ============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

FILE="${1:-}"
[ -n "$FILE" ] || die "用法：./scripts/import-business-dump.sh /path/to/dkd.sql[.gz]"
[ -f "$FILE" ] || die "文件不存在：$FILE"

require_docker
compose_setup

DB="$(env_get DKD_DB_NAME dkd)"
ROOT_PW="$(env_get DKD_DB_PASSWORD)"
wait_healthy mysql 180 || die "MySQL 未就绪"

# 按**魔法字节**判断是否 gzip，而不是看后缀：运维手工改名（.sql 实际是 gz）很常见，
# 只看后缀会把 gz 当纯文本喂给 mysql，报一堆语法错却不说是压缩问题。
is_gzip() { [ "$(head -c 2 "$1" | od -An -tx1 | tr -d ' \n')" = "1f8b" ]; }
read_stream() { if is_gzip "$1"; then gunzip -c "$1"; else cat "$1"; fi; }

if read_stream "$FILE" 2>/dev/null | head -60 | grep -qiE '^[[:space:]]*(create database|use )'; then
  warn "dump 内含 CREATE DATABASE/USE 语句 → 请确认目标库就是 $DB（否则会静默导到别的库）"
fi

info "导入 $FILE → $DB（大库可能数分钟，期间后台写入会等待）"
read_stream "$FILE" | compose exec -T mysql mysql -uroot -p"$ROOT_PW" "$DB"

count="$(compose exec -T mysql mysql -uroot -p"$ROOT_PW" -N -B \
  -e "select count(*) from information_schema.tables where table_schema='$DB' and table_name like 'tb\_%'" 2>/dev/null | tr -d '\r')"
ok "导入完成：$DB 内 tb_* 表共 ${count:-?} 张"
warn "下一步：./scripts/setup-agent-db.sh（给 dkd_agent 补上业务表白名单只读授权）"
