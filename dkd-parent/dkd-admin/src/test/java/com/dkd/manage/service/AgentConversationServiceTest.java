package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.common.exception.ServiceException;
import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.domain.AgentConversation;
import com.dkd.manage.domain.AgentMessage;
import com.dkd.manage.domain.vo.AgentConversationDetailVo;
import com.dkd.manage.domain.vo.AgentConversationVo;
import com.dkd.manage.domain.vo.AgentMessageVo;
import com.dkd.manage.mapper.AgentConversationMapper;
import com.dkd.manage.service.impl.AgentConversationServiceImpl;

/**
 * 会话只读 Service 单测（Batch A · 排期 2-5）。
 *
 * <p>测试边界：Mapper 全部 Mockito 打桩，不连 MySQL（AGENTS §8）。覆盖四类语义：
 * <ol>
 *   <li><b>聚合合并</b>：会话表里的冗余 {@code messageCount} 与实测条数分开返回（计数漂移可见）；</li>
 *   <li><b>空页/空会话</b>：不查聚合、不查消息，且不出现 {@code IN ()} 这类非法 SQL；</li>
 *   <li><b>正文截断</b>：超过 2000 字要标 {@code truncated} 且**不切断 emoji 代理对**；</li>
 *   <li><b>tool_calls 容错</b>：合法 JSON 解析成对象；损坏 JSON 不抛异常、保留原文与错误标记。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentConversationServiceTest
{
    private static final String CONVERSATION_ID = "conv-abc123";

    @Mock
    private AgentConversationMapper agentConversationMapper;

    @InjectMocks
    private AgentConversationServiceImpl agentConversationService;

    private static Date date(String text) throws Exception
    {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").parse(text);
    }

    private static AgentConversationVo head(String conversationId, int messageCount)
    {
        AgentConversationVo vo = new AgentConversationVo();
        vo.setId(Long.valueOf(1L));
        vo.setConversationId(conversationId);
        vo.setUserId(Long.valueOf(7L));
        vo.setUserName("运营小王");
        vo.setRegionId(Long.valueOf(3L));
        vo.setScene(Integer.valueOf(1));
        vo.setTitle("库存查询");
        vo.setStatus(Integer.valueOf(1));
        vo.setMessageCount(Integer.valueOf(messageCount));
        return vo;
    }

    private static AgentConversationVo aggregate(String conversationId, int actualCount, int toolCallsCount,
            long tokensIn, long tokensOut, Date lastMessageTime)
    {
        AgentConversationVo vo = new AgentConversationVo();
        vo.setConversationId(conversationId);
        vo.setActualMessageCount(Integer.valueOf(actualCount));
        vo.setToolCallsCount(Integer.valueOf(toolCallsCount));
        vo.setTokensIn(Long.valueOf(tokensIn));
        vo.setTokensOut(Long.valueOf(tokensOut));
        vo.setLastMessageTime(lastMessageTime);
        return vo;
    }

    private static AgentMessage message(int seq, int msgRole, String content)
    {
        AgentMessage message = new AgentMessage();
        message.setId(Long.valueOf(seq));
        message.setSeq(Integer.valueOf(seq));
        message.setMsgRole(Integer.valueOf(msgRole));
        message.setContent(content);
        message.setModel("deepseek-chat");
        message.setTokensIn(Integer.valueOf(100));
        message.setTokensOut(Integer.valueOf(50));
        message.setLatencyMs(Integer.valueOf(800));
        message.setRequestId("req-" + seq);
        return message;
    }

    @Test
    @DisplayName("列表：聚合合并到会话头，冗余计数与实测条数同时保留（计数漂移可见）")
    void 列表聚合合并() throws Exception
    {
        AgentConversationVo headA = head("conv-a", 12);
        AgentConversationVo headB = head("conv-b", 3);
        when(agentConversationMapper.selectAgentConversationList(any(AgentConversation.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentConversationVo>(Arrays.asList(headA, headB)));
        when(agentConversationMapper.selectAgentMessageAggregates(anyList()))
                .thenReturn(new ArrayList<AgentConversationVo>(Collections.singletonList(
                        aggregate("conv-a", 9, 2, 1300L, 700L, date("2026-11-04 10:30:00")))));

        List<AgentConversationVo> list = agentConversationService.selectAgentConversationList(new AgentConversation());

        assertThat(list).hasSize(2);
        // conv-a：表内说 12 条、实测 9 条 —— 只给一个数就永远发现不了这种漂移
        assertThat(list.get(0).getMessageCount()).isEqualTo(12);
        assertThat(list.get(0).getActualMessageCount()).isEqualTo(9);
        assertThat(list.get(0).getToolCallsCount()).isEqualTo(2);
        assertThat(list.get(0).getTokensIn()).isEqualTo(1300L);
        assertThat(list.get(0).getTokensOut()).isEqualTo(700L);
        assertThat(list.get(0).getLastMessageTime()).isEqualTo(date("2026-11-04 10:30:00"));
        // conv-b 没有任何消息：给确定的 0/null，前端不必到处判空
        assertThat(list.get(1).getActualMessageCount()).isEqualTo(0);
        assertThat(list.get(1).getToolCallsCount()).isEqualTo(0);
        assertThat(list.get(1).getTokensIn()).isEqualTo(0L);
        assertThat(list.get(1).getLastMessageTime()).isNull();
    }

    @Test
    @DisplayName("列表：聚合只按当前页会话ID批量查（不是全表 group by）")
    void 列表聚合并发只传当前页ID()
    {
        when(agentConversationMapper.selectAgentConversationList(any(AgentConversation.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentConversationVo>(Arrays.asList(head("conv-a", 1), head("conv-b", 1))));
        when(agentConversationMapper.selectAgentMessageAggregates(anyList()))
                .thenReturn(new ArrayList<AgentConversationVo>());

        agentConversationService.selectAgentConversationList(new AgentConversation());

        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<String>> idsCaptor = ArgumentCaptor.forClass(List.class);
        verify(agentConversationMapper).selectAgentMessageAggregates(idsCaptor.capture());
        assertThat(idsCaptor.getValue()).containsExactly("conv-a", "conv-b");
    }

    @Test
    @DisplayName("列表：空页不查聚合（也不产生 IN () 非法语句）")
    void 空页不查聚合()
    {
        when(agentConversationMapper.selectAgentConversationList(any(AgentConversation.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentConversationVo>());

        List<AgentConversationVo> list = agentConversationService.selectAgentConversationList(new AgentConversation());

        assertThat(list).isEmpty();
        verify(agentConversationMapper, never()).selectAgentMessageAggregates(anyList());
    }

    @Test
    @DisplayName("列表：时间窗超过 30 天在查库前拒绝")
    void 列表时间窗超限拒绝()
    {
        AgentConversation query = new AgentConversation();
        query.setBeginTime("2026-01-01");
        query.setEndTime("2026-11-04");

        assertThatThrownBy(() -> agentConversationService.selectAgentConversationList(query))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("最大 30 天");
        verify(agentConversationMapper, never())
                .selectAgentConversationList(any(AgentConversation.class), any(AgentQueryWindow.class));
    }

    @Test
    @DisplayName("详情：会话不存在时返回 null 头 + 空时间线，且不再查消息")
    void 详情不存在时返回空结构()
    {
        when(agentConversationMapper.selectAgentConversationByConversationId("missing")).thenReturn(null);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail("missing");

        assertThat(detail.getConversation()).isNull();
        assertThat(detail.getMessages()).isNotNull().isEmpty();
        assertThat(detail.getMessageLimit()).isEqualTo(AgentConversationServiceImpl.MAX_DETAIL_MESSAGES);
        assertThat(detail.getMessagesTruncated()).isFalse();
        verify(agentConversationMapper, never()).selectAgentMessagesByConversationId(anyString(), anyInt());
    }

    @Test
    @DisplayName("详情：消息按 seq 映射，正文超过 2000 字截断并标记")
    void 详情正文超长截断()
    {
        StringBuilder longContent = new StringBuilder();
        for (int i = 0; i < 2100; i++)
        {
            longContent.append('长');
        }
        stubDetail(message(1, 1, "你好，帮我查一下 A1000001 的库存"),
                message(2, 2, longContent.toString()), 2);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        assertThat(detail.getMessages()).hasSize(2);
        AgentMessageVo shortMessage = detail.getMessages().get(0);
        assertThat(shortMessage.getSeq()).isEqualTo(1);
        assertThat(shortMessage.getMsgRole()).isEqualTo(1);
        assertThat(shortMessage.getTruncated()).isFalse();
        assertThat(shortMessage.getContentLength()).isEqualTo(21);
        assertThat(shortMessage.getRequestId()).isEqualTo("req-1");

        AgentMessageVo longMessage = detail.getMessages().get(1);
        assertThat(longMessage.getTruncated()).isTrue();
        assertThat(longMessage.getContentLength()).isEqualTo(2100);
        assertThat(longMessage.getContent()).hasSize(AgentConversationServiceImpl.MAX_CONTENT_LENGTH);
        // 未超上限 → 不标记截断
        assertThat(detail.getMessagesTruncated()).isFalse();
    }

    @Test
    @DisplayName("详情：截断点恰好落在 emoji 高位代理上时不切断字符（否则前端显示乱码）")
    void 详情截断不切断emoji代理对()
    {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < 1999; i++)
        {
            builder.append('a');
        }
        builder.append("\uD83D\uDE00"); // 😀：高位代理恰好落在第 2000 个字符位置
        for (int i = 0; i < 10; i++)
        {
            builder.append('b');
        }
        stubDetail(message(1, 2, builder.toString()), 1);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        AgentMessageVo vo = detail.getMessages().get(0);
        assertThat(vo.getTruncated()).isTrue();
        // 少截 1 个字符（1999 而不是 2000），也不是把代理对切成半个
        assertThat(vo.getContent()).hasSize(1999);
        assertThat(Character.isHighSurrogate(vo.getContent().charAt(vo.getContent().length() - 1))).isFalse();
    }

    @Test
    @DisplayName("详情：消息条数超过服务端上限时标记 messagesTruncated")
    void 详情消息超过上限时标记截断()
    {
        stubDetail(message(1, 1, "问题"), message(2, 2, "回答"), message(3, 2, "补充"), 1200);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        assertThat(detail.getMessageLimit()).isEqualTo(AgentConversationServiceImpl.MAX_DETAIL_MESSAGES);
        assertThat(detail.getMessagesTruncated()).isTrue();
    }

    @Test
    @DisplayName("详情：查询消息时带上服务端上限（不接受前端指定）")
    void 详情消息查询带服务端上限()
    {
        stubDetail(message(1, 1, "问题"), 1);

        agentConversationService.selectConversationDetail(CONVERSATION_ID);

        verify(agentConversationMapper).selectAgentMessagesByConversationId(CONVERSATION_ID,
                AgentConversationServiceImpl.MAX_DETAIL_MESSAGES);
    }

    @Test
    @DisplayName("详情：tool_calls 合法 JSON 解析为对象并计数")
    void 详情工具调用JSON解析()
    {
        AgentMessage message = message(1, 2, "已查询");
        message.setToolCalls("[{\"name\":\"get_machine_inventory\",\"args\":{\"innerCode\":\"A1000001\"}},"
                + "{\"name\":\"list_order_anomalies\",\"args\":{}}]");
        stubDetail(message, 1);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        AgentMessageVo vo = detail.getMessages().get(0);
        assertThat(vo.getToolCalls()).isInstanceOf(List.class);
        assertThat((List<?>) vo.getToolCalls()).hasSize(2);
        assertThat(vo.getToolCallCount()).isEqualTo(2);
        assertThat(vo.getToolCallsRaw()).isNull();
        assertThat(vo.getToolCallsParseError()).isNull();
    }

    @Test
    @DisplayName("详情：tool_calls 损坏 JSON 不抛异常，保留原文与错误标记（不能一条脏数据锁死整段排障证据）")
    void 详情工具调用JSON损坏时容错()
    {
        AgentMessage message = message(1, 2, "已查询");
        message.setToolCalls("{name: get_machine_inventory, args:");
        stubDetail(message, 1);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        AgentMessageVo vo = detail.getMessages().get(0);
        assertThat(vo.getToolCalls()).isNull();
        assertThat(vo.getToolCallsRaw()).isEqualTo("{name: get_machine_inventory, args:");
        assertThat(vo.getToolCallsParseError()).isNotBlank();
        assertThat(vo.getToolCallCount()).isEqualTo(0);
        // 正文照常返回：损坏的是 tool_calls，不是整条消息
        assertThat(vo.getContent()).isEqualTo("已查询");
    }

    @Test
    @DisplayName("详情：tool_calls 为 null 时给 0 条且不标记错误（正常情况，不是异常）")
    void 详情工具调用为空时不算错误()
    {
        stubDetail(message(1, 1, "你好"), 1);

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        AgentMessageVo vo = detail.getMessages().get(0);
        assertThat(vo.getToolCalls()).isNull();
        assertThat(vo.getToolCallsRaw()).isNull();
        assertThat(vo.getToolCallsParseError()).isNull();
        assertThat(vo.getToolCallCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("详情：会话头也带聚合（token 合计用于详情页展示成本）")
    void 详情会话头带聚合() throws Exception
    {
        when(agentConversationMapper.selectAgentConversationByConversationId(CONVERSATION_ID))
                .thenReturn(head(CONVERSATION_ID, 2));
        when(agentConversationMapper.selectAgentMessageAggregates(anyList()))
                .thenReturn(new ArrayList<AgentConversationVo>(Collections.singletonList(
                        aggregate(CONVERSATION_ID, 2, 1, 300L, 150L, date("2026-11-04 10:30:00")))));
        when(agentConversationMapper.selectAgentMessagesByConversationId(anyString(), anyInt()))
                .thenReturn(new ArrayList<AgentMessage>());

        AgentConversationDetailVo detail = agentConversationService.selectConversationDetail(CONVERSATION_ID);

        assertThat(detail.getConversation()).isNotNull();
        assertThat(detail.getConversation().getConversationId()).isEqualTo(CONVERSATION_ID);
        assertThat(detail.getConversation().getActualMessageCount()).isEqualTo(2);
        assertThat(detail.getConversation().getTokensIn()).isEqualTo(300L);
        assertThat(detail.getConversation().getTitle()).isEqualTo("库存查询");
        // messages 为空但聚合说 2 条 → 必须标记截断（页面要能看出"没显示全"）
        assertThat(detail.getMessagesTruncated()).isTrue();
    }

    /** 打桩"会话头 + 聚合 + 消息"三连查（详情接口的固定调用序列）。 */
    private void stubDetail(AgentMessage message, int actualMessageCount)
    {
        stubDetail(new AgentMessage[] { message }, actualMessageCount);
    }

    private void stubDetail(AgentMessage first, AgentMessage second, int actualMessageCount)
    {
        stubDetail(new AgentMessage[] { first, second }, actualMessageCount);
    }

    private void stubDetail(AgentMessage first, AgentMessage second, AgentMessage third, int actualMessageCount)
    {
        stubDetail(new AgentMessage[] { first, second, third }, actualMessageCount);
    }

    private void stubDetail(AgentMessage[] messages, int actualMessageCount)
    {
        when(agentConversationMapper.selectAgentConversationByConversationId(CONVERSATION_ID))
                .thenReturn(head(CONVERSATION_ID, actualMessageCount));
        when(agentConversationMapper.selectAgentMessageAggregates(anyList()))
                .thenReturn(new ArrayList<AgentConversationVo>(Collections.singletonList(
                        aggregate(CONVERSATION_ID, actualMessageCount, 0, 0L, 0L, null))));
        when(agentConversationMapper.selectAgentMessagesByConversationId(anyString(), anyInt()))
                .thenReturn(new ArrayList<AgentMessage>(Arrays.asList(messages)));
    }
}
