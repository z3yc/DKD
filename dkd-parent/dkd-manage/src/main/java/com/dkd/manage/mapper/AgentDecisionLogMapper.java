package com.dkd.manage.mapper;

import java.util.List;

import com.dkd.manage.domain.AgentDecisionLog;

/**
 * 智能体决策留痕Mapper接口（排期任务 3-7 决策审计页）。
 *
 * <p><b>刻意只有查询方法</b>：留痕是审计证据（AGENTS §6.3），写入方只有智能体服务。
 * 后台不提供 insert/update/delete —— 能改的留痕不是留痕。
 *
 * @author ruoyi
 * @date 2026-10-01
 */
public interface AgentDecisionLogMapper
{
    /**
     * 列表查询（**不含** input_context / llm_output 两个 JSON 列）。
     *
     * @param log 过滤条件；时间范围走 {@code BaseEntity.params.beginTime/endTime}
     */
    List<AgentDecisionLog> selectAgentDecisionLogList(AgentDecisionLog log);

    /**
     * 详情查询（**含**两个 JSON 列，供“回放”查看决策上下文）。
     */
    AgentDecisionLog selectAgentDecisionLogById(Long id);
}
