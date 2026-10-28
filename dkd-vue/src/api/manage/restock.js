import request from '@/utils/request'
import { SESSION_EXPIRED_MSG } from '@/utils/sessionExpired'

/**
 * 智能补货工作台 API（后端：排期任务 1-7；前端：1-10 / 1-11）
 *
 * 路径约定：前端只访问 Java 网关 `/agent/restock/**`，由网关转发到 Python dkd-agent
 * 并注入 `X-Agent-User`。Python 侧写操作**强制要求**该头（无身份头直接 401），
 * 所以本地直连 Python 是写不进去的（AGENTS §7.5 鉴权不裸奔）。
 *
 * P1-16 起回归 `@/utils/request`（axios 统一通道），不再自带 fetch：
 * 原先绕开 axios 的根因是「非 2xx 业务码的 msg 会被拦截器改写成『系统接口409异常』」，
 * 该根因已在 request.js 修复（错误分支透传响应体 msg + silent 逃生舱）。
 * 迁移时保住三个既有语义：
 *   ① 后端把「业务拒绝」表达为**真实 HTTP 状态码**（409/422/400/502/503），
 *     可行动文案在 msg——由 request.js 错误分支透传，错误对象带 code；
 *   ② AbortSignal 可取消（页面卸载中止在途请求，取消不弹错）；
 *   ③ 不设超时（确认建单要等 Java 回调，N 台设备时可达数十秒，fetch 时代同样无超时）。
 *
 * 契约：所有函数**只 resolve、绝不 throw**，统一返回
 * `{ ok, code, msg, data }`；调用方据此决定 toast 文案，不需要 try/catch。
 */

/**
 * 统一请求。
 *
 * @param {string} url    已编码的路径（含查询串），**不含** VITE_APP_BASE_API
 * @param {Object} [opts]
 * @param {string} [opts.method] HTTP 方法，默认 GET
 * @param {Object} [opts.body]   请求体（POST 时作为 JSON 发送）
 * @param {AbortSignal} [opts.signal] 取消信号（页面卸载时中止在途请求）
 * @returns {Promise<{ok:boolean, code:number, msg:string, data?:any, unauthorized?:boolean, network?:boolean, aborted?:boolean}>}
 */
async function call(url, opts) {
  const options = opts || {}
  try {
    // silent：拦截器只做信封解析，提示与交互由工作台视图自管（避免双重弹窗）
    const payload = await request({
      url,
      method: options.method || 'GET',
      data: options.body,
      signal: options.signal,
      timeout: 0,
      silent: true
    })
    // 走到这里信封 code 一定是 200（拦截器已 reject 其余情况）
    return { ok: true, code: 200, msg: payload.msg || 'ok', data: payload.data }
  } catch (e) {
    // 主动取消（axios CanceledError / request.js 的 aborted 标记）：不该弹错误提示
    if (e && (e.aborted === true || e.code === -1 || e.name === 'CanceledError' || e.code === 'ERR_CANCELED')) {
      return { ok: false, code: -1, msg: '请求已取消', aborted: true }
    }
    if (e && e.code === 401) {
      // 拦截器已完成「提示 + 重新登录」处置（handleSessionExpired），
      // 这里只回传可行动文案（0-11 / 2026-10-03 实测报障的教训：401 必须给重新登录入口）
      return {
        ok: false,
        code: 401,
        msg: SESSION_EXPIRED_MSG + '，请重新登录后再操作',
        unauthorized: true
      }
    }
    if (e && e.network) {
      return { ok: false, code: 0, msg: '智能体服务连接失败，请稍后重试', network: true }
    }
    // 业务拒绝（409/422/400/502/503...）：code 与 msg 已由 request.js 从信封透传
    const code = (e && e.code) || 0
    return {
      ok: false,
      code: code,
      msg: (e && e.message) || '操作失败（code=' + code + '）'
    }
  }
}

/** 拼 `plan_date` 查询参数（后端把日期作为 query 传递，POST 也一样） */
function withDate(url, planDate) {
  if (!planDate) return url
  return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'plan_date=' + encodeURIComponent(planDate)
}

