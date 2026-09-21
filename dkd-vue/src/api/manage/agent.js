import { openSseStream } from '@/utils/sse'
import { getToken } from '@/utils/auth'

/**
 * 智能体网关 API（排期任务 0-11）
 *
 * 路径约定：前端一律只访问 Java 网关 `/agent/**`，由网关（任务 0-6）转发到 Python dkd-agent；
 * Python 只监听回环，不解析 JWT，只信任网关注入的 X-Agent-* 头（方案 §3.4）。
 */

/**
 * 流式对话（SSE）——新一轮用户输入
 *
 * ⚠️ 这里**刻意不传 `Last-Event-ID`**：该头在 Python 侧表示“重连续传上一轮”，
 * 若在正常新一轮也带上，服务端会把它当续传请求处理（实测：第二轮直接返回
 * “会话不存在或无可续传内容”，对话直接断掉）。续传只走 {@link resumeAgentStream}。
 *
 * @param {Object}   params
 * @param {string}   params.message        用户输入
 * @param {string}   [params.conversationId] 会话 ID（多轮用，首轮由服务端下发）
 * @param {number}   [params.scene]        场景：1-通用问答 2-补货 3-诊断 4-运营分析
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
    idleTimeout: params.idleTimeout,
    onEvent: params.onEvent
  })
}

/**
 * 断线续接：带 `Last-Event-ID` 重连同一轮，服务端**不重跑**对话图，只补发未收到的帧
 * （Python 侧 0-10 已实现并按此验证）。
 *
 * 为什么 `message` 仍要传：Python 的请求模型要求 message 非空，但续传路径会忽略它。
 * 这里传回**该轮的用户原文**，既满足校验，又在语义上如实表达“不重复消费这轮输入”。
 *
 * @param {Object}   params
 * @param {string}   params.message        该轮用户原文（服务端会忽略）
 * @param {string}   params.conversationId 会话 ID（= LangGraph thread_id）
 * @param {string}   params.lastEventId    已收到的最后一个 delta 帧 id
 * @param {number}   [params.scene]
 * @param {Function} params.onEvent
 */
export function resumeAgentStream(params) {
  return openSseStream({
    url: '/agent/chat',
    body: {
      message: params.message,
      conversation_id: params.conversationId,
      scene: params.scene || 1
    },
    lastEventId: String(params.lastEventId),
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
      // RuoYi 的认证失败是 HTTP 200 + code=401，必须先看信封 code，否则会把"登录过期"误报成"服务不可用"
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
