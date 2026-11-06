package com.dkd.manage.mapper;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.domain.AgentConversation;
import com.dkd.manage.domain.AgentMessage;
import com.dkd.manage.domain.vo.AgentConversationVo;

/**
 * 智能体会话/消息 Mapper（Batch A · 排期 2-5，表 {@code agent_conversation} / {@code agent_message}）。
 *
 * <p><b>刻意只有查询方法：</b>会话与消息由智能体服务写入（AGENTS §7.3：{@code agent_*}
 * 是智能体自有数据，但管理端只做审计展示）。后台开写入口会让"谁在什么时候聊了什么"
 * 失去可信度——与 {@code agent_decision_log} 同一条纪律。
 *
 * <p><b>聚合为什么单独一个方法、按"当前页的 conversationId 列表"查：</b>
 * 若把 {@code group by conversation_id} 的子查询直接 join 进列表 SQL，
 * 每次翻页都要对整张 {@code agent_message} 聚合一次（单主库无只读从库，AGENTS §1 约束 5）。
 * 先分页拿到 ≤200 个会话 ID，再 {@code where conversation_id in (...)} 聚合，
 * 代价与页大小成正比、与消息总量无关，且仍是一条 SQL（不是循环内查库的 N+1）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentConversationMapper
{
    /**
     * 会话列表（按 create_time 倒序，分页由 PageHelper 负责）。
     *
     * <p>这里只返回会话表列；消息/token 聚合由
     * {@link #selectAgentMessageAggregates} 按当前页 ID 补查后合并。
     *
     * @param query  过滤条件（scene/status/userId）
     * @param window 已归一化并校验过的时间窗（作用在 agent_conversation.create_time）
     * @return 会话头列表
     */
    List<AgentConversationVo> selectAgentConversationList(@Param("query") AgentConversation query,
            @Param("window") AgentQueryWindow window);

    /**
     * 按业务会话ID取单个会话头（不含消息聚合）。
     *
     * @param conversationId 业务会话ID（= LangGraph thread_id）
     * @return 会话头；不存在返回 null
     */
    AgentConversationVo selectAgentConversationByConversationId(@Param("conversationId") String conversationId);

    /**
     * 按会话ID批量聚合消息统计（实测条数/tool_calls 非空条数/token 合计/最后消息时间）。
     *
     * @param conversationIds 会话ID列表（非空；调用方负责保证不超过一页）
     * @return 聚合行；没有消息的会话不会出现在结果里（Service 补 0/null）
     */
    List<AgentConversationVo> selectAgentMessageAggregates(@Param("conversationIds") List<String> conversationIds);

    /**
     * 取一个会话的消息时间线（按 seq 升序，最多 limit 条）。
     *
     * <p>为什么必须有 limit：长会话可能上千条消息，无界查询在单主库上是红线
     * （AGENTS §10）。被截断时由 Service 在详情出参里标 {@code messagesTruncated}。
     *
     * @param conversationId 业务会话ID
     * @param limit          条数上限（服务端常量，不接受前端指定）
     * @return 消息列表
     */
    List<AgentMessage> selectAgentMessagesByConversationId(@Param("conversationId") String conversationId,
            @Param("limit") int limit);
}
