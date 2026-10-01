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
