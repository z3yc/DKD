package com.dkd.manage.controller;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.manage.domain.AgentDecisionLog;
import com.dkd.manage.service.IAgentDecisionLogService;

/**
 * 智能体决策审计Controller（排期任务 3-7）。
 *
 * <p>AGENTS §6.3 要求「每次写操作决策必须留痕」，§10 的 CR 清单里对应
 * 「Agent 相关改动：决策留痕已接」。留痕数据由智能体侧写好（Python `app/audit.py`
 * 的默认写入器），本控制器只把它**读出来**给人看。
 *
 * <p><b>为什么只有两个 GET（list / 详情），没有任何写接口或删除接口：</b>
 * 留痕是审计证据。一旦后台能改或能删，它就不再能回答“谁在什么时候点了什么、
 * 最后怎么样”——而这正是留痕存在的唯一理由。需要清理时走数据库运维流程，
 * 不在这里开一个口子。
 *
 * @author ruoyi
 * @date 2026-10-01
 */
@RestController
@RequestMapping("/manage/agent/decision-log")
public class AgentDecisionLogController extends BaseController
{
    @Autowired
    private IAgentDecisionLogService agentDecisionLogService;

    /**
     * 查询决策留痕列表（分页）。
     *
     * <p>可用过滤：requestId / scene / userId / triggerType / action / targetType /
     * targetId / result，以及 `params.beginTime` / `params.endTime` 时间范围。
     *
     * <p>注意：列表**不返回** input_context / llm_output 两个 JSON 列（见 Mapper XML 的注释），
     * 需要看完整上下文请调详情接口。
     */
    @PreAuthorize("@ss.hasPermi('manage:agentAudit:list')")
    @GetMapping("/list")
    public TableDataInfo list(AgentDecisionLog log)
    {
        startPage();
        List<AgentDecisionLog> list = agentDecisionLogService.selectAgentDecisionLogList(log);
        return getDataTable(list);
    }

    /**
     * 查询决策留痕详情（含 JSON 上下文，用于审计回放）。
     */
    @PreAuthorize("@ss.hasPermi('manage:agentAudit:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return AjaxResult.success(agentDecisionLogService.selectAgentDecisionLogById(id));
    }
}
