/**
 * SSE 帧解析（纯函数，无任何依赖 —— 便于离线复跑验证）
 *
 * 为什么单独成文件：分帧是流式对话里最容易出错的一环（跨 chunk 截断、多行 data、
 * 服务端心跳注释行），把纯函数与 fetch/AbortController 传输层分离后，
 * 可以用真实抓包数据直接复跑断言，不依赖浏览器环境。
 * 传输层见 `src/utils/sse.js`。
 */

/** 帧边界：兼容 \n\n 与 \r\n\r\n（Python 侧 starlette 输出 \n\n） */
export const FRAME_BOUNDARY = /\r?\n\r?\n/

/**
 * 注释/空帧（如 `: keep-alive` 心跳）不属于可派发事件，按 SSE 规范直接丢弃。
 * 注意：丢弃只影响回调，**不影响空闲计时器** —— 收到任何字节（含心跳）都会重置计时（见 sse.js）。
 */
function isIgnorableFrame(raw) {
  const lines = raw.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line === '' || line.charAt(0) === ':') continue
    return false
  }
  return true
}

/**
 * 从缓冲中尽可能多地取出完整帧
 * @param {string} buffer 累积缓冲
 * @returns {{frames: string[], rest: string}} frames=完整帧原始文本，rest=尚未闭合的尾部
 */
export function extractFrames(buffer) {
  const frames = []
  let rest = buffer
  let index = rest.search(FRAME_BOUNDARY)
  while (index !== -1) {
    const raw = rest.slice(0, index)
    const boundary = rest.slice(index).match(FRAME_BOUNDARY)[0]
    rest = rest.slice(index + boundary.length)
    if (!isIgnorableFrame(raw)) frames.push(raw)
    index = rest.search(FRAME_BOUNDARY)
  }
  return { frames, rest }
}

/**
 * 单帧解析为 { id, event, data }
 * 说明：`data:` 可重复出现（多行数据），按 SSE 规范用 \n 连接；
 *      以 ':' 开头的是注释行（心跳 keep-alive），这里忽略但调用方仍需重置空闲计时器。
 */
export function parseFrame(raw) {
  const frame = { id: undefined, event: 'message', data: '' }
  const lines = raw.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line === '' || line.charAt(0) === ':') continue
    const sep = line.indexOf(':')
    const field = sep === -1 ? line : line.slice(0, sep)
    let value = sep === -1 ? '' : line.slice(sep + 1)
    if (value.charAt(0) === ' ') value = value.slice(1)
    if (field === 'data') {
      frame.data = frame.data === '' ? value : frame.data + '\n' + value
    } else if (field === 'event') {
      frame.event = value
    } else if (field === 'id') {
      frame.id = value
    }
  }
  return frame
}

/** data 尽量解析成对象；解析失败时原样返回字符串（LLM 输出不可信，AGENTS §7.6） */
export function parseData(data) {
  if (!data) return {}
  try {
    return JSON.parse(data)
  } catch (e) {
    return { text: data }
  }
}
