package com.dkd.common.ai.service;

import java.util.Map;

/**
 * AI服务接口 - 提供通用AI能力
 * 
 * @author ruoyi
 */
public interface IAiService {
    
    /**
     * 根据不同模块类型进行诊断
     * 
     * @param moduleType 模块类型 (如: device, order, inventory, finance等)
     * @param inputData 诊断数据
     * @return 诊断结果
     */
    String diagnose(String moduleType, Map<String, Object> inputData);
    
    /**
     * 通用AI问答
     * 
     * @param prompt 提示词
     * @return AI响应
     */
    String ask(String prompt);
    
    /**
     * 根据模块类型生成特定建议
     * 
     * @param moduleType 模块类型
     * @param inputData 输入数据
     * @param suggestionType 建议类型
     * @return 建议内容
     */
    String generateSuggestion(String moduleType, Map<String, Object> inputData, String suggestionType);
}