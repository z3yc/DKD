package com.dkd.manage.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentReadQuerySupport;
import com.dkd.manage.domain.AgentConversation;
import com.dkd.manage.domain.vo.AgentConversationDetailVo;
import com.dkd.manage.domain.vo.AgentConversationVo;
import com.dkd.manage.service.IAgentConversationService;

import java.util.List;

/**
 * 智能体会话只读 Controller（Batch A · 排期 2-5，表 {@code agent_conversation} / {@code agent_message}）。
 *
 * <p>路径前缀 {@code /manage/agent/conversation}，**不经 {@code /agent/**} 网关**。
 *
 * <p><b>为什么只有 GET：</b>会话是审计与成本计量的依据（token 按消息聚合），
 * 后台提供编辑入口会让"谁在什么时候聊了什么"失去可信度。需要清理时走
 * 数据库运维流程（排期 4-4 的保留策略），不在这里开口子。
 *
 * <p><b>详情为什么不在 Service 里抛"不存在"：</b>与 {@code AgentRunController#getInfo}
 * 口径一致——Service 返回"头为空 + 空时间线"的结构，由 Controller 决定转成
 * {@code AjaxResult.error}。这样 Service 的单测不需要依赖 Web 层的异常约定。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/conversation")
public class AgentConversationController extends BaseController
{
    /** conversation_id 列宽 64（agent_conversation.conversation_id / agent_message.conversation_id） */
    private static final int MAX_CONVERSATION_ID_LENGTH = 64;

    @Autowired
    private IAgentConversationService agentConversationService;

    /**
     * 会话列表（分页）。
     *
     * <p>查询参数：{@code scene/status/userId}（精确）、{@code beginTime/endTime}
     * （作用在 {@code agent_conversation.create_time}，缺省近 24h、最大 30 天）、
     * RuoYi 分页参数（{@code pageSize <= 200}）。
     *
     * <p>与日志页相同的顺序要求：先校验时间窗，再 startPage（理由见
     * {@code AgentReadQuerySupport#startBoundedPage}）。
     *
     * @param query 过滤条件
     * @return 分页列表（含消息/token 聚合，按 create_time 倒序）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConversation:list')")
    @GetMapping("/list")
    public TableDataInfo list(AgentConversation query)
    {
        agentConversationService.resolveWindow(query);
        AgentReadQuerySupport.startBoundedPage();
        List<AgentConversationVo> list = agentConversationService.selectAgentConversationList(query);
        return getDataTable(list);
    }

    /**
     * 会话详情：会话头 + 消息时间线（按 seq 升序）。
     *
     * @param conversationId 业务会话ID（= LangGraph thread_id）
     * @return 详情；不存在返回业务错误而不是空对象（空对象会让页面显示一个"空会话"）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConversation:query')")
    @GetMapping("/{conversationId}")
    public AjaxResult getInfo(@PathVariable("conversationId") String conversationId)
    {
        // 参数校验：超长/空值直接拒绝，不拿它去查库（列宽 64，查不到纯属浪费一次 IO）
        String id = StringUtils.trim(conversationId);
        if (StringUtils.isEmpty(id) || id.length() > MAX_CONVERSATION_ID_LENGTH)
        {
            return AjaxResult.error("conversationId 不合法（长度 1~64）：" + conversationId);
        }
        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(id);
        if (detail.getConversation() == null)
        {
            return AjaxResult.error("会话不存在：" + id);
        }
        return AjaxResult.success(detail);
    }
}
