package com.dkd.common.ai.enums;

/**
 * AI建议类型枚举
 * 
 * @author ruoyi
 */
public enum AiSuggestionType {
    
    /**
     * 诊断建议
     */
    DIAGNOSIS("diagnosis", "诊断建议"),
    
    /**
     * 运营建议
     */
    OPERATION("operation", "运营建议"),
    
    /**
     * 补货建议
     */
    REPLENISHMENT("replenishment", "补货建议"),
    
    /**
     * 优化建议
     */
    OPTIMIZATION("optimization", "优化建议"),
    
    /**
     * 预测建议
     */
    PREDICTION("prediction", "预测建议"),
    
    /**
     * 安全建议
     */
    SAFETY("safety", "安全建议"),
    
    /**
     * 分析建议
     */
    ANALYSIS("analysis", "分析建议");
    
    private final String code;
    private final String name;
    
    AiSuggestionType(String code, String name) {
        this.code = code;
        this.name = name;
    }
    
    public String getCode() {
        return code;
    }
    
    public String getName() {
        return name;
    }
    
    /**
     * 根据代码获取枚举
     * 
     * @param code 代码
     * @return 枚举
     */
    public static AiSuggestionType getByCode(String code) {
        for (AiSuggestionType type : values()) {
            if (type.getCode().equals(code)) {
                return type;
            }
        }
        return null;
    }
}