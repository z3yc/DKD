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

    /**
     * 按 {@code request_id} 取**整条链路**（3-7 决策审计页的“最后一公里”）。
     *
     * <p>为什么要单独一个方法而不是“列表按 requestId 过滤”：列表是 {@code order by id desc} 且分页，
     * 把同一条链路的**时间顺序打反了**；而审计要看的正是先后顺序
     * （状态机放行 → 回调建单 → 回写状态）。这里**升序**返回。
     *
     * @param requestId 贯穿网关→Python→回调的请求号（同一请求的多次留痕共用）
     */
    List<AgentDecisionLog> selectAgentDecisionLogTrace(String requestId);
}
