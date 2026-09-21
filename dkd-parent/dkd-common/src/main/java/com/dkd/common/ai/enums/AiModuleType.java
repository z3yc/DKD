package com.dkd.common.ai.enums;

/**
 * AI模块类型枚举
 * 
 * @author ruoyi
 */
public enum AiModuleType {
    
    /**
     * 设备模块
     */
    DEVICE("device", "设备模块"),
    
    /**
     * 订单模块
     */
    ORDER("order", "订单模块"),
    
    /**
     * 库存模块
     */
    INVENTORY("inventory", "库存模块"),
    
    /**
     * 财务模块
     */
    FINANCE("finance", "财务模块"),
    
    /**
     * 运营模块
     */
    OPERATION("operation", "运营模块"),
    
    /**
     * 用户模块
     */
    USER("user", "用户模块"),
    
    /**
     * 商品模块
     */
    PRODUCT("product", "商品模块"),
    
    /**
     * 统计分析模块
     */
    ANALYSIS("analysis", "统计分析模块"),
    
    /**
     * 点位模块
     */
    NODE("node", "点位模块");
    
    private final String code;
    private final String name;
    
    AiModuleType(String code, String name) {
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
    public static AiModuleType getByCode(String code) {
        for (AiModuleType type : values()) {
            if (type.getCode().equals(code)) {
                return type;
            }
        }
        return null;
    }
}