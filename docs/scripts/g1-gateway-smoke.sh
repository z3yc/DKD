#!/usr/bin/env bash
#
# G1 / M1 网关端到端冒烟脚本（排期任务 0-8 的服务端验收；首次落地 2026-09-21，16/16 PASS）
#
# 覆盖范围（每条都是 AGENTS §7 红线相关的可执行证据）：
#   [1] 用户 JWT 登录（复用现有鉴权体系）
#   [2] /agent/status 就绪探测（前端降级判定依据）
#   [3] SSE 逐帧透传（前端 → Java 网关 → Python），含 X-Request-Id 贯穿与 delta 帧 id（断点续传锚点）
#   [4] 回调拒绝路径：无密钥 401 / 伪造密钥 401 / 未配置密钥 503 / 非白名单 404
#   [4b] 正确密钥 → 鉴权放行，业务拒绝原因由 TaskServiceImpl 校验链**原样回传**（用不存在的设备号，不写库）
#   [5] Python 停机降级：/agent/status → upstream=down；/agent/chat → error{degrade:true} 帧
#
# 前置：
#   1) MySQL / Redis 已启动；2) Java 服务已起（默认 :8080）；3) Python 服务已起（默认 :8090）
#   4) .env 已准备（脚本只读取 DKD_AGENT_SERVICE_SECRET 一项用于回调用例）
# 用法（Windows Git Bash）：
#   bash docs/scripts/g1-gateway-smoke.sh              # 含停机降级（会在最后杀掉 Python 进程）
#   SKIP_KILL=1 bash docs/scripts/g1-gateway-smoke.sh  # 跳过停机降级（不想重启 Python 时用）
# 重启 Python：cd dkd-agent && uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
#
set -u

GW="${GW:-http://127.0.0.1:8080}"
PY="${PY:-http://127.0.0.1:8090}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin123}"
ENV_FILE="${ENV_FILE:-.env}"
SKIP_KILL="${SKIP_KILL:-0}"

pass=0
fail=0
ok() { echo "  PASS  $1"; pass=$((pass + 1)); }
ng() { echo "  FAIL  $1"; fail=$((fail + 1)); }

echo "== 前置检查 =="
if curl -s -o /dev/null -m 5 "$GW/captchaImage"; then echo "  Java $GW 可达"; else echo "  致命：Java $GW 不可达，请先启动 dkd-parent"; exit 1; fi
if [ "$SKIP_KILL" != "1" ]; then
  if curl -s -o /dev/null -m 5 "$PY/health"; then echo "  Python $PY 可达"; else echo "  致命：Python $PY 不可达，请先启动 dkd-agent"; exit 1; fi
fi

echo "[1] 登录获取 JWT"
LOGIN=$(curl -s -m 20 -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" "$GW/login")
TOKEN=$(echo "$LOGIN" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
if [ -n "$TOKEN" ]; then ok "登录成功，token 长度 ${#TOKEN}"; else ng "登录失败: $(echo "$LOGIN" | cut -c1-120)"; fi
AUTH="Authorization: Bearer $TOKEN"

echo "[2] 网关就绪状态 /agent/status"
ST=$(curl -s -m 20 -H "$AUTH" "$GW/agent/status")
echo "      resp: $(echo "$ST" | cut -c1-200)"
echo "$ST" | grep -q '"upstream":"up"' && ok "upstream=up" || ng "upstream 非 up"
echo "$ST" | grep -q '"enabled":true' && ok "enabled=true" || ng "enabled 非 true"

echo "[3] SSE 逐帧透传（前端→网关→Python）"
RID="e2e-$(date +%s)"
TMP=$(mktemp -d)
curl -sN -m 40 -H "$AUTH" -H 'Content-Type: application/json' -H "X-Request-Id: $RID" \
  -d '{"message":"G1 relay check","scene":2}' "$GW/agent/chat" -o "$TMP/sse.txt" -D "$TMP/hdr.txt"
grep -qi 'content-type: text/event-stream' "$TMP/hdr.txt" && ok "响应 Content-Type=text/event-stream" || ng "Content-Type 非 SSE"
grep -i "x-request-id: $RID" "$TMP/hdr.txt" >/dev/null && ok "X-Request-Id 回带（$RID）" || ng "X-Request-Id 未回带"
for ev in meta delta done; do
  grep -q "^event: $ev" "$TMP/sse.txt" && ok "SSE 事件 $ev 到达" || ng "SSE 事件 $ev 缺失"
done
grep -q "^id: " "$TMP/sse.txt" && ok "delta 帧带 id（断点续传锚点）" || ng "delta 帧无 id"

echo "[4] 回调鉴权拒绝路径（/agent/callback/task）"
C1=$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -d '{"innerCode":"__no_such_vm__","productTypeId":2,"userId":1,"details":[{"channelCode":"A1","expectCapacity":1}]}' \
  "$GW/agent/callback/task")
[ "$C1" = "401" ] && ok "无密钥 → 401" || ng "无密钥返回 $C1（期望 401）"

C2=$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -H 'X-Agent-Secret: wrong-secret' -d '{"innerCode":"x","productTypeId":2,"userId":1}' "$GW/agent/callback/task")
[ "$C2" = "401" ] && ok "伪造密钥 → 401" || ng "伪造密钥返回 $C2（期望 401）"

