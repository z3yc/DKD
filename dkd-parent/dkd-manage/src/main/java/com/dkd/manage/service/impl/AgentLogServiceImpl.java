package com.dkd.manage.service.impl;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.agent.AgentReadQuerySupport;
import com.dkd.manage.domain.AgentLog;
import com.dkd.manage.mapper.AgentLogMapper;
import com.dkd.manage.service.IAgentLogService;

/**
 * 智能体执行日志只读 Service 实现（Batch A · 排期 2-4）。
 *
 * <p><b>为什么这里没有"业务逻辑"、却还要留一层时间窗解析：</b>
 * 时间窗护栏（默认近 24h、最大 30 天）必须在 Service 也执行一次，不能只依赖 Controller——
 * 将来若有定时任务/其他 Controller 直接调用本 Service，绕过的是整个护栏而不是一句提示。
 * Controller 那一侧再校验一次是为了**顺序**（先校验、后 startPage），不是为了重复校验本身。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentLogServiceImpl implements IAgentLogService
{
    @Autowired
    private AgentLogMapper agentLogMapper;

    @Override
    public AgentQueryWindow resolveWindow(AgentLog query)
    {
        return AgentReadQuerySupport.resolveWindow(query);
    }

    @Override
    public List<AgentLog> selectAgentLogList(AgentLog query)
    {
        // 先算窗口：格式非法/超 30 天/起止颠倒都在查库之前被拒绝（同时保证 SQL 一定带时间条件）
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow(query);
        trimFilters(query);
        return agentLogMapper.selectAgentLogList(query, window);
    }

    /**
     * 去掉过滤条件首尾空白。
     *
     * <p>为什么只 trim 不过滤空串：{@code "  "} 这样的关键词若不 trim 会变成
     * {@code like '%  %'}，看似没筛却把几乎所有行干掉，是典型"查不到但不知道为什么"的坑。
     */
    private void trimFilters(AgentLog query)
    {
        query.setLevel(StringUtils.trim(query.getLevel()));
        query.setStatus(StringUtils.trim(query.getStatus()));
        query.setRunId(StringUtils.trim(query.getRunId()));
        query.setRequestId(StringUtils.trim(query.getRequestId()));
        query.setKeyword(StringUtils.trim(query.getKeyword()));
    }
}
