package com.dkd.manage.controller;

import java.util.List;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.manage.domain.AgentRun;
import com.dkd.manage.domain.AgentStep;
import com.dkd.manage.service.IAgentRunService;

/**
 * 智能体运行时间线Controller（F-03，两个场景共用）。
 *
 * <p><b>为什么权限点复用 manage:agentAudit:*</b>：运行过程与决策留痕是同一套审计证据的两个粒度
 * （run_id 关联），运营能看"决策"就该能看"过程"，拆两套权限只会让授权配置更难维护；
 * 需要单独控制时再拆（届时同时改菜单脚本与本类）。
 *
 * <p>为什么只有 GET：run/step/tool_call 是智能体过程数据，后台不得修改（AGENTS §6.3 审计证据）。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
@RestController
@RequestMapping("/manage/agent/run")
public class AgentRunController extends BaseController
{
    @Autowired
    private IAgentRunService agentRunService;

    /**
     * 运行列表（分页）。
     *
     * <p>可用过滤：sceneCode / status / subjectType / subjectId / requestId，
     * 以及 {@code params.beginTime} / {@code params.endTime}（作用在 started_at 上）。
     *
     * @param query 过滤条件
     * @return 分页列表
     */
    @PreAuthorize("@ss.hasPermi('manage:agentAudit:list')")
    @GetMapping("/list")
    public TableDataInfo list(AgentRun query)
    {
        startPage();
        List<AgentRun> list = agentRunService.selectAgentRunList(query);
        return getDataTable(list);
    }

    /**
     * 一次运行的完整时间线（run + 每轮步骤 + 每步工具调用）。
     *
     * @param runId 运行ID（业务主键）
     * @return 时间线；不存在时返回业务错误而不是空对象（空对象会让页面显示一个"空运行"）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentAudit:query')")
    @GetMapping("/{runId}")
    public AjaxResult getInfo(@PathVariable("runId") String runId)
    {
        Map<String, Object> timeline = agentRunService.selectTimeline(runId);
        if (timeline.get("run") == null)
        {
            return AjaxResult.error("运行记录不存在：" + runId);
        }
        return AjaxResult.success(timeline);
    }

    /**
     * 只取轮次（页面展开时若已拿到 run，可不重复取 run）。
     *
     * @param runId 运行ID
     * @return 轮次列表（含每轮工具调用）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentAudit:query')")
    @GetMapping("/{runId}/steps")
    public AjaxResult steps(@PathVariable("runId") String runId)
    {
        List<AgentStep> steps = agentRunService.selectStepsWithToolCalls(runId);
        return AjaxResult.success(steps);
    }
}
