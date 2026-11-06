package com.dkd.manage.service;

import java.util.List;

import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.domain.AgentConversation;
import com.dkd.manage.domain.vo.AgentConversationDetailVo;
import com.dkd.manage.domain.vo.AgentConversationVo;

/**
 * 智能体会话只读 Service（Batch A · 排期 2-5）。
 *
 * <p>只读：会话/消息由智能体服务写入，管理端只做审计与排障展示（AGENTS §7.3）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentConversationService
{
    /**
     * 校验并归一化查询时间窗（作用在 {@code agent_conversation.create_time}；规则同日志页）。
     *
     * @param query 查询对象
     * @return 归一化后的时间窗
     */
    AgentQueryWindow resolveWindow(AgentConversation query);

    /**
     * 会话列表（分页由调用方在 Controller 里 startPage）。
     *
     * @param query 过滤条件（scene/status/userId + 时间窗原文）
     * @return 会话头列表（含消息/token 聚合）
     */
    List<AgentConversationVo> selectAgentConversationList(AgentConversation query);

    /**
     * 会话详情：会话头 + 消息时间线（按 seq 升序，最多服务端上限条）。
     *
     * @param conversationId 业务会话ID
     * @return 详情；会话不存在时 {@code conversation} 为 null 且 messages 为空列表
     *         （由 Controller 转成业务错误，与 AgentRunController 的口径一致）
     */
    AgentConversationDetailVo selectConversationDetail(String conversationId);
}
