package com.dkd.manage.controller;

import java.util.LinkedHashMap;
import java.util.Map;

import javax.servlet.http.HttpServletRequest;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.annotation.Log;
import com.dkd.common.constant.HttpStatus;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentLlmProbe;
import com.dkd.manage.domain.AgentLlmProfile;
import com.dkd.manage.domain.dto.AgentLlmProfileDto;
import com.dkd.manage.domain.dto.AgentLlmProfileEnabledDto;
import com.dkd.manage.service.IAgentLlmProfileService;

/**
 * 模型档案 Controller（排期 3-5，原型 S14）。
 *
 * <p>路径前缀 {@code /manage/agent/llm/profile}；**不经 {@code /agent/**} 网关**。
 *
 * <p><b>安全边界：</b>所有接口只接收/返回环境变量名 {@code apiKeyEnv}，表里也没有密钥列；
 * 入参 DTO（{@link AgentLlmProfileDto}）没有 apiKey 字段——前端就算提交密钥也会被 Jackson 丢弃，
 * 不会进入实体、更不会落库（AGENTS §7.1 在类型层落实）。
 *
 * <p>连通测试（{@code /{id}/probe}）是唯一会"真实出网调模型"的接口，因此单独权限点
 * {@code manage:agentLlmProfile:probe}：能看档案的人不必自动获得"用公司额度探测模型"的能力。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/llm/profile")
public class AgentLlmProfileController extends BaseController
{
    @Autowired
    private IAgentLlmProfileService agentLlmProfileService;

    /**
     * 档案列表（默认档案置顶；可按 code 模糊 / enabled 精确过滤）。
     *
     * @param query 过滤条件
     * @return {@code data} 为档案数组（不含任何密钥值）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLlmProfile:list')")
    @GetMapping("/list")
    public AjaxResult list(AgentLlmProfile query)
    {
        return success(agentLlmProfileService.selectAgentLlmProfileList(query));
    }

    /**
     * 新增档案（isDefault=1 时同事务把其它档案置 0）。
     *
     * @param dto 入参
     * @return 落库后的档案
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLlmProfile:add')")
    @Log(title = "智能体模型档案", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody AgentLlmProfileDto dto)
    {
        return success(agentLlmProfileService.insertAgentLlmProfile(dto, getUsername()));
    }

    /**
     * 编辑档案（编码只读；isDefault 互斥同新增）。
     *
     * @param id  主键（以路径为准，防止 body 的 id 与路径不一致时改错行）
     * @param dto 入参
     * @return 更新后的档案
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLlmProfile:edit')")
    @Log(title = "智能体模型档案", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}")
    public AjaxResult edit(@PathVariable("id") Long id, @RequestBody AgentLlmProfileDto dto)
    {
        dto.setId(id);
        return success(agentLlmProfileService.updateAgentLlmProfile(dto, getUsername()));
    }

    /**
     * 启用/停用档案。
     *
     * @param id  主键
     * @param dto 入参（enabled 0/1）
     * @return 更新后的档案
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLlmProfile:edit')")
    @Log(title = "智能体模型档案", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}/enabled")
    public AjaxResult updateEnabled(@PathVariable("id") Long id, @RequestBody AgentLlmProfileEnabledDto dto)
    {
        return success(agentLlmProfileService.updateEnabled(id, dto, getUsername()));
    }

    /**
     * 模型连通性测试（会真实出网调模型，权限点单独授予）。
     *
     * <p>requestId 由本请求解析（复用全链路追踪头），透传给 Python 并写入探测记录，
     * 便于把"这次测试"与 Python 侧日志对上。
     *
     * @param id      档案主键
     * @param request 当前请求（取 requestId）
     * @return 探测记录（Python 不可达时返回业务错误，但失败记录已落库）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLlmProfile:probe')")
    @Log(title = "智能体模型档案", businessType = BusinessType.OTHER)
    @PostMapping("/{id}/probe")
    public AjaxResult probe(@PathVariable("id") Long id, HttpServletRequest request)
    {
        String requestId = AgentRequestId.resolve(request);
        AgentLlmProbe probe = agentLlmProfileService.probe(id, requestId, getUsername());
        return success(probe);
    }

    /**
     * 探测记录（倒序分页；前端抽屉 10/页）。
     *
     * @param id 档案主键
     * @return {@code rows/total} 分页结构
     */
    @PreAuthorize("@ss.hasPermi('manage:agentLlmProfile:list')")
    @GetMapping("/{id}/probes")
    public TableDataInfo probes(@PathVariable("id") Long id)
    {
        // ⚠️ 顺序必须是「先校验档案存在，再 startPage()」：
        // PageHelper 会拦截 startPage() 之后的**第一条** MyBatis 查询并拼 LIMIT；
        // 若先 startPage 再 loadOrThrow，那条自带 `limit 1` 的查询会被拼成
        // `limit 1 LIMIT 10` → SQLSyntaxError（2026-11-04 真机 e2e 撞到的 500）。
        AgentLlmProfile profile = agentLlmProfileService.selectAgentLlmProfileById(id);
        startPage();
        return getDataTable(agentLlmProfileService.selectProbeListByCode(profile.getCode()));
    }

    /**
     * 字段校验失败 → HTTP 200 + code 400 + data:{field:msg}（与配置台响应形态一致）。
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
