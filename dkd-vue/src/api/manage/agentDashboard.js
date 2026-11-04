import request from '@/utils/request'

/**
 * 智能体运营驾驶舱 API（排期任务 6-1，页面 S7）。
 *
 * 口径唯一来源：`docs/agent-metrics-dictionary.md`（指标字典 V1）。
 * 页面上每个数字都必须能回答“分子/分母/n/窗口”，所以本接口一次性返回四卡 + 告警条 +
 * 待处理 + 对比数据的聚合结果，避免前端用多个接口拼口径（拼口径 = 必然漂移）。
 *
 * 只读接口：驾驶舱不提供任何写入口；数据来自 agent_* 表与评测报告，由 Java 聚合。
 */

/**
 * 驾驶舱概览（一次请求返回整页数据）。
 *
 * @param {number} days 时间窗（7 / 14 / 30）；后端按半开区间 [begin, end) 统计
 * @returns AjaxResult：data = {
 *   adoption:   { totalPlans, orderedPlans, adjustedPlans, adoptionRate, adjustRate },
 *   degradation:{ totalPlans, unknownPlans, uncalibratedPlans, uncalibratedRate },
 *   cost:       { calls, tokensIn, tokensOut, tokensTotal, avgTokensPerCall, avgLatencyMs,
 *                 daily: [{ date, calls, tokensTotal }] },
 *   failure:    { runs, failedRuns, failureRate, errorLogs },
 *   pending:    { count, overThreshold, thresholdHours, oldestWaitMinutes },
 *   compare:    { available, n, meanDeltaPct, medianDeltaPct, planDate, generatedAt, note },
 *   alerts:     [{ level: 'critical'|'warning'|'info', code, msg }],
 *   generatedAt, days
 * }
 */
export function getDashboardOverview(days) {
  return request({
    url: '/manage/agent/dashboard/overview',
    method: 'get',
    params: { days },
    // 失败原因由页面「失败态 + 重试」持久展示；silent 避免拦截器再弹一条 toast（双提示）
    silent: true
  })
}