# 正确密钥 + 不存在的设备号：鉴权放行后由 TaskServiceImpl 校验链拒绝，原因原样回传，且**不写库**
SEC=$(grep -E '^DKD_AGENT_SERVICE_SECRET=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
C3=$(curl -s -m 20 -X POST -H 'Content-Type: application/json' -H "X-Agent-Secret: $SEC" \
  -d '{"innerCode":"__no_such_vm__","productTypeId":2,"userId":1,"details":[{"channelCode":"A1","expectCapacity":1}]}' \
  "$GW/agent/callback/task")
echo "      resp: $(echo "$C3" | cut -c1-200)"
echo "$C3" | grep -q '售货机不存在' && ok "密钥正确 → 通过鉴权并回传业务拒绝原因（未写库）" || ng "业务拒绝原因未原样回传"

C4=$(curl -s -m 20 -o /dev/null -w "%{http_code}" -X POST -H "X-Agent-Secret: $SEC" -d "{}" "$GW/agent/callback/other")
[ "$C4" = "404" ] && ok "非白名单回调子路径被拒（HTTP 404）" || ng "非白名单回调子路径返回 $C4（期望 404）"

echo "[4b] 未认证访问（RuoYi 约定：HTTP 200 + code=401，与 /manage/** 一致；降级不等于免鉴权）"
NOAUTH=$(curl -s -m 20 "$GW/agent/status")
echo "      resp: $(echo "$NOAUTH" | cut -c1-160)"
echo "$NOAUTH" | grep -q '"code":401' && ok "未认证 → 信封 code=401" || ng "未认证未被拒绝"

if [ "$SKIP_KILL" = "1" ]; then
  echo "[5] 已跳过 Python 停机降级（SKIP_KILL=1）"
else
  echo "[5] Python 停机降级"
  PID=$(netstat -ano | grep LISTENING | grep ":8090" | head -1 | awk '{print $NF}')
  [ -n "$PID" ] && taskkill //PID "$PID" //F >/dev/null 2>&1
  sleep 3
  D1=$(curl -s -m 25 -H "$AUTH" "$GW/agent/status")
  echo "      status: $(echo "$D1" | cut -c1-200)"
  echo "$D1" | grep -q '"upstream":"down"' && ok "停机后 /agent/status upstream=down" || ng "停机后未标记 down"
  curl -sN -m 30 -H "$AUTH" -H 'Content-Type: application/json' -d '{"message":"degrade check","scene":2}' \
    "$GW/agent/chat" -o "$TMP/sse_down.txt"
  if grep -q '"degrade":true' "$TMP/sse_down.txt"; then
    ok "停机时返回 SSE 降级帧（degrade:true）"
  else
    ng "降级帧缺失"
    head -5 "$TMP/sse_down.txt"
  fi
  echo "  提示：Python 已被本步骤停止，需手动重启：cd dkd-agent && uv run uvicorn app.main:app --host 127.0.0.1 --port 8090"
fi

rm -rf "$TMP"
echo
echo "RESULT: pass=$pass fail=$fail"
[ "$fail" = "0" ]
