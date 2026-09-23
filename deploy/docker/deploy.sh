#!/usr/bin/env bash
# ============================================================================
# 一键部署 / 升级
#
# 用法：
#   ./deploy.sh                      # 自动判断：DKD_DB_HOST=mysql 时连 infra 一起起
#   ./deploy.sh --no-infra           # 用外部 MySQL/Redis（只起应用栈）
#   ./deploy.sh --mobile             # 额外启动运维移动端 dkd-app（--profile mobile）
#   ./deploy.sh --skip-build         # 只重启（不重新构建镜像）
#   ./deploy.sh --tag 2026-09-23-1   # 指定镜像 tag（回滚：build 前改回上一版 tag）
#
# 顺序有意为之：构建 → 起基础设施 → 等 healthy → 起应用 → 等 healthy → 智能体数据库授权 → 健康检查
#   “授权放在起服务之后”是因为 agent_* 表由 MySQL 首次初始化创建，授权必须晚于建表。
# ============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

WITH_INFRA="${WITH_INFRA:-auto}"
DO_BUILD=1
MOBILE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-infra) WITH_INFRA=1 ;;
    --no-infra)   WITH_INFRA=0 ;;
    --mobile)     MOBILE=1 ;;
    --skip-build) DO_BUILD=0 ;;
    --tag)        shift; DKD_IMAGE_TAG="$1"; export DKD_IMAGE_TAG ;;
    -h|--help)    sed -n '2,16p' "$0"; exit 0 ;;
    *)            die "未知参数：$1（-h 看用法）" ;;
  esac
  shift
done

require_docker
compose_setup
if printf '%s' "${DKD_COMPOSE[*]}" | grep -q infra; then
  info "部署模式：自带 MySQL/Redis（infra compose 已纳入）"
else
  info "部署模式：使用外部数据库（DKD_DB_HOST=$(env_get DKD_DB_HOST mysql)）"
fi

# ---- 0) 安全自检（部署前拦住最常见的“把模板值带上生产”）----
for k in DKD_DB_PASSWORD DKD_REDIS_PASSWORD DKD_JWT_SECRET DKD_OSS_ACCESS_KEY \
         DKD_OSS_SECRET_KEY DKD_DEEPSEEK_API_KEY DKD_AGENT_SERVICE_SECRET DKD_AGENT_DB_PASSWORD \
         DKD_DRUID_PASSWORD; do
  v="$(env_get "$k" CHANGE_ME)"
  case "$v" in
    ""|CHANGE_ME|CHANGE_ME_32_BYTES_MIN|sk-CHANGE_ME) die "$k 仍是模板/空值，拒绝部署（AGENTS §7.1）" ;;
  esac
done
ok "凭据自检通过（无模板占位值）"

# env_get 用 printf 输出（无尾随换行），因此 wc -c 就是真实长度
JWT_LEN="$(env_get DKD_JWT_SECRET | wc -c | tr -d ' ')"
[ "$JWT_LEN" -ge 32 ] && ok "JWT 密钥长度 ${JWT_LEN}（>=32）" \
  || warn "JWT 密钥长度仅 ${JWT_LEN}，建议 >=32 位强随机"

# ---- 1) 构建 ----
if [ "$DO_BUILD" = "1" ]; then
  info "构建镜像（首次较慢：Maven/npm/pip 依赖下载）"
  compose build
  ok "镜像构建完成"
else
  warn "--skip-build：使用已有镜像（确认 tag=${DKD_IMAGE_TAG:-latest} 是你要的版本）"
fi

# ---- 2) 起基础设施并等就绪 ----
if printf '%s' "${DKD_COMPOSE[*]}" | grep -q infra; then
  info "启动 MySQL / Redis"
  compose up -d mysql redis
  wait_healthy mysql 300 || die "MySQL 未就绪"
  wait_healthy redis 120 || die "Redis 未就绪"
fi

# ---- 3) 起应用 ----
info "启动应用栈"
compose up -d dkd-admin dkd-agent web
[ "$MOBILE" = "1" ] && compose --profile mobile up -d dkd-app || true

wait_healthy dkd-agent 180 || warn "dkd-agent 未 healthy（Java 网关会自动降级，不影响主业务）"
wait_healthy dkd-admin 300 || die "dkd-admin 未就绪（docker compose logs dkd-admin）"
wait_healthy web 60 || warn "web 未 healthy"

# ---- 4) 智能体数据库授权（幂等，可重复执行）----
info "配置 dkd-agent 数据库账号（两级授权 + 否决路径验证）"
"$DKDDIR/scripts/setup-agent-db.sh" || warn "授权脚本未全绿：业务表 dump 可能还没导入（见 README §4）"

# ---- 5) 健康检查 ----
info "健康检查"
"$DKDDIR/scripts/healthcheck.sh" || warn "健康检查有 FAIL 项，请按上面提示逐个处理"

echo
echo "== 访问入口 =="
echo "  管理前端：      http://<服务器IP>:$(env_get DKD_HTTP_PORT 80)/"
echo "  管理后台直连：  http://127.0.0.1:$(env_get DKD_ADMIN_PUBLISH_PORT 18080)/   （仅回环，SSH 隧道）"
[ "$MOBILE" = "1" ] && echo "  运维移动端：    http://<服务器IP>:$(env_get DKD_APP_PORT 9070)/"
echo "  智能体服务：    **不对外发布**（只允许 Java 网关在容器网络内访问，AGENTS §7.5）"
echo
echo "  降级演练：改 .env.deploy 的 DKD_AGENT_ENABLED=false → docker compose up -d dkd-admin"
