import request from '@/utils/request'

/**
 * 智能体模型档案 API（排期任务 3-5，页面 S14）。
 *
 * 安全红线（AGENTS §7.1）：`apiKeyEnv` 只存**环境变量名**（如 DKD_DEEPSEEK_API_KEY），
 * 密钥值只存在于部署环境/.env；因此：
 *  - 后端响应里没有任何密钥字段，本层也**不提供**任何密钥读写接口；
 *  - 页面没有密钥输入框——有输入框就会有人把密钥粘进去，库一旦落过密钥就再也洗不掉。
 *
 * 写接口与测试连通带 `silent: true`：失败原因（字段级校验/HTTP 状态/错误类型）
 * 需要在抽屉或结果弹窗里持久展示，交给拦截器只会闪一条 toast。
 */

/**
 * 查询模型档案清单（数量很少，后端不分页）。
 *
 * @returns AjaxResult：data = [{ id, code, name, provider, baseUrl, model, apiKeyEnv,
 *            timeoutS, maxRetries, enabled, isDefault, description }]
 */
export function listAgentLlmProfiles() {
  return request({
    url: '/manage/agent/llm/profile/list',
    method: 'get'
  })
}

/**
 * 新增模型档案。
 *
 * @param {Object} data { code, name, provider, baseUrl, model, apiKeyEnv,
 *                        timeoutS, maxRetries, enabled, isDefault, description }
 */
export function addAgentLlmProfile(data) {
  return request({
    url: '/manage/agent/llm/profile',
    method: 'post',
    data,
    silent: true
  })
}

/**
 * 编辑模型档案（code 创建后不可改；改动下一次调用生效）。
 *
 * @param {number} id 档案主键
 * @param {Object} data 同新增字段（不含 id）
 */
export function updateAgentLlmProfile(id, data) {
  return request({
    url: '/manage/agent/llm/profile/' + id,
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 启用 / 停用模型档案。
 *
 * 注意与「默认档案」是两回事：停用只影响可用性，`isDefault` 是回落到哪个档案的语义，
 * 由后端保证「最多一个默认」；停用默认档案时后端可能会拒绝或要求先改默认。
 *
 * @param {number} id 档案主键
 * @param {Object} data { enabled: 0|1 }
 */
export function setAgentLlmProfileEnabled(id, data) {
  return request({
    url: '/manage/agent/llm/profile/' + id + '/enabled',
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 测试连通（服务端用该档案的 connection 信息做一次最小探测，并写探测记录）。
 *
 * 探测**不修改档案**，因此“测失败”不等于“档案坏了”：结果里同时给
 * httpStatus / latencyMs / modelServed / errorType / errorMsg，便于区分
 * 鉴权失败、网络不可达、模型名不存在这几类完全不同的原因。
 *
 * @param {number} id 档案主键
 * @returns AjaxResult：data = { ok, latencyMs, modelServed, errorType, errorMsg, httpStatus }
 */
export function probeAgentLlmProfile(id) {
  return request({
    url: '/manage/agent/llm/profile/' + id + '/probe',
    method: 'post',
    silent: true
  })
}

/**
 * 查询某档案的探测记录（分页，倒序）。
 *
 * @param {number} id 档案主键
 * @param {Object} query { pageNum, pageSize }
 * @returns TableDataInfo：{ total, rows }；rows 字段见 agent_llm_probe（camelCase）
 */
export function listAgentLlmProbes(id, query) {
  return request({
    url: '/manage/agent/llm/profile/' + id + '/probes',
    method: 'get',
    params: query
  })
}
