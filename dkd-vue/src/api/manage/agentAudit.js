import request from '@/utils/request'

/**
 * 智能体决策审计 API（排期任务 3-7）
 *
 * 只读：审计留痕由智能体服务写入（AGENTS §6.3），后台**不提供**新增/修改/删除接口。
 * 这不是“还没做”，而是设计取舍——能改的留痕就不是留痕了。
 */

/**
 * 查询决策留痕列表（分页）。
 *
 * @param {Object} query 过滤条件：scene / userId / triggerType / action / targetType /
 *                       targetId / result / requestId / params.beginTime / params.endTime
 */
export function listDecisionLogs(query) {
  return request({
    url: '/manage/agent/decision-log/list',
    method: 'get',
    params: query
  })
}

/**
 * 查询决策留痕详情（**含** input_context / llm_output 两个 JSON 上下文列）。
 *
 * 为什么详情要单独一个接口：列表刻意不带这两个大 JSON 列（该表随每次对话增长，
 * 列表带上它们会让分页越来越慢）。需要看完整决策上下文时再按 id 取一条。
 *
 * @param {number} id 留痕主键
 */
export function getDecisionLog(id) {
  return request({
    url: '/manage/agent/decision-log/' + id,
    method: 'get'
  })
}

/**
 * 按 X-Request-Id 取**整条链路**（升序，含 JSON 上下文）。
 *
 * 与「列表按 requestId 过滤」的区别：列表是倒序分页，看链路时顺序是反的、还要翻页；
 * 审计要回答的恰恰是先后顺序（状态机放行 → 回调建单 → 回写状态）。后端单条 SQL 里
 * `order by id asc` + `limit 50`，并复用详情列片段（因此带 input_context / llm_output）。
 *
 * @param {string} requestId 贯穿网关→Python→回调的请求号（必填，空值后端直接 500 信封拒绝）
 */
export function traceDecisionLog(requestId) {
  return request({
    url: '/manage/agent/decision-log/trace',
    method: 'get',
    params: { requestId }
  })
}
