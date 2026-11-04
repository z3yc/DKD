package com.dkd.manage.controller;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.constant.HttpStatus;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.service.IAgentDashboardService;

/**
 * 智能体驾驶舱 Controller（排期 6-1，原型 S7）。
 *
 * <p>路径前缀 {@code /manage/agent/dashboard}，**不经 {@code /agent/**} 网关**。
 *
 * <p>只有一个只读接口 {@code GET /overview?days=7}：一次返回四卡（采纳/降级/成本/失败）+
 * 待我处理 + 对比卡 + 告警条，避免页面发 7 个请求各自带窗口（也就不会有"各卡窗口不一致"的口径漂移）。
 *
 * <p>权限点 {@code manage:agentDashboard:list}（菜单由前端任务交付）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/dashboard")
public class AgentDashboardController extends BaseController
{
    @Autowired
    private IAgentDashboardService agentDashboardService;

    /**
     * 驾驶舱总览。
     *
     * @param days 统计窗口天数（1~30；不传默认 7）
     * @return {@code data} 见 {@link IAgentDashboardService#selectOverview(Integer)}
     */
    @PreAuthorize("@ss.hasPermi('manage:agentDashboard:list')")
    @GetMapping("/overview")
    public AjaxResult overview(@RequestParam(value = "days", required = false) Integer days)
    {
        return success(agentDashboardService.selectOverview(days));
    }

    /**
     * 字段校验失败（如 days 越界）→ HTTP 200 + code 400 + data{field:msg}（与其他智能体管理页一致）。
     *
     * @param e 字段错误
     * @return 统一响应
     */
    @ExceptionHandler(AgentFieldErrorException.class)
    public AjaxResult handleFieldError(AgentFieldErrorException e)
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put(e.getField(), e.getMessage());
        return new AjaxResult(HttpStatus.BAD_REQUEST, e.getMessage(), data);
    }
}
