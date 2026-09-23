#!/usr/bin/env bash
# ============================================================================
# 部署脚本公共库（被 deploy.sh / setup-agent-db.sh / healthcheck.sh / backup.sh source）
#
# 为什么不直接 `source .env.deploy`：
#   ① .env.deploy 里有带空格的人类可读值（如 `DKD_ADMIN_JAVA_OPTS=-Xms512m -Xmx1024m`），
#      source 会把 `-Xmx1024m` 当命令执行一遍；
#   ② 逐行解析时顺手 `tr -d '\r'`，避免 Windows 写回的 CRLF 给口令多带一个不可见字符
#      （症状是「密码明明对，MySQL 却回 1045」）。
# ============================================================================

DKDDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DKDDIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$DKDDIR/.env.deploy}"

_pass_count=0
_fail_count=0

die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
info() { printf '\033[36m» %s\033[0m\n' "$*"; }
ok()   { _pass_count=$((_pass_count + 1)); printf '\033[32m  PASS  %s\033[0m\n' "$*"; }
bad()  { _fail_count=$((_fail_count + 1)); printf '\033[31m  FAIL  %s\033[0m\n' "$*"; }
summary() {
  printf '\n== 小结：PASS=%s FAIL=%s ==\n' "$_pass_count" "$_fail_count"
  [ "$_fail_count" -eq 0 ]
}

# 读 .env.deploy 的某个键（缺省值可选；缺失且无缺省则直接终止）
env_get() {
  local key="$1" def="${2-}" line val
  if [ ! -f "$ENV_FILE" ]; then
    [ -n "$def" ] && { printf '%s' "$def"; return; }
    die "找不到 $ENV_FILE（先执行：cp $DKDDIR/.env.deploy.example $ENV_FILE && chmod 600 $ENV_FILE）"
  fi
  line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 | tr -d '\r' || true)"
  if [ -z "$line" ]; then
    [ -n "$def" ] && { printf '%s' "$def"; return; }
    die ".env.deploy 缺少必填变量：$key"
  fi
  val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  printf '%s' "$val"
}

# 组装 compose 参数：项目名固定为 dkd（两个 compose 文件都写了 name: dkd，保证容器/卷同命名空间）
compose_setup() {
  DKD_COMPOSE=()
  if [ "${WITH_INFRA:-auto}" = "1" ] || { [ "${WITH_INFRA:-auto}" = "auto" ] && [ "$(env_get DKD_DB_HOST mysql)" = "mysql" ]; }; then
    DKD_COMPOSE+=(-f "$DKDDIR/docker-compose.infra.yml")
  fi
  DKD_COMPOSE+=(-f "$DKDDIR/docker-compose.yml")
}
compose() { docker compose --project-directory "$DKDDIR" --env-file "$ENV_FILE" "${DKD_COMPOSE[@]}" "$@"; }

require_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker（服务器需 Docker 24+ 与 compose v2）"
  docker compose version >/dev/null 2>&1 || die "未找到 docker compose v2 插件"
  docker info >/dev/null 2>&1 || die "Docker 守护进程不可用（docker info 失败）"
}

# 等待某个服务 healthy（compose healthcheck）；$1=服务名 $2=超时秒数
wait_healthy() {
  local svc="$1" timeout="${2:-180}" waited=0 cid state
  info "等待 $svc healthy（最多 ${timeout}s）"
  while [ "$waited" -lt "$timeout" ]; do
    cid="$(compose ps -q "$svc" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
      [ "$state" = "healthy" ] && { ok "$svc healthy（${waited}s）"; return 0; }
      [ "$state" = "unhealthy" ] && bad "$svc 处于 unhealthy，请查日志：docker compose logs $svc"
    fi
    sleep 5; waited=$((waited + 5))
  done
  bad "$svc 在 ${timeout}s 内未 healthy"
  return 1
}
