#!/usr/bin/env bash
# ============================================================================
# 创建/刷新 dkd-agent 的最小权限 MySQL 账号，并**当场验证否决路径**（AGENTS §7.3 / §7.4）
#
# 为什么以 docs/ddl/create_agent_db_user.sql 为唯一来源：
#   白名单（哪些 tb_* 可读、哪些 agent_* 可写）只允许一处真相；新增表时改那一份文件即可
#   （对应排期 FIX-9 的「新增 agent_* 表要同步三处」清单）。本脚本只做两件事：
#     ① 把 <AGENT_DB_PASSWORD> 换成运行时口令；② 把 host 从 127.0.0.1/localhost 放宽/收窄。
#   为什么必须改 host：容器里 agent 的 127.0.0.1 是它自己，连不到 MySQL ——
#   原脚本的 `@'127.0.0.1'`/`@'localhost'` 在容器部署下会直接 1045。
#
# 幂等：SQL 侧是 CREATE USER IF NOT EXISTS + REVOKE 后重新 GRANT，可反复执行。
# 用法：./scripts/setup-agent-db.sh
# ============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

require_docker
compose_setup

DB="$(env_get DKD_DB_NAME dkd)"
ROOT_PW="$(env_get DKD_DB_PASSWORD)"
AGENT_USER="$(env_get DKD_AGENT_DB_USER dkd_agent)"
AGENT_PW="$(env_get DKD_AGENT_DB_PASSWORD)"
HOST_PATTERN="$(env_get DKD_AGENT_DB_HOST_PATTERN '%')"
SRC="$REPO_ROOT/docs/ddl/create_agent_db_user.sql"

[ -f "$SRC" ] || die "找不到授权源文件：$SRC"
[ "$AGENT_PW" = "CHANGE_ME" ] && die "DKD_AGENT_DB_PASSWORD 仍是模板占位值，先在 .env.deploy 里改掉"

info "授权来源：${SRC#$REPO_ROOT/}（host 模式=${HOST_PATTERN}，库=${DB}）"

# ---- 1) 生成 SQL ----
# 口令里的 & | \ 会被 sed 当特殊字符吃掉（症状：SQL 静默变形、账号口令对不上），必须先转义
ESC_PW="$(printf '%s' "$AGENT_PW" | sed -e 's/[\\&|]/\\&/g')"
SQL="$(sed \
  -e "s|<AGENT_DB_PASSWORD>|$ESC_PW|g" \
  -e "s|@'127.0.0.1'|@'$HOST_PATTERN'|g" \
  -e "s|@'localhost'|@'$HOST_PATTERN'|g" \
  -e "s|dkd\.tb_|${DB}.tb_|g" \
  -e "s|dkd\.agent_|${DB}.agent_|g" \
  "$SRC")"

# ---- 2) 执行（本机有 mysql 容器则直接执行；外部 DB 模式则生成文件交给 DBA）----
if [ -n "$(compose ps -q mysql 2>/dev/null || true)" ]; then
  wait_healthy mysql 180 || die "MySQL 未就绪，无法授权"
  # --force：业务表尚未导入时 GRANT 会报 1146，此时应继续把 agent_* 的授权做完，
  #          再在下面把「还缺哪些表」明确列出来（比整段失败更有用）。
  printf '%s\n' "$SQL" \
    | compose exec -T mysql mysql --protocol=socket -uroot -p"$ROOT_PW" --force >/dev/null 2>&1 \
    || warn "授权 SQL 返回非零（通常是部分业务表不存在，见下方缺表清单）"
  MYSQL_SIDE=1
else
  GEN="$DKDDIR/.agent-db-grants.generated.sql"
  printf '%s\n' "$SQL" > "$GEN"
  chmod 600 "$GEN"
  warn "未检测到本机 mysql 容器（外部数据库模式）→ 已生成授权 SQL：$GEN"
  warn "  ⚠️ 该文件含明文口令，仅用于交给 DBA 执行，用完请立即删除（已在 .gitignore 中排除）"
  warn "  DBA 执行：mysql -h<外部host> -uroot -p ${DB} < $GEN"
  MYSQL_SIDE=0
fi

# ---- 3) 权限矩阵验证 ----
info "权限验证（账号 $AGENT_USER，host=$HOST_PATTERN）"

run_as_agent() { compose exec -T mysql mysql -u"$AGENT_USER" -p"$AGENT_PW" -N -B -e "$1" 2>&1 || true; }

