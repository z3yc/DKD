package com.dkd.manage.mapper;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.domain.AgentLog;

/**
 * 智能体执行日志 Mapper（Batch A · 排期 2-4，表 {@code agent_log}）。
 *
 * <p><b>刻意只有查询方法：</b>{@code agent_log} 是智能体服务写入的排障证据（只增不改），
 * 管理端只读。后台能改日志，日志就不再能回答"当时到底报了什么错"（AGENTS §6.3 同一纪律）。
 *
 * <p><b>时间窗为什么是独立参数而不是从 query 里读：</b>窗口必须先经过
 * {@code AgentReadQuerySupport.resolveWindow} 归一化与上限校验（默认近 24h、最大 30 天）。
 * 把"已校验的窗口"作为显式参数传进来，Mapper 层就不可能拿到一个未校验的范围；
 * 且 SQL 里两个时间条件是**无条件**的（不写 {@code <if>}）——漏传只会查不到数据，
 * 不会退化成全表扫描（fail-closed）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentLogMapper
{
    /**
     * 日志列表（按 create_time 倒序，分页由 PageHelper 负责）。
     *
     * @param query  过滤条件（level/status/runId/requestId/keyword）
     * @param window 已归一化并校验过的时间窗（下界/上界均含）
     * @return 日志列表
     */
    List<AgentLog> selectAgentLogList(@Param("query") AgentLog query,
            @Param("window") AgentQueryWindow window);
}
