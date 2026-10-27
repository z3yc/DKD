package com.dkd.manage.service;

import java.util.List;
import java.util.Map;

import com.dkd.manage.domain.AgentRun;
import com.dkd.manage.domain.AgentStep;

/**
 * 智能体运行观测Service（F-03）。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
public interface IAgentRunService
{
    /**
     * 查询运行列表。
     *
     * @param query 过滤条件
     * @return 运行列表
     */
    List<AgentRun> selectAgentRunList(AgentRun query);

    /**
     * 按 runId 查运行详情。
     *
     * @param runId 运行ID
     * @return 运行记录；不存在返回 null
     */
    AgentRun selectAgentRunByRunId(String runId);

    /**
     * 查一次运行的轮次，并把每轮的工具调用挂到 `toolCalls` 上。
     *
     * @param runId 运行ID
     * @return 轮次列表（按 step_no 升序）
     */
    List<AgentStep> selectStepsWithToolCalls(String runId);

    /**
     * 时间线：一次运行的完整视图（run + steps + toolCalls）。
     *
     * <p>返回值用 Map 而不是新建 VO：这是**只读聚合**，字段完全来自三张表，
     * 造一个只有 getter/setter 的 VO 不会增加任何约束，反而多一处要同步的字段表。
     * 若将来要加派生字段（如总耗时、重试次数），再升级为 VO。
     *
     * @param runId 运行ID
     * @return `{run: AgentRun|null, steps: List<AgentStep>}`；run 不存在时 steps 为空列表
     */
    Map<String, Object> selectTimeline(String runId);
}
