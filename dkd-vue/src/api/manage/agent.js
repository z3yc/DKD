import { openSseStream } from '@/utils/sse'
import { getToken } from '@/utils/auth'

/**
 * 智能体网关 API（排期任务 0-11）
 *
 * 路径约定：前端一律只访问 Java 网关 `/agent/**`，由网关（任务 0-6）转发到 Python dkd-agent；
 * Python 只监听回环，不解析 JWT，只信任网关注入的 X-Agent-* 头（方案 §3.4）。
 */

/**
 * 流式对话（SSE）
 * 为什么返回 { promise, abort } 而不是 Promise：长请求必须可取消（AGENTS §2.2）
 *
 * @param {Object}   params
 * @param {string}   params.message        用户输入
 * @param {string}   [params.conversationId] 会话 ID（多轮 / 断线续传用，首轮由服务端下发）
 * @param {number}   [params.scene]        场景：1-通用问答 2-补货 3-诊断 4-运营分析
 * @param {string}   [params.lastEventId]  断点续传：上次收到的最后一帧 id
 * @param {Function} params.onEvent        每帧回调 (frame) => void
 */
export function chatWithAgent(params) {
  return openSseStream({
    url: '/agent/chat',
    body: {
      message: params.message,
      conversation_id: params.conversationId || undefined,
      scene: params.scene || 1
    },
    lastEventId: params.lastEventId,
    idleTimeout: params.idleTimeout,
    onEvent: params.onEvent
  })
}

/**
 * 网关就绪状态探测：用于降级判定（方案 §3.3 —— agent.enabled=false 或 Python 不可达时，
 * 前端应隐藏/禁用对话入口并给出提示，而不是伪造会话能力）
 *
 * 为什么不用 axios（@/utils/request）：其响应拦截器对 code!==200 会弹全局 ElNotification，
 * 而"智能体未启用/暂不可用"是**预期状态**而非系统错误，弹窗会误导用户。
 * 这里用 fetch 探测，失败一律归一化为 { enabled:false, upstream:'down' }。
 */
export function probeAgentStatus() {
  const headers = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = 'Bearer ' + token
  const base = import.meta.env.VITE_APP_BASE_API || ''
  return fetch(base + '/agent/status', { method: 'GET', headers })
    .then((res) => res.json())
    .then((payload) => {
      // RuoYi 的认证失败是 HTTP 200 + code=401，必须先看信封 code，否则会把“登录过期”误报成“服务不可用”
      if (payload && payload.code === 401) {
        return { enabled: false, upstream: 'down', unauthorized: true }
      }
      const data = (payload && payload.data) || {}
      return {
        enabled: data.enabled !== false,
        upstream: data.upstream === 'up' ? 'up' : 'down',
        unauthorized: false
      }
    })
    .catch(() => ({ enabled: false, upstream: 'down', unauthorized: false }))
}
