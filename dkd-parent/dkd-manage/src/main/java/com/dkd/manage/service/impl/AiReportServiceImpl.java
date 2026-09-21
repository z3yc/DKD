package com.dkd.manage.service.impl;

import cn.hutool.json.JSONUtil;
import com.dkd.common.ai.service.IAiService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

@Service
public class AiReportServiceImpl {

    private static final Logger log = LoggerFactory.getLogger(AiReportServiceImpl.class);

    private static final String REPORT_PROMPT_TEMPLATE =
            "你是一位自动售货机运营数据分析专家。请根据以下运营数据，生成一份结构化的运营分析报告。\n\n" +
            "## 报告要求\n" +
            "1. 整体运营概要（2-3句话）\n" +
            "2. 收入分析（环比变化、趋势判断）\n" +
            "3. 设备运营状况（在线率、故障分析）\n" +
            "4. 库存健康度（缺货预警、补货建议）\n" +
            "5. 点位表现分析（最佳/最差点位）\n" +
            "6. 商品销售分析（热销/滞销）\n" +
            "7. 改进建议（3-5条具体可执行建议）\n\n" +
            "## 运营数据\n%s\n\n" +
            "请用 Markdown 格式输出。";

    @Autowired
    private IAiService aiService;

    public String generateReportAnalysis(Map<String, Object> reportData) {
        try {
            String json = JSONUtil.toJsonPrettyStr(reportData);
            String prompt = String.format(REPORT_PROMPT_TEMPLATE, json);
            return aiService.ask(prompt);
        } catch (Exception e) {
            log.error("AI报表分析生成失败", e);
            return "AI 分析生成失败：" + e.getMessage();
        }
    }

    public Map<String, Object> buildReportDataContext(
            String reportType,
            String reportDate,
            Long totalRevenue,
            Integer totalOrders,
            Integer totalDevices,
            Integer onlineDevices,
            Integer faultDevices,
            Integer lowStockDevices) {

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("报表类型", reportType);
        data.put("报表日期", reportDate);
        data.put("总收入(分)", totalRevenue);
        data.put("成功订单数", totalOrders);
        data.put("设备总数", totalDevices);
        data.put("在线设备数", onlineDevices);
        data.put("故障设备数", faultDevices);
        data.put("库存不足设备数", lowStockDevices);
        if (totalDevices != null && totalDevices > 0 && onlineDevices != null) {
            data.put("设备在线率", String.format("%.1f%%", onlineDevices * 100.0 / totalDevices));
        }
        return data;
    }
}
