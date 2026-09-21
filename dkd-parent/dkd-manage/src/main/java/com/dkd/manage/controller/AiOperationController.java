package com.dkd.manage.controller;

import com.dkd.common.ai.service.IAiService;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.service.impl.AiOperationServiceImpl;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

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
    @GetMapping("/diagnose")
    public AjaxResult diagnose(@RequestParam("innerCode") String innerCode) {
        String advice = aiOperationService.getSmartAdvice(innerCode);
        return AjaxResult.success("操作成功", advice);
    }
    
    /**
     * 通用设备诊断接口
     */
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
    @GetMapping("/suggest/operation")
    public AjaxResult operationSuggest(@RequestParam("innerCode") String innerCode) {
        String advice = aiOperationService.getSmartAdvice(innerCode);
        return AjaxResult.success("运营建议生成完成", advice);
    }
}