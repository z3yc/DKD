/**
 * SSE 流式客户端（排期任务 0-11 / 方案 V1.1 §3.3）
 *
 * 为什么不用 EventSource：
 *   EventSource 无法携带自定义请求头，而本项目所有请求必须带 `Authorization: Bearer <jwt>`。
 * 为什么不用现有 axios 实例（src/utils/request.js）：
 *   该实例 `timeout: 10000` 且响应拦截器按 `res.data.code` 解析 JSON，
 *   会把 text/event-stream 当 JSON 处理（方案 §3.3 已把这条列为已知坑）。
 * 因此这里用 fetch + ReadableStream 手写，提供三项硬要求（AGENTS §2.2）：
 *   1) 增量分帧：按 SSE 帧边界逐帧回调，不等待整个响应体；
 *   2) 可取消：内部持有 AbortController，暴露 abort()；
 *   3) 心跳/空闲超时：长时间收不到任何字节即中止，避免"假死"占住界面。
 */

import { getToken } from '@/utils/auth'
import { extractFrames, parseFrame, parseData } from '@/utils/sseFrames'

/** 默认空闲超时：LLM 首包可能较慢（实测 DeepSeek 单轮 5s+），给足但不容忍假死 */
const DEFAULT_IDLE_TIMEOUT = 60000

/** 拼接绝对地址：与 axios 实例保持同一 baseURL（开发 /dev-api 由 vite 代理到 8080） */
function resolveUrl(url) {
  if (/^https?:\/\//i.test(url)) return url
  const base = import.meta.env.VITE_APP_BASE_API || ''
  return base + url
}

/** 是否真的是 SSE 流：网关/安全链在异常时会回 JSON 信封，不能用 response.ok 代替判定 */
function isEventStream(response) {
  const contentType = response.headers.get('content-type') || ''
  return contentType.indexOf('text/event-stream') !== -1
}

/**
 * 打开一条 SSE 流
 *
 * @param {Object}   options
 * @param {string}   options.url           后端路径，如 '/agent/chat'
 * @param {Object}   options.body          请求体（JSON 序列化）
 * @param {Object}   [options.headers]     额外请求头
 * @param {string}   [options.lastEventId] 断点续传：**仅在重连续传时**传，普通新一轮对话绝不能带（见 api/manage/agent.js 注释）
 * @param {number}   [options.idleTimeout] 空闲超时毫秒，0 表示不启用
 * @param {Function} [options.onEvent]     每帧回调 (frame) => void
 * @returns {{ promise: Promise<void>, abort: Function }}
 */
export function openSseStream(options) {
  const { url, body, headers = {}, lastEventId, idleTimeout = DEFAULT_IDLE_TIMEOUT, onEvent } = options
  const controller = new AbortController()
  let timer = null
  let settled = false
  let idleTimedOut = false

  function clearIdleTimer() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function armIdleTimer() {
    clearIdleTimer()
    if (!idleTimeout || settled) return
    timer = setTimeout(() => {
      idleTimedOut = true
      controller.abort()
    }, idleTimeout)
  }

  const promise = (async () => {
    const requestHeaders = {
      'Content-Type': 'application/json;charset=utf-8',
      Accept: 'text/event-stream',
      ...headers
    }
    const token = getToken()
    if (token) requestHeaders.Authorization = 'Bearer ' + token
    if (lastEventId) requestHeaders['Last-Event-ID'] = lastEventId

    armIdleTimer()
    let response
    try {
      response = await fetch(resolveUrl(url), {
        method: 'POST',
        headers: requestHeaders,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal
      })
    } catch (e) {
      clearIdleTimer()
      settled = true
      if (controller.signal.aborted) {
        // 主动取消不算错误，交给调用方按 aborted 分支处理
        const abortError = new Error(idleTimedOut ? '响应超时，已中止' : '已取消')
        abortError.aborted = true
        throw abortError
      }
      const netError = new Error('智能体服务连接失败，请检查网络或稍后重试')
      netError.retryable = true // 网络层失败 ⇒ 可用 Last-Event-ID 续传（调用方决定）
      throw netError
    }

    if (!response.ok || !isEventStream(response)) {
      clearIdleTimer()
      settled = true
      // 非 SSE 响应走这里：网关降级（503）与**未认证**都返 RuoYi 信封。
      // 注意：RuoYi 的认证失败是 HTTP 200 + code=401（与 /manage/** 一致），只看 response.ok 会漏掉，
      // 若当流解析会得到一个永远不出的空白气泡 —— 因此必须按 Content-Type 判定。
      let message = '智能体服务不可用'
      let code = response.status
      let unauthorized = false
      let degraded = response.status === 503
      try {
        const payload = await response.json()
        if (payload && payload.msg) message = payload.msg
        if (payload && payload.code) code = payload.code
        unauthorized = code === 401
        degraded = degraded || code === 503
      } catch (e) {
        // 响应体不是 JSON，保留默认提示（不得把原始报文透给用户）
        message = message + '（HTTP ' + response.status + '）'
      }
      const degradeError = new Error(message)
      degradeError.code = code
      degradeError.unauthorized = unauthorized
      degradeError.degraded = degraded
      degradeError.retryable = false // 信封类错误（未认证/降级）不靠重试解决
      throw degradeError
    }

    if (!response.body) {
      clearIdleTimer()
      settled = true
      throw new Error('当前浏览器不支持流式响应（ReadableStream 不可用）')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        armIdleTimer()
        buffer += decoder.decode(value, { stream: true })
        const parsed = extractFrames(buffer)
        buffer = parsed.rest
        for (let i = 0; i < parsed.frames.length; i++) {
          const frame = parseFrame(parsed.frames[i])
          frame.parsed = parseData(frame.data)
          if (onEvent) onEvent(frame)
        }
      }
      // 流结束但缓冲里还有半帧（服务端异常断开）——不静默丢弃，交给上层看是否已收到 done
      if (buffer.trim() !== '' && onEvent) {
        const frame = parseFrame(buffer)
        frame.parsed = parseData(frame.data)
        frame.partial = true
        onEvent(frame)
      }
    } catch (e) {
      if (controller.signal.aborted) {
        const abortError = new Error(idleTimedOut ? '响应超时，已中止' : '已取消')
        abortError.aborted = true
        throw abortError
      }
      const brokenError = new Error('流式响应中断，请重试')
      brokenError.retryable = true // 中途断开 ⇒ 同样可续传
      throw brokenError
    } finally {
      clearIdleTimer()
      settled = true
      try {
        // 必须 await：cancel() 返回 Promise，未 await 的拒绝不会被 try/catch 捕获，
        // 会在浏览器控制台报 “AbortError: BodyStreamBuffer was aborted”（实测踩坑）
        await reader.cancel()
      } catch (e) {
        // reader 已关闭/已被 abort 时 cancel 会拒绝，无需处理（不掩盖上面的业务异常）
      }
    }
  })()

  return {
    promise,
    abort() {
      clearIdleTimer()
      controller.abort()
    }
  }
}
