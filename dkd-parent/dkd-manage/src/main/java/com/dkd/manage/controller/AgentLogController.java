package com.dkd.manage.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.manage.agent.AgentReadQuerySupport;
import com.dkd.manage.domain.AgentLog;
import com.dkd.manage.service.IAgentLogService;

/**
 * 智能体执行日志 Controller（Batch A · 排期 2-4，表 {@code agent_log}）。
 *
 * <p>路径前缀 {@code /manage/agent/log}，**不经 {@code /agent/**} 网关**（网关负责用户对话转发，
 * 日志页是后台管理功能，走 RuoYi 的 JWT 过滤链与权限点）。
 *
 * <p><b>为什么只有一个 GET、没有任何写接口：</b>日志是排障证据，写入方只有智能体服务。
 * 后台能改/删日志，它就不再能回答"当时到底报了什么错"（与
 * {@code AgentDecisionLogController} 同一条纪律，AGENTS §6.3）。
 *
 * <p><b>强制时间窗与分页上限：</b>由 {@link AgentReadQuerySupport} 统一执行——
 * 默认近 24h、最大 30 天、{@code pageSize <= 200}。单主库无只读从库，
 * 不带窗口的日志查询会退化成全表扫描并与业务抢库（AGENTS §1 约束 5）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/log")
public class AgentLogController extends BaseController
{
    @Autowired
    private IAgentLogService agentLogService;

    /**
     * 日志列表（分页）。
     *
     * <p>查询参数：{@code beginTime/endTime}（缺省近 24h，最大 30 天）、
     * {@code level/status/runId/requestId}（精确）、{@code keyword}（模糊匹配
     * {@code message/error_msg}）、RuoYi 分页参数 {@code pageNum/pageSize}（≤200）。
     *
     * <p>注意调用顺序是刻意的：**先校验时间窗，再 startPage**。PageHelper 的分页条件
     * 挂在线程变量上、只有执行一次查询才会被消费；若先 startPage 再因窗口超限抛异常，
     * 这个条件会残留到同线程的下一次查询（表现为"随便点个详情只返回了 10 条"）。
     *
     * @param query 过滤条件
     * @return 分页列表（按 create_time 倒序）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLog:list')")
    @GetMapping("/list")
    public TableDataInfo list(AgentLog query)
    {
        // 1) 校验/归一化时间窗（超 30 天、格式错、起止颠倒在这里被拒绝并给出明确原因）
        agentLogService.resolveWindow(query);
        // 2) 启动分页并强制 pageSize 上限（超限同样在查库前拒绝）
        AgentReadQuerySupport.startBoundedPage();
        return getDataTable(agentLogService.selectAgentLogList(query));
    }
}
