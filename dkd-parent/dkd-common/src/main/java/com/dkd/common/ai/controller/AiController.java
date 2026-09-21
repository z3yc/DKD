package com.dkd.common.ai.controller;

import com.dkd.common.ai.service.IAiService;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

/**
 * AI控制器 - 提供通用AI服务接口
 * 
 * @author ruoyi
 */
@RestController
@RequestMapping("/common/ai")
public class AiController extends BaseController {
    
    @Autowired
    private IAiService aiService;
    
    /**
     * 通用AI诊断接口
     * 
     * @param moduleType 模块类型
     * @param inputData 输入数据
     * @return 诊断结果
     */
    @PostMapping("/diagnose")
    public AjaxResult diagnose(@RequestParam("moduleType") String moduleType,
                              @RequestBody Map<String, Object> inputData) {
        String result = aiService.diagnose(moduleType, inputData);
        return AjaxResult.success("诊断完成", result);
    }
    
    /**
     * 通用AI问答接口
     * 
     * @param request 提示词
     * @return AI响应
     */
    @PostMapping("/ask")
    public AjaxResult ask(@RequestBody Map<String, String> request) {
        String prompt = request.get("prompt");
        if (prompt == null || prompt.trim().isEmpty()) {
            return AjaxResult.error("提示词不能为空");
        }
        String result = aiService.ask(prompt);
        return AjaxResult.success("问答完成", result);
    }
    
    /**
     * 生成特定建议
     * 
     * @param moduleType 模块类型
     * @param suggestionType 建议类型
     * @param inputData 输入数据
     * @return 建议结果
     */
    @PostMapping("/suggest")
    public AjaxResult suggest(@RequestParam("moduleType") String moduleType,
                             @RequestParam("suggestionType") String suggestionType,
                             @RequestBody Map<String, Object> inputData) {
        String result = aiService.generateSuggestion(moduleType, inputData, suggestionType);
        return AjaxResult.success("建议生成完成", result);
    }
    
    /**
     * 设备诊断接口（兼容原有接口）
     * 
     * @param innerCode 设备编号
     * @return 诊断结果
     */
    @GetMapping("/device/diagnose")
    public AjaxResult deviceDiagnose(@RequestParam("innerCode") String innerCode) {
        // 构建设备信息
        Map<String, Object> inputData = new HashMap<>();
        inputData.put("innerCode", innerCode);
        String result = aiService.diagnose("device", inputData);
        return AjaxResult.success("设备诊断完成", result);
    }
}