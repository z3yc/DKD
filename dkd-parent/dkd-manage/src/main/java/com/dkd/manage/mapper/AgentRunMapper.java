package com.dkd.manage.mapper;

import java.util.List;

import com.dkd.manage.domain.AgentRun;
import com.dkd.manage.domain.AgentStep;
import com.dkd.manage.domain.AgentToolCall;

/**
 * 智能体运行观测Mapper（F-03）。
 *
 * <p><b>刻意只有查询方法</b>：三张运行表由 Python 智能体服务写入（过程数据，属智能体自有数据，
 * AGENTS §7.3），管理端只读。给后台开写接口等于让"过程记录"可以被改，它就不再是证据。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
public interface AgentRunMapper
{
    /**
     * 运行列表（按开始时间倒序，分页由 PageHelper 负责）。
     *
     * @param query 过滤条件；时间范围走 {@code BaseEntity.params.beginTime/endTime}（作用在 started_at 上）
     * @return 运行列表
     */
    List<AgentRun> selectAgentRunList(AgentRun query);

    /**
     * 按业务主键 runId 取单条运行。
     *
     * @param runId 运行ID
     * @return 运行记录；不存在返回 null
     */
    AgentRun selectAgentRunByRunId(String runId);

    /**
     * 取一次运行的全部轮次（按 step_no 升序）。
     *
     * @param runId 运行ID
     * @return 轮次列表
     */
    List<AgentStep> selectAgentStepsByRunId(String runId);

    /**
     * 取一次运行的全部工具调用（按 step_no、call_seq 升序）。
     *
     * <p>为什么一次性取全部而不是按轮次查：一次运行通常只有十几条，一条 SQL 取回后在 Service 里分组，
     * 避免页面展开时出现 N+1 查询（AGENTS §10 明确把"循环内查库"列为重点检查项）。
     *
     * @param runId 运行ID
     * @return 工具调用列表
     */
    List<AgentToolCall> selectAgentToolCallsByRunId(String runId);
}
