package com.dkd.manage.service;

import java.util.Map;

/**
 * 智能体驾驶舱 Service（排期 6-1，原型 S7）。
 *
 * <p>一次请求返回整页数据（四卡 + 告警条 + 待我处理 + 对比卡），每张卡对应一条带窗口的聚合 SQL；
 * 比率/除零口径在 Service 统一计算（`null` = 无样本，绝不把"没数据"显示成 0%）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentDashboardService
{
    /**
     * 驾驶舱总览。
     *
     * @param days 统计窗口天数（1~30；null 默认 7）
     * @return 概览数据（adoption/degradation/cost/failure/pending/compare/alerts/generatedAt/days）
     */
    Map<String, Object> selectOverview(Integer days);
}