/**
 * 补货计划清单（工作台数据源）。
 *
 * @param {string} [planDate] YYYY-MM-DD；缺省为服务端「今天」（06:00 定时任务写入的那份）
 * @param {AbortSignal} [signal]
 * @returns data: `{ planDate, stats:{pending,urgent,totalQuantity}, plans:[...] }`
 *   plans[i] 字段（camelCase，见 `restock_decisions.summarize_plan`）：
 *   `{ id, vmId, innerCode, status, skuCount, totalQuantity, assigneeId, assigneeName, items[] }`
 *   之外还有 `taskId / taskCode / adjustReason / adjustedBy / adjustedTime`。
 *   ⚠️ **没有点位名/设备地址字段**（契约未提供），页面只如实展示 innerCode，不拼假地址。
 */
export function listRestockPlans(planDate, signal) {
  return call(withDate('/agent/restock/plans', planDate), { signal })
}

/**
 * 确认建单（幂等：已建单再点返回「已建单」而不是报错）。
 *
 * 后端设计取舍（`api/restock.py` 模块 docstring）：**不提供批量端点**，
 * 「全部确认建单」由前端逐条调用本函数组合——批量端点会把
 * 「一台失败是否整批回滚」变成没人说得清的问题。故调用方需自行汇总每条结果。
 *
 * @param {number} planId
 * @param {string} [planDate]
 */
export function confirmRestockPlan(planId, planDate) {
  return call(withDate('/agent/restock/plans/' + planId + '/confirm', planDate), { method: 'POST' })
}

/**
 * 调整建议。两种模式**互斥**（同时给会被后端拒绝：分不清人工意图）：
 *   ① 指定数量：`{ quantity, channelCode? }`（channelCode 缺省取该计划第一个货道）
 *   ② 按新参数重算：`{ windowDays?, serviceLevel?, coverageDays? }`
 * 两种模式都必须带 `reason`（必填，用于复盘归因）。
 *
 * ⚠️ 字段名坑：`windowDays` / `serviceLevel` / `coverageDays` 是后端显式 alias，
 * 但 `channel_code` **没有 alias**，必须原样发 snake_case，否则会被 Pydantic 当成未知字段丢掉
 * （表现为“我选了货道却调整了别的货道”这种最难查的错）。
 *
 * @param {number} planId
 * @param {Object} payload `{ reason, quantity?, channel_code?, windowDays?, serviceLevel?, coverageDays? }`
 * @param {string} [planDate]
 */
export function adjustRestockPlan(planId, payload, planDate) {
  return call(withDate('/agent/restock/plans/' + planId + '/adjust', planDate), {
    method: 'POST',
    body: payload
  })
}

/**
 * 跳过建议（原因必填，终态；重复跳过返回 409）。
 *
 * @param {number} planId
 * @param {string} reason
 * @param {string} [planDate]
 */
export function skipRestockPlan(planId, reason, planDate) {
  return call(withDate('/agent/restock/plans/' + planId + '/skip', planDate), {
    method: 'POST',
    body: { reason: reason }
  })
}

/**
 * 恢复建议（3-已跳过 → 1-建议；原因必填，且**仅限当天**——跨日改写会污染 3-6 复盘口径）。
 *
 * @param {number} planId
 * @param {string} reason
 * @param {string} [planDate]
 */
export function restoreRestockPlan(planId, reason, planDate) {
  return call(withDate('/agent/restock/plans/' + planId + '/restore', planDate), {
    method: 'POST',
    body: { reason: reason }
  })
}

/**
 * 查询自动分析开关（原型 V2 的暂停横幅）。
 *
 * @returns data 为 `PauseState`，键是 **snake_case**（后端 `model_dump(mode="json")` 未走 alias）：
 *   `{ paused, reason, paused_by, paused_time, resumed_by, resumed_time }`
 */
export function getRestockPauseState() {
  return call('/agent/restock/pause')
}

/**
 * 暂停 / 恢复自动分析（原因必填：这是「让系统闭嘴」的操作，必须能事后解释）。
 *
 * @param {boolean} paused true=暂停（次日 06:00 不再生成建议），false=恢复
 * @param {string} reason
 */
export function setRestockPause(paused, reason) {
  return call(paused ? '/agent/restock/pause' : '/agent/restock/resume', {
    method: 'POST',
    body: { reason: reason }
  })
}
