#!/usr/bin/env bash
# ============================================================================
# 部署后健康检查（一键回答“这次部署到底通没通”）
#
# 覆盖：容器 health → 前端静态站 → 后端可达 → 网关就绪 → **SSE 是否被缓冲**
# 用法：./scripts/healthcheck.sh
#   ADMIN_USER=xxx ADMIN_PASS='yyy' ./scripts/healthcheck.sh   # 生产库换了管理员口令时
# ============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

require_docker
compose_setup

HTTP_PORT="$(env_get DKD_HTTP_PORT 80)"
ADMIN_PORT="$(env_get DKD_ADMIN_PUBLISH_PORT 18080)"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin123}"
BASE="http://127.0.0.1:$HTTP_PORT"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

info "① 容器状态"
compose ps

info "② 容器 healthcheck"
for svc in mysql redis dkd-admin dkd-agent web dkd-app; do
  cid="$(compose ps -q "$svc" 2>/dev/null || true)"
  if [ -z "$cid" ]; then
    [ "$svc" = "dkd-app" ] && warn "$svc 未启动（可选，--profile mobile 才启）" || warn "$svc 未启动"
    continue
  fi
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
  [ "$state" = "healthy" ] && ok "$svc = healthy" || bad "$svc = $state（docker compose logs $svc）"
done

info "③ 前端静态站（nginx）"
code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$BASE/" || true)"
[ "$code" = "200" ] && ok "GET $BASE/ → 200" || bad "GET $BASE/ → $code"

info "④ 后端直连（仅回环发布端口）"
code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:$ADMIN_PORT/captchaImage" || true)"
[ "$code" = "200" ] && ok "GET :$ADMIN_PORT/captchaImage → 200" || bad "GET :$ADMIN_PORT/captchaImage → $code"

info "⑤ 经网关的智能体就绪状态（含降级开关语义）"
LOGIN="$(curl -s -m 20 -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" "$BASE/prod-api/login" || true)"
TOKEN="$(printf '%s' "$LOGIN" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
if [ -z "$TOKEN" ]; then
  warn "登录失败，跳过 ⑤/⑥：$(printf '%s' "$LOGIN" | head -c 120)"
  warn "  默认账号 admin/admin123 只适用于初始化库；生产库请用 ADMIN_USER/ADMIN_PASS 传入"
else
  ok "登录成功（token 长度 ${#TOKEN}）"
  ST="$(curl -s -m 20 -H "Authorization: Bearer $TOKEN" "$BASE/prod-api/agent/status" || true)"
  printf '%s' "$ST" | grep -q '"enabled":true' && ok "/agent/status enabled=true" || warn "/agent/status enabled 非 true（是否在做降级演练？）"
  printf '%s' "$ST" | grep -q '"upstream":"up"' && ok "/agent/status upstream=up（Python 可达）" || bad "/agent/status upstream 非 up：$(printf '%s' "$ST" | head -c 160)"

  info "⑥ SSE 逐帧透传（最能暴露 nginx 缓冲的检查）"
  RID="docker-health-$(date +%s)"
  curl -sN -m 60 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -H "X-Request-Id: $RID" -d '{"message":"deploy healthcheck","scene":1}' \
    "$BASE/prod-api/agent/chat" -o "$TMP/sse.txt" -D "$TMP/hdr.txt" || true
  grep -qi 'content-type: text/event-stream' "$TMP/hdr.txt" && ok "响应为 text/event-stream" || bad "Content-Type 非 SSE（可能打到了静态站）"
  grep -qi "x-request-id: $RID" "$TMP/hdr.txt" && ok "X-Request-Id 回带（$RID）" || bad "X-Request-Id 未回带（request_id 贯穿断裂）"
  grep -q 'event: meta' "$TMP/sse.txt" && ok "收到 meta 帧" || bad "未收到 meta 帧"
  grep -q 'event: done' "$TMP/sse.txt" && ok "收到 done 帧" || bad "未收到 done 帧（对话未正常结束）"
  if grep -q '"degrade":true' "$TMP/sse.txt"; then
    bad "出现降级帧（Python 不可达或被降级开关拦截）"
  else
    ok "无降级帧"
  fi
  # 逐 token 只在真 LLM 模式下才有意义（echo 模式本就是整段一次返回）；
  # delta 帧 > 1 同时证明「Java 逐块 flush + nginx 不缓冲」这条链路没有被攒起来。
  if grep -q '"mode":"llm"' "$TMP/sse.txt"; then
    deltas="$(grep -c 'event: delta' "$TMP/sse.txt" || true)"
    [ "${deltas:-0}" -gt 1 ] && ok "LLM 模式逐 token 下发（delta 帧=$deltas）" || bad "LLM 模式下 delta 帧仅 $deltas 个 → 疑似被缓冲"
  else
    warn "当前为 echo 模式（DKD_AGENT_USE_LLM=0），跳过“逐 token”判定；帧级链路已验证"
  fi
fi

summary
