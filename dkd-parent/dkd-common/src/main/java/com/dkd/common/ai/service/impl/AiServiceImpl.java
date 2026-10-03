package com.dkd.common.ai.service.impl;

import cn.hutool.http.HttpRequest;
import cn.hutool.http.HttpResponse;
import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.dkd.common.ai.config.AiConfig;
import com.dkd.common.ai.enums.AiModuleType;
import com.dkd.common.ai.enums.AiSuggestionType;
import com.dkd.common.ai.service.IAiService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;

/**
 * AI服务实现类
 * 
 * @author ruoyi
 */
@Service
public class AiServiceImpl implements IAiService {
    
    private static final Logger log = LoggerFactory.getLogger(AiServiceImpl.class);
    
    @Autowired
    private AiConfig aiConfig;
    
    @Override
    public String diagnose(String moduleType, Map<String, Object> inputData) {
        // 根据模块类型生成不同的诊断提示词
        String prompt = generateDiagnosisPrompt(moduleType, inputData);
        return callAiApi(prompt);
    }
    
    @Override
    public String ask(String prompt) {
        return callAiApi(prompt);
    }
    
    @Override
    public String generateSuggestion(String moduleType, Map<String, Object> inputData, String suggestionType) {
        // 根据模块类型和建议类型生成不同的提示词
        String prompt = generateSuggestionPrompt(moduleType, inputData, suggestionType);
        return callAiApi(prompt);
    }
    
    /**
     * 生成诊断提示词
     * 
     * @param moduleType 模块类型
     * @param inputData 输入数据
     * @return 提示词
     */
    private String generateDiagnosisPrompt(String moduleType, Map<String, Object> inputData) {
        StringBuilder prompt = new StringBuilder();
        
        // 根据模块类型生成不同的诊断提示词
        AiModuleType aiModuleType = AiModuleType.getByCode(moduleType);
        if (aiModuleType == null) {
            aiModuleType = AiModuleType.DEVICE; // 默认为设备模块
        }
        
        switch (aiModuleType) {
            case DEVICE:
                prompt.append("你是一位智能设备诊断专家。请根据以下设备信息，进行故障诊断并提供简短的解决方案（100字以内）：\n");
                appendDeviceInfo(prompt, inputData);
                break;
            case ORDER:
                prompt.append("你是一位订单处理专家。请根据以下订单信息，分析订单状态并提供处理建议（100字以内）：\n");
                appendOrderInfo(prompt, inputData);
                break;
            case INVENTORY:
                prompt.append("你是一位库存管理专家。请根据以下库存信息，分析库存状态并提供管理建议（100字以内）：\n");
                appendInventoryInfo(prompt, inputData);
                break;
            case FINANCE:
                prompt.append("你是一位财务分析专家。请根据以下财务数据，分析财务状况并提供优化建议（100字以内）：\n");
                appendFinanceInfo(prompt, inputData);
                break;
            case OPERATION:
                prompt.append("你是一位运营管理专家。请根据以下运营数据，分析运营状况并提供运营建议（100字以内）：\n");
                appendOperationInfo(prompt, inputData);
                break;
            case USER:
                prompt.append("你是一位用户行为分析专家。请根据以下用户信息，分析用户行为并提供服务建议（100字以内）：\n");
                appendUserInfo(prompt, inputData);
                break;
            case PRODUCT:
                prompt.append("你是一位商品管理专家。请根据以下商品信息，分析商品状况并提供管理建议（100字以内）：\n");
                appendProductInfo(prompt, inputData);
                break;
            case ANALYSIS:
                prompt.append("你是一位数据分析专家。请根据以下数据，进行分析并提供洞察建议（100字以内）：\n");
                appendAnalysisInfo(prompt, inputData);
                break;
            case NODE:
                prompt.append("你是一位点位管理专家。请根据以下点位信息，进行分析并提供点位运营和优化建议（100字以内）：\n");
                appendNodeInfo(prompt, inputData);
                break;
            default:
                prompt.append("你是一位系统诊断专家。请根据以下信息，进行分析并提供诊断建议（100字以内）：\n");
                for (Map.Entry<String, Object> entry : inputData.entrySet()) {
                    prompt.append(entry.getKey()).append("：").append(entry.getValue()).append("\n");
                }
                break;
        }
        
        return prompt.toString();
    }
    