expect_ok() {
  local desc="$1" sql="$2" out
  out="$(run_as_agent "$sql")"
  if printf '%s' "$out" | grep -q "ERROR"; then
    bad "$desc → 失败：$(printf '%s' "$out" | head -1)"
  elif [ -n "$out" ]; then
    ok "$desc → 放行（$out）"
  else
    bad "$desc → 输出为空（既没报错也没结果，需人工确认）"
  fi
}

expect_denied() {
  local desc="$1" sql="$2" out
  out="$(run_as_agent "$sql")"
  if printf '%s' "$out" | grep -q "ERROR 1142"; then
    ok "$desc → 被拒（ERROR 1142）"
  elif printf '%s' "$out" | grep -q "ERROR 1146"; then
    warn "$desc → 跳过（目标表不存在，导入业务库后重跑本脚本）"
  else
    bad "$desc → 未被拒绝！输出：$(printf '%s' "$out" | head -2 | tr '\n' ' ')"
  fi
}

if [ "$MYSQL_SIDE" = "1" ]; then
  expect_ok     "账号可连接、可读自有表"        "select count(*) from $DB.agent_restock_plan"
  expect_denied "写业务表 tb_report（§7.3）"     "insert into $DB.tb_report (report_type, report_date) values ('docker-setup', curdate())"
  expect_denied "读系统表 sys_user（非白名单）"   "select count(*) from $DB.sys_user"
  expect_denied "DELETE 自有表（§7.4 软删除）"    "delete from $DB.agent_restock_plan where 1=0"
else
  warn "外部数据库模式：MySQL 侧权限矩阵请由 DBA 用上面的 SQL 执行后用同样的三条语句验证"
fi

# ---- 4) 缺表清单：dump 导入后必须重跑本脚本 ----
if [ "$MYSQL_SIDE" = "1" ]; then
  WHITELIST="$(grep -oE 'ON (dkd|'"$DB"')\.tb_[a-z_]+' "$SRC" | sed -E 's/ON (dkd|[^.]+)\.//' | sort -u || true)"
  if [ -n "$WHITELIST" ]; then
    LIST="$(printf "'%s'," $WHITELIST | sed 's/,$//')"
    EXIST="$(compose exec -T mysql mysql -uroot -p"$ROOT_PW" -N -B \
      -e "select table_name from information_schema.tables where table_schema='$DB' and table_name in ($LIST)" \
      2>/dev/null | tr -d '\r' || true)"
    MISSING=""
    for t in $WHITELIST; do
      printf '%s\n' "$EXIST" | grep -qx "$t" || MISSING="$MISSING $t"
    done
    if [ -n "$MISSING" ]; then
      warn "以下白名单业务表尚不存在：${MISSING}"
      warn "→ 导入现网 dump 后**重跑本脚本**即可补上授权（见 README §4）"
    else
      ok "白名单业务表全部存在（$(printf '%s\n' $WHITELIST | wc -l | tr -d ' ') 张）"
    fi
  fi
fi

# ---- 5) 从 agent 容器内复验：证明「部署形态下」也成立，不只是 MySQL 侧 grant 表好看 ----
if [ -n "$(compose ps -q dkd-agent 2>/dev/null || true)" ]; then
  info "从 dkd-agent 容器内部复验（用它自己的 DSN 与账号）"
  compose exec -T dkd-agent python - <<'PY' || warn "容器内复验未通过（核对 DKD_AGENT_DB_* 与账号 host 模式）"
import asyncio

from sqlalchemy import text

from app.db import dispose_engine, get_sessionmaker

CASES = [
    ("select 1", "只读连接", True),
    ("insert into tb_report (report_type, report_date) values ('x', curdate())", "写业务表", False),
    ("select count(*) from sys_user", "读非白名单表", False),
    ("delete from agent_restock_plan", "DELETE 自有表", False),
]


async def main() -> None:
    async with get_sessionmaker()() as session:
        for sql, label, should_pass in CASES:
            try:
                await session.execute(text(sql))
                await session.commit()
                print(f"  {'OK' if should_pass else '!!'} {label}: 执行成功")
            except Exception as exc:  # 这里就是要展示各类拒绝原因
                print(f"  {'!!' if should_pass else 'OK'} {label} 被拒: {type(exc).__name__}: {str(exc).splitlines()[0][:90]}")
    await dispose_engine()


asyncio.run(main())
PY
else
  warn "dkd-agent 未运行，跳过容器内复验（启动后再跑一次本脚本即可）"
fi

summary
