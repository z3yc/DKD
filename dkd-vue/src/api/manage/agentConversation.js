import request from '@/utils/request'

/**
 * 智能体会话 API（排期任务 2-5 / 4-1，页面 S13）。
 *
 * 只读：会话与消息由 dkd-agent 写入 `agent_conversation` / `agent_message`
 * （复用现有两表，不新建 agent_chat_* 表——计划表 ⚠1 已裁定复用，避免双写与迁移）。
 * 后台只查询，不提供修改与删除：会话内容是“用户与智能体到底说了什么”的事实记录。
 */

/**
 * 查询会话列表（分页，含消息数/工具调用数/token 聚合）。
 *
 * 后端契约（java-log）：按 conversation 聚合 message，列表 VO 直接给出
 * messageCount / toolCallCount / tokensIn / tokensOut，**列表不做 N+1**
 * （每行再查一次消息表会把分页变成 N+1 查询，AGENTS §10 CR 重点项）。
 *
 * @param {Object} query scene、status、userId、beginTime、endTime、pageNum、pageSize
 */
export function listAgentConversation(query) {
  return request({
    url: '/manage/agent/conversation/list',
    method: 'get',
    params: query
  })
}

/**
 * 查询会话详情：会话头 + 消息时间线（AjaxResult，data = { conversation, messages }）。
 *
 * 为什么详情一次性带 messages：打开抽屉就要整段回放（S13 交互），
 * 拆成“先会话头再消息”会让抽屉先渲染空壳再跳一次，且中间窗口容易被误读成“没有消息”。
 * messages 按 seq 升序；单条 content 超过 2000 字由后端截断并置 `truncated=true`，
 * 页面据此提示“内容已截断”，不把截断后的内容当成完整记录展示。
 *
 * @param {string} conversationId 会话ID（进 URL 路径前编码：VO 里是 UUID，但约定不断言格式）
 */
export function getAgentConversation(conversationId) {
  return request({
    url: '/manage/agent/conversation/' + encodeURIComponent(conversationId),
    method: 'get'
  })
}