    /**
     * 生成建议提示词
     * 
     * @param moduleType 模块类型
     * @param inputData 输入数据
     * @param suggestionType 建议类型
     * @return 提示词
     */
    private String generateSuggestionPrompt(String moduleType, Map<String, Object> inputData, String suggestionType) {
        StringBuilder prompt = new StringBuilder();
        
        // 根据模块类型和建议类型生成不同的提示词
        AiModuleType aiModuleType = AiModuleType.getByCode(moduleType);
        if (aiModuleType == null) {
            aiModuleType = AiModuleType.DEVICE; // 默认为设备模块
        }
        
        AiSuggestionType aiSuggestionType = AiSuggestionType.getByCode(suggestionType);
        if (aiSuggestionType == null) {
            aiSuggestionType = AiSuggestionType.DIAGNOSIS; // 默认为诊断建议
        }
        
        switch (aiSuggestionType) {
            case DIAGNOSIS:
                return generateDiagnosisPrompt(moduleType, inputData);
            case OPERATION:
                prompt.append("你是一位运营专家。请根据以下").append(aiModuleType.getName()).append("信息，提供运营优化建议（100字以内）：\n");
                break;
            case REPLENISHMENT:
                prompt.append("你是一位补货专家。请根据以下").append(aiModuleType.getName()).append("信息，提供补货建议（100字以内）：\n");
                break;
            case OPTIMIZATION:
                prompt.append("你是一位优化专家。请根据以下").append(aiModuleType.getName()).append("信息，提供优化建议（100字以内）：\n");
                break;
            case PREDICTION:
                prompt.append("你是一位预测专家。请根据以下").append(aiModuleType.getName()).append("信息，提供预测分析（100字以内）：\n");
                break;
            case SAFETY:
                prompt.append("你是一位安全专家。请根据以下").append(aiModuleType.getName()).append("信息，提供安全建议（100字以内）：\n");
                break;
            default:
                prompt.append("你是一位专家。请根据以下").append(aiModuleType.getName()).append("信息，提供").append(suggestionType).append("（100字以内）：\n");
                break;
        }
        
        // 添加具体信息
        switch (aiModuleType) {
            case DEVICE:
                appendDeviceInfo(prompt, inputData);
                break;
            case ORDER:
                appendOrderInfo(prompt, inputData);
                break;
            case INVENTORY:
                appendInventoryInfo(prompt, inputData);
                break;
            case FINANCE:
                appendFinanceInfo(prompt, inputData);
                break;
            case OPERATION:
                appendOperationInfo(prompt, inputData);
                break;
            case USER:
                appendUserInfo(prompt, inputData);
                break;
            case PRODUCT:
                appendProductInfo(prompt, inputData);
                break;
            case ANALYSIS:
                appendAnalysisInfo(prompt, inputData);
                break;
            default:
                for (Map.Entry<String, Object> entry : inputData.entrySet()) {
                    prompt.append(entry.getKey()).append("：").append(entry.getValue()).append("\n");
                }
                break;
        }
        
        return prompt.toString();
    }
    
