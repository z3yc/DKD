package com.dkd.common.ai.example;

import com.dkd.common.ai.enums.AiModuleType;
import com.dkd.common.ai.enums.AiSuggestionType;
import com.dkd.common.ai.service.IAiService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;

/**
 * AI服务使用示例
 * 
 * 展示如何在不同模块中使用AI服务
 * 
 * @author ruoyi
 */
@Component
public class AiServiceUsageExample {
    
    @Autowired
    private IAiService aiService;
    
    /**
     * 设备模块使用示例
     */
    public void deviceModuleExample() {
        // 构建设备信息
        Map<String, Object> deviceData = new HashMap<>();
        deviceData.put("innerCode", "VM001");
        deviceData.put("addr", "北京市朝阳区xxx街道");
        deviceData.put("vmTypeId", "TypeA");
        deviceData.put("vmStatus", "运营中");
        deviceData.put("channelMaxCapacity", 50);
        deviceData.put("lastMaintainTime", "2024-01-15");
        deviceData.put("errorLogs", "无");
        
        // 设备诊断
        String diagnosis = aiService.diagnose(AiModuleType.DEVICE.getCode(), deviceData);
        System.out.println("设备诊断结果: " + diagnosis);
        
        // 生成运营建议
        String operationAdvice = aiService.generateSuggestion(
            AiModuleType.DEVICE.getCode(), 
            deviceData, 
            AiSuggestionType.OPERATION.getCode()
        );
        System.out.println("运营建议: " + operationAdvice);
        
        // 生成补货建议
        String replenishmentAdvice = aiService.generateSuggestion(
            AiModuleType.DEVICE.getCode(), 
            deviceData, 
            AiSuggestionType.REPLENISHMENT.getCode()
        );
        System.out.println("补货建议: " + replenishmentAdvice);
    }
    
    /**
     * 订单模块使用示例
     */
    public void orderModuleExample() {
        // 构建订单信息
        Map<String, Object> orderData = new HashMap<>();
        orderData.put("orderId", "ORD20240101001");
        orderData.put("orderStatus", "待处理");
        orderData.put("amount", 25.50);
        orderData.put("createTime", "2024-01-01 10:30:00");
        orderData.put("paymentMethod", "微信支付");
        orderData.put("deviceCode", "VM001");
        orderData.put("products", "可乐, 薯片");
        orderData.put("userInfo", "VIP用户");
        
        // 订单诊断
        String orderDiagnosis = aiService.diagnose(AiModuleType.ORDER.getCode(), orderData);
        System.out.println("订单诊断结果: " + orderDiagnosis);
        
        // 生成订单处理建议
        String orderAdvice = aiService.generateSuggestion(
            AiModuleType.ORDER.getCode(), 
            orderData, 
            AiSuggestionType.DIAGNOSIS.getCode()
        );
        System.out.println("订单处理建议: " + orderAdvice);
    }
    
    /**
     * 库存模块使用示例
     */
    public void inventoryModuleExample() {
        // 构建库存信息
        Map<String, Object> inventoryData = new HashMap<>();
        inventoryData.put("deviceCode", "VM001");
        inventoryData.put("totalProducts", 30);
        inventoryData.put("outOfStockCount", 5);
        inventoryData.put("turnoverRate", 0.75);
        inventoryData.put("averageInventory", 40);
        inventoryData.put("replenishmentFrequency", "每日");
        inventoryData.put("hotProducts", "可乐, 矿泉水");
        inventoryData.put("slowMovingProducts", "某些零食");
        
        // 库存诊断
        String inventoryDiagnosis = aiService.diagnose(AiModuleType.INVENTORY.getCode(), inventoryData);
        System.out.println("库存诊断结果: " + inventoryDiagnosis);
        
        // 生成补货建议
        String replenishmentAdvice = aiService.generateSuggestion(
            AiModuleType.INVENTORY.getCode(), 
            inventoryData, 
            AiSuggestionType.REPLENISHMENT.getCode()
        );
        System.out.println("库存补货建议: " + replenishmentAdvice);
    }
    
    /**
     * 财务模块使用示例
     */
    public void financeModuleExample() {
        // 构建财务信息
        Map<String, Object> financeData = new HashMap<>();
        financeData.put("totalRevenue", 50000.00);
        financeData.put("totalExpenses", 30000.00);
        financeData.put("netProfit", 20000.00);
        financeData.put("profitMargin", 0.4);
        financeData.put("costStructure", "运营成本60%, 维护成本20%, 其他20%");
        financeData.put("revenueTrend", "上升");
        financeData.put("budgetStatus", "在预算内");
        financeData.put("cashFlow", "健康");
        
        // 财务分析
        String financeAnalysis = aiService.diagnose(AiModuleType.FINANCE.getCode(), financeData);
        System.out.println("财务分析结果: " + financeAnalysis);
        
        // 生成优化建议
        String optimizationAdvice = aiService.generateSuggestion(
            AiModuleType.FINANCE.getCode(), 
            financeData, 
            AiSuggestionType.OPTIMIZATION.getCode()
        );
        System.out.println("财务优化建议: " + optimizationAdvice);
    }
    
    /**
     * 通用AI问答示例
     */
    public void generalAiExample() {
        String question = "如何提高智能售货机的运营效率？";
        String answer = aiService.ask(question);
        System.out.println("AI回答: " + answer);
    }
}