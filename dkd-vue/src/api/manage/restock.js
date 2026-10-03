import { getToken } from '@/utils/auth'
import { handleSessionExpired, SESSION_EXPIRED_MSG } from '@/utils/sessionExpired'

/**
 * 智能补货工作台 API（后端：排期任务 1-7；前端：1-10 / 1-11）
 *
 * 路径约定：前端只访问 Java 网关 `/agent/restock/**`，由网关转发到 Python dkd-agent
 * 并注入 `X-Agent-User`。Python 侧写操作**强制要求**该头（无身份头直接 401），
 * 所以本地直连 Python 是写不进去的（AGENTS §7.5 鉴权不裸奔）。
 *
 * ⚠️ 为什么这里刻意不走 `@/utils/request`（axios 拦截器）：
 * 后端把「业务拒绝」表达为**真实 HTTP 状态码**，可行动文案在 `msg` 里：
 *   - 409 = 状态机拒绝 / 并发冲突（如「已建单的计划不能重复确认」）
 *   - 422 = 字段校验（如原因字段缺失）
 *   - 400 = 业务规则拒绝（如「调整必须填写原因」「服务水平必须落在 [0.5, 0.99]」）
 *   - 502 = 回调 Java 建单失败
 *   - 503 = 补货服务未初始化
 * 而 axios 拦截器对非 2xx 会落入 error 分支，**丢掉 msg**、统一改写成
 * 「系统接口409异常」并 `Promise.reject(error)`。运营看到的会是「系统异常」，
 * 而不是「已建单的计划不能重复确认」这类能自救的提示——这正是 1-10/1-11 最该避免的体验。
 * 因此本文件用 fetch 直接读信封，处置方式与 `api/manage/agent.js::probeAgentStatus` 一致。
 *
 * 契约：所有函数**只 resolve、绝不 throw**，统一返回
 * `{ ok, code, msg, data }`；调用方据此决定 toast 文案，不需要 try/catch。
 */

const BASE = import.meta.env.VITE_APP_BASE_API || ''

/**
 * 统一请求。
 *
 * @param {string} url    已编码的路径（含查询串），**不含** VITE_APP_BASE_API
 * @param {Object} [opts]
 * @param {string} [opts.method] HTTP 方法，默认 GET
 * @param {Object} [opts.body]   请求体（给了才带 Content-Type: application/json）
 * @param {AbortSignal} [opts.signal] 取消信号（页面卸载时中止在途请求）
 * @returns {Promise<{ok:boolean, code:number, msg:string, data?:any, unauthorized?:boolean, network?:boolean}>}
 */
async function call(url, opts) {
  const options = opts || {}
  const headers = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = 'Bearer ' + token

  const init = { method: options.method || 'GET', headers }
  if (options.signal) init.signal = options.signal
  if (typeof options.body !== 'undefined') {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(options.body)
  }

  let res
  try {
    res = await fetch(BASE + url, init)
  } catch (e) {
    // 区分「主动取消」与「真的连不上」：取消不该弹错误提示（AGENTS §2.2 可取消）
    if (e && e.name === 'AbortError') {
      return { ok: false, code: -1, msg: '请求已取消', aborted: true }
    }
    return { ok: false, code: 0, msg: '智能体服务连接失败，请稍后重试', network: true }
  }

  let payload = null
  try {
    payload = await res.json()
  } catch (e) {
    payload = null
  }

  // 网关/框架层裸错误（无 RuoYi 信封）也要给一句人话，而不是 undefined
  if (!payload || typeof payload.code === 'undefined') {
    return { ok: false, code: res.status, msg: '智能体服务返回异常（HTTP ' + res.status + '）' }
  }
  // RuoYi 的认证失败是 HTTP 200 + code=401，必须先看信封 code（0-11 已踩过同款坑）
  if (payload.code === 401) {
    // ⚠️ 不能只把这个标记扔在返回值里（2026-10-03 实测报障）：本文件刻意绕开了 axios 拦截器，
    // 而拦截器正是承载「提示 + 重新登录」的地方。此前没有任何视图消费 `unauthorized`，
    // 于是运营看到的是 RuoYi 原文「请求访问：/agent/restock/plans/97/adjust，认证失败，
    // 无法访问系统资源」——既不知道是自己登录过期，也没有重新登录的入口。
    // 现在统一走 utils/sessionExpired（与 axios 链路同一份实现），并把文案换成可行动的。
    handleSessionExpired()
    return {
      ok: false,
      code: 401,
      msg: SESSION_EXPIRED_MSG + '，请重新登录后再操作',
      unauthorized: true
    }
  }
  if (payload.code !== 200) {
    return { ok: false, code: payload.code, msg: payload.msg || '操作失败（code=' + payload.code + '）' }
  }
  return { ok: true, code: 200, msg: payload.msg || 'ok', data: payload.data }
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
