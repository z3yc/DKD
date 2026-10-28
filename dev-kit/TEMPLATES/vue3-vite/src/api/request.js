/**
 * 统一请求封装。
 *
 * 为什么必须有这一层：如果每个组件各自 axios 调用，错误处理会出现 N 种实现，
 * 而"哪个错误能重试、哪个要跳登录"这种决策必须只有一份。
 *
 * 职责：
 * 1. 统一注入 request_id（透传上游或新生成），便于前后端日志串联；
 * 2. 按错误码类别决定行为（契约 CONTRACTS/error-codes.md）：仅 06/07 自动重试；
 * 3. 统一错误提示与 401 处理；
 * 4. 暴露 cancel 能力（AbortController）。
 */
import axios from 'axios'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: Number(import.meta.env.VITE_API_TIMEOUT_MS || 10000)
})

// 可自动重试的类别：06 限流 / 07 依赖不可用。其余重试一万次还是错。
const RETRYABLE = ['06', '07']

function isRetryable(code) {
  if (!code || code.length < 4) return false
  // 错误码结构 <SYS><CC><NN>：截取类别段需先去掉 2 位系统标识
  const category = code.slice(2, 4)
  return RETRYABLE.includes(category)
}

function newRequestId() {
  // 前端生成的 request_id 后端同样接受，保证链路可串
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

request.interceptors.request.use((config) => {
  if (!config.headers['X-Request-Id']) {
    config.headers['X-Request-Id'] = newRequestId()
  }
  return config
})

request.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const cfg = error.config || {}
    const body = error.response?.data
    const code = body?.code

    // 指数退避重试：仅在可重试类别且未超过上限时；非幂等方法（POST）不重试
    const method = (cfg.method || 'get').toLowerCase()
    const canRetry =
      isRetryable(code) && method === 'get' && (cfg.__retryCount || 0) < 2
    if (canRetry) {
      cfg.__retryCount = (cfg.__retryCount || 0) + 1
      await new Promise((r) => setTimeout(r, 500 * 2 ** (cfg.__retryCount - 1)))
      return request(cfg)
    }

    // 401：未登录/过期 —— 交给上层路由守卫处理，这里不静默吞掉
    if (error.response?.status === 401) {
      window.dispatchEvent(new CustomEvent('app:unauthorized'))
      return Promise.reject(error)
    }

    // 统一提示：优先用服务端 message（已是面向用户的文案）
    const message = body?.message || '请求失败，请稍后重试'
    window.dispatchEvent(new CustomEvent('app:error', { detail: { code, message } }))
    return Promise.reject(error)
  }
)

/**
 * 发起可取消的请求。调用方在组件卸载时必须 abort(),
 * 否则会出现"响应回来时组件已销毁"的竞态与内存泄漏。
 */
export function requestWithCancel(config) {
  const controller = new AbortController()
  const promise = request({ ...config, signal: controller.signal })
  return { promise, abort: () => controller.abort() }
}

export default request
