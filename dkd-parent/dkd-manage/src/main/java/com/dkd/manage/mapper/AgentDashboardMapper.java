package com.dkd.manage.mapper;

import java.util.Date;
import java.util.List;
import java.util.Map;

import org.apache.ibatis.annotations.Param;

/**
 * 智能体驾驶舱聚合 Mapper（排期 6-1）。
 *
 * <p><b>为什么每个聚合都是独立的一条带窗口的 SQL、返回 Map：</b>
 * 驾驶舱是"看数"页，不需要实体映射；每个卡片一条聚合（`count/sum/max`），
 * 全部带时间窗口（`days` 1~30），禁止无窗口全表扫描（AGENTS §1 约束 5：单主库无只读从库）。
 *
 * <p>口径依据 {@code docs/agent-metrics-dictionary.md} V1（M1/M2/M6/M8/M9），
 * 具体在每个 XML 语句上方注释（为什么用决策日志算调整、为什么 NULL 不进分母等）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentDashboardMapper
{
    /**
     * 采纳卡：窗口内已终结决策的计划 cohort（M1/M2 口径，按决策时点归属窗口）。
     *
     * @param windowStart 窗口起点（含）
     * @param windowEnd   窗口终点（含）
     * @return totalPlans / orderedPlans / adjustedPlans
     */
    Map<String, Object> selectAdoptionAggregate(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd);

    /**
     * 降级卡：窗口内 plan_date 范围的计划行（M5/M6 口径：llm_calibrated 0=未校准，NULL=未知）。
     *
     * @param windowStart 窗口起点（含）
     * @param windowEnd   窗口终点（含）
     * @return totalPlans / unknownPlans / uncalibratedPlans
     */
    Map<String, Object> selectDegradationAggregate(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd);

    /**
     * 成本卡：窗口内 agent_message 的 token/延迟合计（M8 的 chat 侧）。
     *
     * @param windowStart 窗口起点（含）
     * @param windowEnd   窗口终点（含）
     * @return calls / tokensIn / tokensOut / tokensTotal
     */
    Map<String, Object> selectCostAggregate(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd);

    /**
     * 成本卡按日趋势（窗口内按 create_time 的自然日分组）。
     *
     * @param windowStart 窗口起点（含）
     * @param windowEnd   窗口终点（含）
     * @return 每行 {date, calls, tokensTotal}，按日期升序
     */
    List<Map<String, Object>> selectDailyCost(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd);

    /**
     * 失败卡：窗口内终态运行（M9 口径；running/waiting_confirm 不进分母）。
     *
     * @param windowStart 窗口起点（含）
     * @param windowEnd   窗口终点（含）
     * @return runs / failedRuns
     */
    Map<String, Object> selectFailureAggregate(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd);

    /**
     * 失败卡：窗口内 ERROR 日志条数（2-4 日志表）。
     *
     * @param windowStart 窗口起点（含）
     * @param windowEnd   窗口终点（含）
     * @return errorLogs
     */
    Map<String, Object> selectErrorLogCount(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd);

    /**
     * 待我处理：窗口内计划的待办（status 1/2/6）数量、超时数、最长等待分钟。
     *
     * @param windowStart     窗口起点（含）
     * @param windowEnd       窗口终点（含）
     * @param thresholdMinutes 超时阈值（分钟）
     * @return count / overThreshold / oldestWaitMinutes
     */
    Map<String, Object> selectPendingAggregate(@Param("windowStart") Date windowStart,
            @Param("windowEnd") Date windowEnd, @Param("thresholdMinutes") int thresholdMinutes);

    /**
     * 对比卡：最新一行对比报告（agent_compare_report，只增；orders by generated_at desc）。
     *
     * @return n / meanDeltaPct / medianDeltaPct / planDate / generatedAt；无数据返回 null
     */
    Map<String, Object> selectLatestCompareReport();
}
