package com.dkd.manage.service;

import java.util.List;

import com.dkd.manage.domain.AgentDecisionLog;

/**
 * 智能体决策留痕Service接口（排期任务 3-7）。
 *
 * @author ruoyi
 * @date 2026-10-01
 */
public interface IAgentDecisionLogService
{
    /**
     * 查询决策留痕列表（不含 JSON 上下文列）。
     *
     * @param log 过滤条件
     */
    List<AgentDecisionLog> selectAgentDecisionLogList(AgentDecisionLog log);

    /**
     * 查询决策留痕详情（含 JSON 上下文列，供审计“回放”查看）。
     *
     * @param id 主键
     */
    AgentDecisionLog selectAgentDecisionLogById(Long id);

    /**
     * 按 request_id 取整条链路（**升序**，含 JSON 上下文）。
     *
     * @param requestId 贯穿网关→Python→回调的请求号
     */
    List<AgentDecisionLog> selectTraceByRequestId(String requestId);
}
