import request from '@/utils/request'

/**
 * 智能体运行时间线 API（F-04，补货 / 故障 / 对话共用一套）。
 *
 * 只读：agent_run / agent_step / agent_tool_call 三张表由 dkd-agent 写入
 * （智能体自有数据，AGENTS §7.3），管理端只查询、不提供任何写接口——
 * 时间线是“过程证据”，能改就不再是证据了。
 */

/**
 * 查询运行列表（分页）。
 *
 * 返回 TableDataInfo：{ total, rows }。rows 为 run 行（字段 camelCase），
 * 不含 step/toolCall，逐轮回放走 getAgentRun。
 *
 * @param {Object} query sceneCode / status / subjectId / requestId /
 *                       params.beginTime / params.endTime / pageNum / pageSize
 */
export function listAgentRuns(query) {
  return request({
    url: '/manage/agent/run/list',
    method: 'get',
    params: query
  })
}

/**
 * 查询一次运行及其全部轮次（AjaxResult，data = { run, steps }）。
 *
 * 为什么详情要一次性带 steps：时间线页打开抽屉就要整段回放，
 * 分成“先 run 再 steps”两个请求会让抽屉先渲染空壳再跳一次。
 *
 * @param {string} runId 运行ID（Python 生成，进 URL 路径前编码，避免特殊字符截断路由）
 */
export function getAgentRun(runId) {
  return request({
    url: '/manage/agent/run/' + encodeURIComponent(runId),
    method: 'get'
  })
}

/**
 * 只取某一轮次列表（AjaxResult，data = [step...]，step 内含 toolCalls）。
 *
 * 与 getAgentRun 的 steps 是同一份数据；留给“只需刷新轮次”的场景
 * （例如将来页面做增量刷新），避免重复拉运行概要。
 *
 * @param {string} runId 运行ID
 */
export function listAgentRunSteps(runId) {
  return request({
    url: '/manage/agent/run/' + encodeURIComponent(runId) + '/steps',
    method: 'get'
  })
}

/**
 * 发起一次故障诊断（F-09c）。
 *
 * 注意这与上面三个**只读**接口不同：它走 Java 智能体网关 `/agent/fault/runs`
 * （不是 `/manage/**`），后端要求 `manage:vm:query`；同步执行，返回 runId 与派单提案。
 *
 * @param {Object} data { innerCode, windowHours }
 */
export function startFaultRun(data) {
  return request({
    url: '/agent/fault/runs',
    method: 'post',
    data
  })
}

/**
 * 确认 / 驳回一次派单提案（F-09c）。
 *
 * 这是**写操作**：确认会经 Java 回调创建真实维修工单（后端要求 `manage:task:add`），
 * 幂等——重复确认返回同一工单，不会建出第二张。驳回必须带 remark（原因）。
 *
 * @param {string} runId 运行ID
 * @param {Object} data { action: 'confirm'|'reject', assigneeId?, priority?, remark? }
 */
export function confirmFaultRun(runId, data) {
  return request({
    url: '/agent/fault/runs/' + encodeURIComponent(runId) + '/confirm',
    method: 'post',
    data
  })
}
