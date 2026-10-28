package com.dkd.manage.controller;

import com.dkd.common.ai.service.IAiService;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.service.impl.AiOperationServiceImpl;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

/**
 * AI 设备诊断/运营建议接口。
 *
 * <p>为什么每个方法都必须有 {@code @PreAuthorize}（AGENTS §7.5 / 业务规范 §4.6）：
 * 这三个接口会读取设备上下文并调用 LLM，属于“设备上下文能力”。此前它们没有任何权限注解，
 * 导致**任何登录用户**都能调用（相当于把设备数据与 AI 成本开放给所有角色）。
 * 统一用 {@code manage:vm:query} 与设备诊断场景对齐，也与 Agent 网关按能力判权的权限点同名。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@RestController
@RequestMapping("/manage/ai")
public class AiOperationController extends BaseController {

    @Autowired
    private AiOperationServiceImpl aiOperationService;
    
    @Autowired
    private IAiService aiService;

    /**
     * 获取设备智能诊断建议
     */
    @PreAuthorize("@ss.hasPermi('manage:vm:query')")
    @GetMapping("/diagnose")
    public AjaxResult diagnose(@RequestParam("innerCode") String innerCode) {
        String advice = aiOperationService.getSmartAdvice(innerCode);
        return AjaxResult.success("操作成功", advice);
    }
    
    /**
     * 通用设备诊断接口
     */
    @PreAuthorize("@ss.hasPermi('manage:vm:query')")
    @PostMapping("/diagnose")
    public AjaxResult generalDiagnose(@RequestParam("innerCode") String innerCode,
                                     @RequestParam(required = false, defaultValue = "device") String moduleType) {
        // 构建设备信息
        Map<String, Object> inputData = new HashMap<>();
        inputData.put("innerCode", innerCode);
        String result = aiService.diagnose(moduleType, inputData);
        return AjaxResult.success("诊断完成", result);
    }
    
    /**
     * 生成运营建议
     */
    @PreAuthorize("@ss.hasPermi('manage:vm:query')")
    @GetMapping("/suggest/operation")
    public AjaxResult operationSuggest(@RequestParam("innerCode") String innerCode) {
        String advice = aiOperationService.getSmartAdvice(innerCode);
        return AjaxResult.success("运营建议生成完成", advice);
    }
}