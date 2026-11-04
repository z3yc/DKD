package com.dkd.manage.controller;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.annotation.Log;
import com.dkd.common.constant.HttpStatus;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.dto.AgentEvalReportDto;
import com.dkd.manage.service.IAgentEvalReportService;

/**
 * 智能体评测报告 Controller（排期 5-1）。
 *
 * <p>路径前缀 {@code /manage/agent/eval}，**不经 {@code /agent/**} 网关**。
 *
 * <p>权限点复用配置台（`manage:agentConfig:list/edit`）：评测报告是"配置版本保存"的证据链，
 * 能改配置的人就该能跑/看评测；单独拆权限只会让 CI 配授权更麻烦（后续若要"只读评测"再拆）。
 *
 * <p>三个接口：
 * <ul>
 *   <li>{@code POST /report}：评测脚本/CI 上报报告（只增）；</li>
 *   <li>{@code GET /reports}：某配置最近 N 条报告（版本历史展示）；</li>
 *   <li>{@code GET /gate}：护栏状态预览（{enabled, passed, latest, reason}），
 *       便于前端在保存前先提示"需要评测/低于基线"。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/eval")
public class AgentEvalController extends BaseController
{
    @Autowired
    private IAgentEvalReportService agentEvalReportService;

    /**
     * 上报评测报告（只增）。
     *
     * @param dto 入参
     * @return 落库后的报告
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:edit')")
    @Log(title = "智能体评测", businessType = BusinessType.INSERT)
    @PostMapping("/report")
    public AjaxResult report(@RequestBody AgentEvalReportDto dto)
    {
        return success(agentEvalReportService.insertReport(dto, getUsername()));
    }

    /**
     * 某配置最近 N 条评测报告（倒序）。
     *
     * @param configCode 配置编码（必填）
     * @param limit      条数（1~100，缺省 20）
     * @return {@code data} 为报告数组
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:list')")
    @GetMapping("/reports")
    public AjaxResult reports(@RequestParam(value = "configCode", required = false) String configCode,
            @RequestParam(value = "limit", required = false) Integer limit)
    {
        return success(agentEvalReportService.selectLatestByCode(configCode, limit));
    }

    /**
     * 评测护栏状态（保存前预览）：{@code {enabled, passed, latest, reason}}。
     *
     * @param configCode    配置编码（必填）
     * @param configVersion 配置版本（可空；空时无法判定，返回说明）
     * @return 状态对象
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:list')")
    @GetMapping("/gate")
    public AjaxResult gate(@RequestParam(value = "configCode", required = false) String configCode,
            @RequestParam(value = "configVersion", required = false) Long configVersion)
    {
        return success(agentEvalReportService.selectGate(configCode, configVersion));
    }

    /**
     * 字段校验失败 → HTTP 200 + code 400 + data{field:msg}（与其他智能体管理页一致）。
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