    /**
     * 添加设备信息到提示词
     */
    private void appendDeviceInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("设备编号：").append(inputData.getOrDefault("innerCode", "未知")).append("\n");
        prompt.append("设备类型：").append(inputData.getOrDefault("vmTypeId", "未知")).append("\n");
        prompt.append("部署地址：").append(inputData.getOrDefault("addr", "未知")).append("\n");
        prompt.append("当前状态：").append(inputData.getOrDefault("vmStatus", "未知")).append("\n");
        prompt.append("最大货道容量：").append(inputData.getOrDefault("channelMaxCapacity", "未知")).append("\n");
        prompt.append("最后补货时间：").append(inputData.getOrDefault("lastSupplyTime", "未知")).append("\n");
        prompt.append("最后维护时间：").append(inputData.getOrDefault("lastMaintainTime", "未知")).append("\n");
        prompt.append("故障记录：").append(inputData.getOrDefault("errorLogs", "无")).append("\n");
    }
    
    /**
     * 添加订单信息到提示词
     */
    private void appendOrderInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("订单编号：").append(inputData.getOrDefault("orderId", "未知")).append("\n");
        prompt.append("订单状态：").append(inputData.getOrDefault("orderStatus", "未知")).append("\n");
        prompt.append("订单金额：").append(inputData.getOrDefault("amount", "未知")).append("\n");
        prompt.append("创建时间：").append(inputData.getOrDefault("createTime", "未知")).append("\n");
        prompt.append("支付方式：").append(inputData.getOrDefault("paymentMethod", "未知")).append("\n");
        prompt.append("设备编号：").append(inputData.getOrDefault("deviceCode", "未知")).append("\n");
        prompt.append("商品信息：").append(inputData.getOrDefault("products", "未知")).append("\n");
        prompt.append("用户信息：").append(inputData.getOrDefault("userInfo", "未知")).append("\n");
    }
    
    /**
     * 添加库存信息到提示词
     */
    private void appendInventoryInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("设备编号：").append(inputData.getOrDefault("deviceCode", "未知")).append("\n");
        prompt.append("商品总数：").append(inputData.getOrDefault("totalProducts", "未知")).append("\n");
        prompt.append("缺货商品数：").append(inputData.getOrDefault("outOfStockCount", "未知")).append("\n");
        prompt.append("库存周转率：").append(inputData.getOrDefault("turnoverRate", "未知")).append("\n");
        prompt.append("平均库存：").append(inputData.getOrDefault("averageInventory", "未知")).append("\n");
        prompt.append("补货频率：").append(inputData.getOrDefault("replenishmentFrequency", "未知")).append("\n");
        prompt.append("热销商品：").append(inputData.getOrDefault("hotProducts", "未知")).append("\n");
        prompt.append("滞销商品：").append(inputData.getOrDefault("slowMovingProducts", "未知")).append("\n");
    }
    
    /**
     * 添加财务信息到提示词
     */
    private void appendFinanceInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("总收入：").append(inputData.getOrDefault("totalRevenue", "未知")).append("\n");
        prompt.append("总支出：").append(inputData.getOrDefault("totalExpenses", "未知")).append("\n");
        prompt.append("净利润：").append(inputData.getOrDefault("netProfit", "未知")).append("\n");
        prompt.append("利润率：").append(inputData.getOrDefault("profitMargin", "未知")).append("\n");
        prompt.append("成本结构：").append(inputData.getOrDefault("costStructure", "未知")).append("\n");
        prompt.append("收入趋势：").append(inputData.getOrDefault("revenueTrend", "未知")).append("\n");
        prompt.append("预算执行情况：").append(inputData.getOrDefault("budgetStatus", "未知")).append("\n");
        prompt.append("现金流状况：").append(inputData.getOrDefault("cashFlow", "未知")).append("\n");
    }
    
    /**
     * 添加运营信息到提示词
     */
    private void appendOperationInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("设备总数：").append(inputData.getOrDefault("deviceCount", "未知")).append("\n");
        prompt.append("在线设备数：").append(inputData.getOrDefault("onlineDevices", "未知")).append("\n");
        prompt.append("故障设备数：").append(inputData.getOrDefault("faultyDevices", "未知")).append("\n");
        prompt.append("平均响应时间：").append(inputData.getOrDefault("avgResponseTime", "未知")).append("\n");
        prompt.append("服务可用性：").append(inputData.getOrDefault("availability", "未知")).append("\n");
        prompt.append("运营成本：").append(inputData.getOrDefault("operatingCost", "未知")).append("\n");
        prompt.append("运营效率：").append(inputData.getOrDefault("operatingEfficiency", "未知")).append("\n");
        prompt.append("服务质量评分：").append(inputData.getOrDefault("serviceQuality", "未知")).append("\n");
    }
    
    /**
     * 添加用户信息到提示词
     */
    private void appendUserInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("用户ID：").append(inputData.getOrDefault("userId", "未知")).append("\n");
        prompt.append("用户类型：").append(inputData.getOrDefault("userType", "未知")).append("\n");
        prompt.append("消费金额：").append(inputData.getOrDefault("consumptionAmount", "未知")).append("\n");
        prompt.append("消费频次：").append(inputData.getOrDefault("consumptionFrequency", "未知")).append("\n");
        prompt.append("活跃度：").append(inputData.getOrDefault("activityLevel", "未知")).append("\n");
        prompt.append("偏好商品：").append(inputData.getOrDefault("preferredProducts", "未知")).append("\n");
        prompt.append("用户评分：").append(inputData.getOrDefault("userRating", "未知")).append("\n");
        prompt.append("注册时间：").append(inputData.getOrDefault("registerTime", "未知")).append("\n");
    }
    
    /**
     * 添加商品信息到提示词
     */
    private void appendProductInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("商品ID：").append(inputData.getOrDefault("productId", "未知")).append("\n");
        prompt.append("商品名称：").append(inputData.getOrDefault("productName", "未知")).append("\n");
        prompt.append("销售数量：").append(inputData.getOrDefault("salesVolume", "未知")).append("\n");
        prompt.append("库存数量：").append(inputData.getOrDefault("inventoryCount", "未知")).append("\n");
        prompt.append("销售价格：").append(inputData.getOrDefault("price", "未知")).append("\n");
        prompt.append("成本价格：").append(inputData.getOrDefault("cost", "未知")).append("\n");
        prompt.append("利润率：").append(inputData.getOrDefault("profitMargin", "未知")).append("\n");
        prompt.append("销售趋势：").append(inputData.getOrDefault("salesTrend", "未知")).append("\n");
    }
    
    /**
     * 添加分析信息到提示词
     */
    private void appendAnalysisInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("分析类型：").append(inputData.getOrDefault("analysisType", "未知")).append("\n");
        prompt.append("数据范围：").append(inputData.getOrDefault("dataRange", "未知")).append("\n");
        prompt.append("关键指标：").append(inputData.getOrDefault("keyMetrics", "未知")).append("\n");
        prompt.append("趋势分析：").append(inputData.getOrDefault("trendAnalysis", "未知")).append("\n");
        prompt.append("异常检测：").append(inputData.getOrDefault("anomalyDetection", "未知")).append("\n");
        prompt.append("相关性分析：").append(inputData.getOrDefault("correlationAnalysis", "未知")).append("\n");
        prompt.append("预测结果：").append(inputData.getOrDefault("prediction", "未知")).append("\n");
        prompt.append("数据质量：").append(inputData.getOrDefault("dataQuality", "未知")).append("\n");
    }
    
    /**
     * 添加点位信息到提示词
     */
    private void appendNodeInfo(StringBuilder prompt, Map<String, Object> inputData) {
        prompt.append("点位ID：").append(inputData.getOrDefault("id", "未知")).append("\n");
        prompt.append("点位名称：").append(inputData.getOrDefault("nodeName", "未知")).append("\n");
        prompt.append("详细地址：").append(inputData.getOrDefault("address", "未知")).append("\n");
        prompt.append("商圈类型：").append(inputData.getOrDefault("businessType", "未知")).append("\n");
        prompt.append("所属区域ID：").append(inputData.getOrDefault("regionId", "未知")).append("\n");
        prompt.append("合作商ID：").append(inputData.getOrDefault("partnerId", "未知")).append("\n");
        prompt.append("人流量评估：").append(inputData.getOrDefault("traffic", "未知")).append("\n");
        prompt.append("竞争情况：").append(inputData.getOrDefault("competition", "未知")).append("\n");
    }
    
    /**
     * 调用AI API
     * 
     * @param content 内容
     * @return AI响应
     */
    private String callAiApi(String content) {
        try {
            // 构建请求体
            Map<String, Object> message = new HashMap<>();
            message.put("role", "user");
            message.put("content", content);
            
            Map<String, Object> body = new HashMap<>();
            body.put("model", aiConfig.getModel());
            body.put("messages", new Object[]{message});
            
            // 打印请求信息用于调试
            log.info("正在调用 AI 接口: URL={}", aiConfig.getApiUrl());
            
            // 发送请求 (使用 Hutool)
            HttpResponse response = HttpRequest.post(aiConfig.getApiUrl())
                    .header("Authorization", "Bearer " + aiConfig.getApiKey())
                    .header("Content-Type", "application/json")
                    .body(JSONUtil.toJsonStr(body))
                    .timeout(aiConfig.getTimeout()) // 使用配置的超时时间
                    .execute();
            
            String result = response.body();
            int status = response.getStatus();

            // ⚠️ 这里**不再打印上游响应体**（原来 `Body={}` 整段打印）。
            // 理由（AGENTS §6.1「日志中禁止输出完整 LLM 请求/响应体」）：401 响应体里带
            // 凭据指纹（实测 `Your api key: ****48a8 is invalid`），INFO 级别常开 = 凭据线索常年落在日志里。
            // 仍保留可定位的最小上下文：状态码 + 上游给的可读原因（截断），原始体降级到 DEBUG。
            if (status != 200) {
                log.warn("调用 AI 接口失败: status={}, upstreamMessage={}", status, extractErrorMessage(result));
                log.debug("AI 接口原始响应体: {}", result);
                return describeFailureByStatus(status);
            }
            log.debug("AI 接口响应: status={}, bodyLength={}", status,
                    result == null ? 0 : result.length());

            // 解析 JSON
            JSONObject json = JSONUtil.parseObj(result);
            
            // 检查是否包含 API 返回的错误信息
            // ⚠️ 原实现把上游 `error.message` 原样拼接后返回给前端渲染（实测走到了终端用户眼前）。
            // 该字段由上游控制，可能含凭据指纹与内部请求 ID —— 改为"日志留真因、界面给可执行文案"。
            if (json.containsKey("error")) {
                log.warn("AI 接口返回错误对象（HTTP 200）: upstreamMessage={}", extractErrorMessage(result));
                return "AI 服务返回错误，请联系管理员核对 API Key 与模型配置";
            }
            
            JSONArray choices = json.getJSONArray("choices");
            if (choices != null && !choices.isEmpty()) {
                return choices.getJSONObject(0).getJSONObject("message").getStr("content");
            }

            log.warn("AI 接口响应无 choices 字段: status={}, bodyLength={}", status,
                    result == null ? 0 : result.length());
            return "AI 服务返回内容为空，请稍后再试";
            
        } catch (Exception e) {
            log.error("调用 AI 发生异常", e);
            // 捕获 JSON 解析异常或其他网络异常。
            // 不回传 `e.getMessage()`：异常文案可能含内部地址/实现细节，运营看不懂也帮不上排查。
            return "AI 服务调用异常，请稍后再试（详情见服务端日志）";
        }
    }

    /**
     * 按 HTTP 状态码给出**可直接行动**的用户文案（不把上游原始响应体回传前端）。
     *
     * 为什么分状态码而不是统一 "接口请求失败"：鉴权失败（运维要改 Key）、被限流（等一会儿
     * 就行）、上游故障（报障）三者的下一步动作完全不同，合成一条会把"改配置"和"稍后重试"混在一起。
     */
    private static String describeFailureByStatus(int status) {
        if (status == 401 || status == 403) {
            return "AI 服务鉴权失败（HTTP " + status + "），请联系管理员核对 API Key 配置";
        }
        if (status == 429) {
            return "AI 服务调用过于频繁（HTTP 429），请稍后再试";
        }
        if (status >= 500) {
            return "AI 服务暂时不可用（HTTP " + status + "），请稍后再试";
        }
        return "AI 服务调用失败（HTTP " + status + "），请稍后再试";
    }

    /**
     * 只提取上游错误对象里的 `message`（截断到 200 字）用于日志，避免整段响应体入日志。
     *
     * 解析失败时返回占位符而不是抛异常——错误路径上的解析不能再制造错误。
     */
    private static String extractErrorMessage(String body) {
        if (body == null || body.isEmpty()) {
            return "(空响应体)";
        }
        try {
            JSONObject json = JSONUtil.parseObj(body);
            JSONObject error = json.getJSONObject("error");
            if (error != null && error.getStr("message") != null) {
                return truncate(error.getStr("message"), 200);
            }
        } catch (Exception ignored) {
            // 上游返回非 JSON（网关 HTML、超时占位等）：降级为截断原文，仍比整段入日志小
        }
        return truncate(body, 200);
    }

    private static String truncate(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…(truncated)";
    }
}