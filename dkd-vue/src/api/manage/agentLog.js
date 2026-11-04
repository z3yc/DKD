import request from '@/utils/request'

/**
 * 智能体执行日志 API（排期任务 2-4，页面 S6）。
 *
 * 只读：日志由 dkd-agent 的 logging.Handler 异步写入 `agent_log`
 * （智能体自有表，AGENTS §7.3），后台**不提供任何写接口**——
 * 日志一旦可改可删，就不能再回答“当时到底报了什么”。
 */

/**
 * 查询执行日志列表（分页）。
 *
 * 后端契约（java-log）：时间窗是**强制**的——不传 beginTime/endTime 时按近 24h 兜底，
 * 最大 30 天（单主库护栏，AGENTS §1 约束 5：分析类/批量查询必须限定时间窗口）。
 * 因此页面每次查询都会显式带上时间范围，不做“不带时间全表翻页”。
 *
 * @param {Object} query beginTime / endTime（yyyy-MM-dd HH:mm:ss）、level、status、
 *                       runId、requestId、keyword（匹配 message/error_msg）、pageNum、pageSize
 */
export function listAgentLog(query) {
  return request({
    url: '/manage/agent/log/list',
    method: 'get',
    params: query
  })
}
