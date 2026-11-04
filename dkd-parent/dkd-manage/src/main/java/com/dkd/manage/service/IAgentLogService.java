package com.dkd.manage.service;

import java.util.List;

import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.domain.AgentLog;

/**
 * 智能体执行日志只读 Service（Batch A · 排期 2-4）。
 *
 * <p>只读：日志是排障证据，写入方只有智能体服务（AGENTS §6.3）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentLogService
{
    /**
     * 校验并归一化查询时间窗（默认近 24h、最大 30 天）。
     *
     * <p>Controller 在 {@code startPage()} **之前**调用它：被拒绝的请求不应该启动分页，
     * 否则 PageHelper 的线程变量会残留到同线程的下一次查询（详见
     * {@code AgentReadQuerySupport#startBoundedPage} 的注释）。
     *
     * @param query 查询对象
     * @return 归一化后的时间窗
     */
    AgentQueryWindow resolveWindow(AgentLog query);

    /**
     * 日志列表（分页由调用方在 Controller 里 startPage）。
     *
     * @param query 过滤条件（level/status/runId/requestId/keyword + 时间窗原文）
     * @return 日志列表
     */
    List<AgentLog> selectAgentLogList(AgentLog query);
}
