package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.agent.AgentReadQuerySupport;
import com.dkd.manage.domain.AgentConversation;
import com.dkd.manage.domain.AgentMessage;
import com.dkd.manage.domain.vo.AgentConversationDetailVo;
import com.dkd.manage.domain.vo.AgentConversationVo;
import com.dkd.manage.domain.vo.AgentMessageVo;
import com.dkd.manage.mapper.AgentConversationMapper;
import com.dkd.manage.service.IAgentConversationService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * 智能体会话只读 Service 实现（Batch A · 排期 2-5）。
 *
 * <p>本类做三件"必须加工"的事，其余一律透传：
 * <ol>
 *   <li><b>时间窗护栏</b>：与日志页同一套（默认近 24h、最大 30 天），作用在会话 create_time；</li>
 *   <li><b>聚合合并</b>：先分页拿会话头，再按当前页 ID 批量聚合 {@code agent_message}
 *       （不用全表 group by，见 Mapper 注释）；</li>
 *   <li><b>出参加工</b>：正文按 2000 字截断、{@code tool_calls} JSON 容错解析。</li>
 * </ol>
 *
 * <p><b>为什么解析容错必须做在这里而不是交给前端：</b>{@code tool_calls} 是历史 JSON 文本，
 * 一旦损坏（早期版本/人工修库），前端 JSON.parse 会抛异常，整页白屏；而"这条消息的
 * 工具调用原始文本损坏"本身是排障信息，应该被展示出来而不是让整段会话不可看。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentConversationServiceImpl implements IAgentConversationService
{
    /** 详情接口单次返回的消息条数上限（长会话不能无界返回，单主库护栏） */
    public static final int MAX_DETAIL_MESSAGES = 500;

    /** 消息正文出参截断长度（截断标记见 {@code AgentMessageVo#getTruncated()}） */
    public static final int MAX_CONTENT_LENGTH = 2000;

    /** JSON 读写（线程安全；与 AgentConfigServiceImpl 同一做法，不需要 Spring 的 ObjectMapper 定制） */
    private static final ObjectMapper JSON = new ObjectMapper();

    @Autowired
    private AgentConversationMapper agentConversationMapper;

    @Override
    public AgentQueryWindow resolveWindow(AgentConversation query)
    {
        return AgentReadQuerySupport.resolveWindow(query);
    }

    @Override
    public List<AgentConversationVo> selectAgentConversationList(AgentConversation query)
    {
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow(query);
        List<AgentConversationVo> rows = agentConversationMapper.selectAgentConversationList(query, window);
        if (rows == null || rows.isEmpty())
        {
            // 空页不查聚合：既省一次 SQL，也避免 IN () 这种非法语句
            return rows == null ? new ArrayList<AgentConversationVo>() : rows;
        }
        List<String> conversationIds = new ArrayList<String>(rows.size());
        for (AgentConversationVo row : rows)
        {
            if (StringUtils.isNotEmpty(row.getConversationId()))
            {
                conversationIds.add(row.getConversationId());
            }
        }
        List<AgentConversationVo> aggregates = conversationIds.isEmpty()
                ? new ArrayList<AgentConversationVo>()
                : agentConversationMapper.selectAgentMessageAggregates(conversationIds);
        mergeAggregates(rows, aggregates);
        return rows;
    }

    @Override
    public AgentConversationDetailVo selectConversationDetail(String conversationId)
    {
        AgentConversationDetailVo detail = new AgentConversationDetailVo();
        detail.setMessages(new ArrayList<AgentMessageVo>());
        detail.setMessageLimit(Integer.valueOf(MAX_DETAIL_MESSAGES));
        detail.setMessagesTruncated(Boolean.FALSE);
        if (StringUtils.isEmpty(conversationId))
        {
            return detail;
        }
        String id = conversationId.trim();

        AgentConversationVo head = agentConversationMapper.selectAgentConversationByConversationId(id);
        if (head == null)
        {
            // 不存在：conversation 留 null，由 Controller 转业务错误（与 AgentRunController 口径一致）
            return detail;
        }
        List<AgentConversationVo> aggregates = agentConversationMapper
                .selectAgentMessageAggregates(Collections.singletonList(id));
        mergeAggregates(Collections.singletonList(head), aggregates);

        List<AgentMessage> messages = agentConversationMapper
                .selectAgentMessagesByConversationId(id, MAX_DETAIL_MESSAGES);
        List<AgentMessageVo> messageVos = new ArrayList<AgentMessageVo>();
        if (messages != null)
        {
            for (AgentMessage message : messages)
            {
                messageVos.add(toMessageVo(message));
            }
        }
        detail.setConversation(head);
        detail.setMessages(messageVos);
        // 截断判断用**聚合出的实测条数**：拿"本次返回的条数 == 上限"来猜会把
        // "恰好 500 条"误报成截断，也会在 cap 生效时漏报
        Integer actual = head.getActualMessageCount();
        detail.setMessagesTruncated(Boolean.valueOf(
                actual != null && actual.intValue() > messageVos.size()));
        return detail;
    }

    /**
     * 把聚合行合并到会话头上（没有聚合行 = 该会话没有消息，补 0/null 而不是留 null）。
     *
     * @param heads      会话头列表（会被就地修改）
     * @param aggregates 聚合行列表（可 null / 空）
     */
    private void mergeAggregates(List<AgentConversationVo> heads, List<AgentConversationVo> aggregates)
    {
        Map<String, AgentConversationVo> byConversationId = new HashMap<String, AgentConversationVo>();
        if (aggregates != null)
        {
            for (AgentConversationVo aggregate : aggregates)
            {
                if (aggregate != null && StringUtils.isNotEmpty(aggregate.getConversationId()))
                {
                    byConversationId.put(aggregate.getConversationId(), aggregate);
                }
            }
        }
        for (AgentConversationVo head : heads)
        {
            AgentConversationVo aggregate = byConversationId.get(head.getConversationId());
            if (aggregate == null)
            {
                // 没有消息的会话也给出确定的 0/null，前端不需要到处判空
                head.setActualMessageCount(Integer.valueOf(0));
                head.setToolCallsCount(Integer.valueOf(0));
                head.setTokensIn(Long.valueOf(0L));
                head.setTokensOut(Long.valueOf(0L));
                head.setLastMessageTime(null);
                continue;
            }
            head.setActualMessageCount(nvl(aggregate.getActualMessageCount()));
            head.setToolCallsCount(nvl(aggregate.getToolCallsCount()));
            head.setTokensIn(aggregate.getTokensIn() == null ? Long.valueOf(0L) : aggregate.getTokensIn());
            head.setTokensOut(aggregate.getTokensOut() == null ? Long.valueOf(0L) : aggregate.getTokensOut());
            head.setLastMessageTime(aggregate.getLastMessageTime());
        }
    }

    /**
     * 实体 → 出参：正文截断 + 工具调用 JSON 容错解析。
     *
     * @param message 消息实体
     * @return 出参
     */
    private AgentMessageVo toMessageVo(AgentMessage message)
    {
        AgentMessageVo vo = new AgentMessageVo();
        vo.setId(message.getId());
        vo.setSeq(message.getSeq());
        vo.setMsgRole(message.getMsgRole());
        vo.setModel(message.getModel());
        vo.setTokensIn(message.getTokensIn());
        vo.setTokensOut(message.getTokensOut());
        vo.setLatencyMs(message.getLatencyMs());
        vo.setRequestId(message.getRequestId());
        vo.setCreateTime(message.getCreateTime());
        fillContent(message.getContent(), vo);
        fillToolCalls(message.getToolCalls(), vo);
        return vo;
    }

    /**
     * 正文截断（按 2000 字）+ 原始长度/截断标记。
     *
     * <p>为什么截断要避开代理对：Java 的 {@code String} 是按 UTF-16 计数的，
     * 第 2000 个字符恰好落在一个 emoji 的高位代理上时，直接 {@code substring(0, 2000)}
     * 会切出半个字符（前端显示成乱码/方块）。这里宁可少一个字符，也不切断。
     *
     * @param content 正文原文
     * @param vo      出参
     */
    private void fillContent(String content, AgentMessageVo vo)
    {
        if (content == null)
        {
            vo.setContent(null);
            vo.setContentLength(Integer.valueOf(0));
            vo.setTruncated(Boolean.FALSE);
            return;
        }
        vo.setContentLength(Integer.valueOf(content.length()));
        if (content.length() <= MAX_CONTENT_LENGTH)
        {
            vo.setContent(content);
            vo.setTruncated(Boolean.FALSE);
            return;
        }
        int cut = MAX_CONTENT_LENGTH;
        if (Character.isHighSurrogate(content.charAt(cut - 1)))
        {
            cut = cut - 1;
        }
        vo.setContent(content.substring(0, cut));
        vo.setTruncated(Boolean.TRUE);
    }

    /**
     * {@code tool_calls} JSON 容错解析。
     *
     * <p>成功：{@code toolCalls} = 解析后的对象（数组/对象），并给出条数；
     * 失败：**不抛异常**，回填原文与异常类型，条数记 0。
     *
     * @param raw 原始 JSON 文本
     * @param vo  出参
     */
    private void fillToolCalls(String raw, AgentMessageVo vo)
    {
        if (StringUtils.isEmpty(raw))
        {
            vo.setToolCalls(null);
            vo.setToolCallCount(Integer.valueOf(0));
            return;
        }
        try
        {
            Object parsed = JSON.readValue(raw, Object.class);
            vo.setToolCalls(parsed);
            vo.setToolCallCount(Integer.valueOf(countToolCalls(parsed)));
        }
        catch (JsonProcessingException e)
        {
            // 容错是刻意的：一条损坏的历史 JSON 不能把整个会话详情打成 500。
            // 只回填异常类型（不回填异常 message 里的原文片段），原文放在 toolCallsRaw 供人工修复
            vo.setToolCalls(null);
            vo.setToolCallsRaw(raw);
            vo.setToolCallsParseError(e.getClass().getSimpleName());
            vo.setToolCallCount(Integer.valueOf(0));
        }
    }

    /**
     * 统计一条消息里的工具调用条数。
     *
     * <p>兼容两种形态：数组（Python 当前写入的形态）与对象（可能是
     * {@code {"calls": [...]}} 这种包装）。无法识别的标量按 1 计。
     *
     * @param parsed 解析后的 JSON 对象
     * @return 条数
     */
    private int countToolCalls(Object parsed)
    {
        if (parsed == null)
        {
            return 0;
        }
        if (parsed instanceof List)
        {
            return ((List<?>) parsed).size();
        }
        if (parsed instanceof Map)
        {
            Map<?, ?> map = (Map<?, ?>) parsed;
            Object calls = map.get("calls");
            if (calls instanceof List)
            {
                return ((List<?>) calls).size();
            }
            return map.isEmpty() ? 0 : 1;
        }
        return 1;
    }

    private Integer nvl(Integer value)
    {
        return value == null ? Integer.valueOf(0) : value;
    }
}
