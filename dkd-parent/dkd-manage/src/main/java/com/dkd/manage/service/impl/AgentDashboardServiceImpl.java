package com.dkd.manage.service.impl;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.dkd.common.utils.DateUtils;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.mapper.AgentDashboardMapper;
import com.dkd.manage.service.IAgentDashboardService;

/**
 * 智能体驾驶舱 Service 实现（排期 6-1）。
 *
 * <p><b>除零口径（全页统一，禁止各处一个样）：</b>
 * <ul>
 *   <li>比率类（采纳率/调整率/失败率/未校准率）：分母为 0 → **null**（= 无样本），由页面显示「—」；</li>
 *   <li>平均类（单次 token/平均延迟）：calls=0 → **null**；</li>
 *   <li>计数/时长类（待处理 count/最长等待、daily 数组）：无数据 → **0 / 空数组**。</li>
 * </ul>
 * 把"没数据"显示成 0% 会让驾驶舱永远"绿油油"，这是监控页最不能犯的错。
 *
 * <p>口径依据 {@code docs/agent-metrics-dictionary.md} V1：M1/M2（采纳/调整）、M5/M6（校准/降级）、
 * M8（token 成本）、M9（失败运行）；告警阈值按 6-1 任务定义（失败率≥20% / ERROR 日志>0 /
 * 未校准率≥50% / 待处理超时>0）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentDashboardServiceImpl implements IAgentDashboardService
{
    /** 默认统计窗口（天） */
    public static final int DEFAULT_DAYS = 7;

    /** 统计窗口上限（天）：超过 30 天请走离线报表，不在驾驶舱做全库扫描 */
    public static final int MAX_DAYS = 30;

    /** 待我将处理超时阈值（小时）：与原型 S7 默认一致 */
    public static final int PENDING_THRESHOLD_HOURS = 24;

    /** 失败运行率告警阈值（M9） */
    private static final BigDecimal FAILURE_RATE_ALERT = new BigDecimal("0.20");

    /** 未校准率告警阈值（M6） */
    private static final BigDecimal UNCALIBRATED_RATE_ALERT = new BigDecimal("0.50");

    @Autowired
    private AgentDashboardMapper agentDashboardMapper;

    @Override
    public Map<String, Object> selectOverview(Integer days)
    {
        int windowDays = days == null ? DEFAULT_DAYS : days.intValue();
        if (windowDays < 1 || windowDays > MAX_DAYS)
        {
            throw new AgentFieldErrorException("days",
                    "统计窗口需在 1~" + MAX_DAYS + " 天之间（当前 " + windowDays + " 天）");
        }
        Date now = new Date();
        Date windowStart = startOfWindow(now, windowDays);

        Map<String, Object> adoption = buildAdoption(windowStart, now);
        Map<String, Object> degradation = buildDegradation(windowStart, now);
        Map<String, Object> cost = buildCost(windowStart, now);
        Map<String, Object> failure = buildFailure(windowStart, now);
        Map<String, Object> pending = buildPending(windowStart, now);
        Map<String, Object> compare = buildCompare();

        Map<String, Object> overview = new LinkedHashMap<String, Object>();
        overview.put("adoption", adoption);
        overview.put("degradation", degradation);
        overview.put("cost", cost);
        overview.put("failure", failure);
        overview.put("pending", pending);
        overview.put("compare", compare);
        overview.put("alerts", buildAlerts(failure, degradation, pending));
        overview.put("generatedAt", DateUtils.parseDateToStr(DateUtils.YYYY_MM_DD_HH_MM_SS, now));
        overview.put("days", Integer.valueOf(windowDays));
        return overview;
    }

    // ------------------------------------------------------------------------------------
    // 四卡 + 待处理 + 对比
    // ------------------------------------------------------------------------------------

    /**
     * 采纳卡（M1/M2）：采纳率 = 直接确认建单 / 已终结决策计划；调整率 = 调整后建单 / 同上。
     *
     * @param windowStart 窗口起点
     * @param windowEnd   窗口终点
     * @return 卡片数据
     */
    private Map<String, Object> buildAdoption(Date windowStart, Date windowEnd)
    {
        Map<String, Object> row = agentDashboardMapper.selectAdoptionAggregate(windowStart, windowEnd);
        long total = longValue(row, "totalPlans");
        long ordered = longValue(row, "orderedPlans");
        long adjusted = longValue(row, "adjustedPlans");

        Map<String, Object> card = new LinkedHashMap<String, Object>();
        card.put("totalPlans", Long.valueOf(total));
        card.put("orderedPlans", Long.valueOf(ordered));
        card.put("adjustedPlans", Long.valueOf(adjusted));
        card.put("adoptionRate", ratio(ordered, total));
        card.put("adjustRate", ratio(adjusted, total));
        return card;
    }

    /**
     * 降级卡（M5/M6）：未校准率 = llm_calibrated=0 / (总数 - 未知)；NULL 单列 unknown 不进分母。
     *
     * @param windowStart 窗口起点
     * @param windowEnd   窗口终点
     * @return 卡片数据
     */
    private Map<String, Object> buildDegradation(Date windowStart, Date windowEnd)
    {
        Map<String, Object> row = agentDashboardMapper.selectDegradationAggregate(windowStart, windowEnd);
        long total = longValue(row, "totalPlans");
        long unknown = longValue(row, "unknownPlans");
        long uncalibrated = longValue(row, "uncalibratedPlans");
        long known = total - unknown;

        Map<String, Object> card = new LinkedHashMap<String, Object>();
        card.put("totalPlans", Long.valueOf(total));
        card.put("unknownPlans", Long.valueOf(unknown));
        card.put("uncalibratedPlans", Long.valueOf(uncalibrated));
        // 分母排除 unknown：把"未接入/未产生"混进分母会把降级率稀释成假低值
        card.put("uncalibratedRate", ratio(uncalibrated, known));
        return card;
    }

    /**
     * 成本卡（M8 chat 侧）：token 合计/平均值 + 按日趋势。
     *
     * @param windowStart 窗口起点
     * @param windowEnd   窗口终点
     * @return 卡片数据
     */
    private Map<String, Object> buildCost(Date windowStart, Date windowEnd)
    {
        Map<String, Object> row = agentDashboardMapper.selectCostAggregate(windowStart, windowEnd);
        long calls = longValue(row, "calls");
        long tokensIn = longValue(row, "tokensIn");
        long tokensOut = longValue(row, "tokensOut");
        long tokensTotal = tokensIn + tokensOut;

        Map<String, Object> card = new LinkedHashMap<String, Object>();
        card.put("calls", Long.valueOf(calls));
        card.put("tokensIn", Long.valueOf(tokensIn));
        card.put("tokensOut", Long.valueOf(tokensOut));
        card.put("tokensTotal", Long.valueOf(tokensTotal));
        card.put("avgTokensPerCall", average(tokensTotal, calls));
        card.put("avgLatencyMs", averageLatencyMs(row, calls));
        List<Map<String, Object>> dailyRows = agentDashboardMapper.selectDailyCost(windowStart, windowEnd);
        List<Map<String, Object>> daily = new ArrayList<Map<String, Object>>();
        if (dailyRows != null)
        {
            for (Map<String, Object> dayRow : dailyRows)
            {
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                item.put("date", String.valueOf(dayRow.get("statDate")));
                item.put("calls", Long.valueOf(longValue(dayRow, "calls")));
                item.put("tokensTotal", Long.valueOf(longValue(dayRow, "tokensTotal")));
                daily.add(item);
            }
        }
        card.put("daily", daily);
        return card;
    }

    /**
     * 失败卡（M9）：失败率 = (failed+interrupted) / 终态运行；分母为 0 → null。
     *
     * <p>另带 ERROR 日志条数（2-4 日志表），它是"失败可被发现"的兜底信号（chat 不写 agent_run）。
     *
     * @param windowStart 窗口起点
     * @param windowEnd   窗口终点
     * @return 卡片数据
     */
    private Map<String, Object> buildFailure(Date windowStart, Date windowEnd)
    {
        Map<String, Object> runRow = agentDashboardMapper.selectFailureAggregate(windowStart, windowEnd);
        long runs = longValue(runRow, "runs");
        long failedRuns = longValue(runRow, "failedRuns");
        Map<String, Object> logRow = agentDashboardMapper.selectErrorLogCount(windowStart, windowEnd);
        long errorLogs = longValue(logRow, "errorLogs");

        Map<String, Object> card = new LinkedHashMap<String, Object>();
        card.put("runs", Long.valueOf(runs));
        card.put("failedRuns", Long.valueOf(failedRuns));
        card.put("failureRate", ratio(failedRuns, runs));
        card.put("errorLogs", Long.valueOf(errorLogs));
        return card;
    }

    /**
     * 待我处理：窗口内 status 1建议/2已调整/6待指派的计划；超时按最近变更（无则创建）计时。
     *
     * @param windowStart 窗口起点
     * @param windowEnd   窗口终点
     * @return 卡片数据
     */
    private Map<String, Object> buildPending(Date windowStart, Date windowEnd)
    {
        int thresholdMinutes = PENDING_THRESHOLD_HOURS * 60;
        Map<String, Object> row = agentDashboardMapper.selectPendingAggregate(windowStart, windowEnd, thresholdMinutes);
        Map<String, Object> card = new LinkedHashMap<String, Object>();
        card.put("count", Long.valueOf(longValue(row, "pendingCount")));
        card.put("overThreshold", Long.valueOf(longValue(row, "overThreshold")));
        card.put("thresholdHours", Integer.valueOf(PENDING_THRESHOLD_HOURS));
        card.put("oldestWaitMinutes", Long.valueOf(longValue(row, "oldestWaitMinutes")));
        return card;
    }

    /**
     * 对比卡（5-4 报告）：读最新一行；n=0 时 available=false，页面不得画结论。
     *
     * @return 卡片数据
     */
    private Map<String, Object> buildCompare()
    {
        Map<String, Object> row = agentDashboardMapper.selectLatestCompareReport();
        Map<String, Object> card = new LinkedHashMap<String, Object>();
        long n = row == null ? 0L : longValue(row, "n");
        boolean available = n > 0L;
        card.put("available", Boolean.valueOf(available));
        card.put("n", Long.valueOf(n));
        card.put("meanDeltaPct", row == null ? null : decimalValue(row.get("meanDeltaPct")));
        card.put("medianDeltaPct", row == null ? null : decimalValue(row.get("medianDeltaPct")));
        card.put("planDate", row == null ? null : formatDate(row.get("planDate"), DateUtils.YYYY_MM_DD));
        card.put("generatedAt",
                row == null ? null : formatDate(row.get("generatedAt"), DateUtils.YYYY_MM_DD_HH_MM_SS));
        card.put("note", compareNote(row == null, n));
        return card;
    }

    /**
     * 对比卡的 sample-size 说明（对齐指标字典 §4：n<30 仅内部调试、n<100 不作对外口径）。
     *
     * @param missing true = 没有任何对比报告
     * @param n       配对样本数
     * @return 说明文案
     */
    private String compareNote(boolean missing, long n)
    {
        if (missing)
        {
            return "暂无对比报告（数据源待 5-4），不画结论";
        }
        if (n <= 0L)
        {
            return "样本不足（n=0），不画结论";
        }
        if (n < 30L)
        {
            return "n=" + n + "：样本不足，仅限内部调试，不作对外口径";
        }
        return n < 100L ? "n=" + n + "：n<100，不作对外口径" : "n=" + n + "：可作内部验收依据";
    }

    // ------------------------------------------------------------------------------------
    // 告警条
    // ------------------------------------------------------------------------------------

    /**
     * 告警条：按任务定义的四个阈值各产出一条（无异常返回空数组）。
     *
     * <p>level 分配：失败率=critical（运行直接失败）；ERROR 日志/未校准率=warning（质量/能力风险）；
     * 待处理超时=info（运营动作提醒，不是系统故障）。
     *
     * @param failure     失败卡
     * @param degradation 降级卡
     * @param pending     待处理卡
     * @return 告警数组
     */
    private List<Map<String, Object>> buildAlerts(Map<String, Object> failure, Map<String, Object> degradation,
            Map<String, Object> pending)
    {
        List<Map<String, Object>> alerts = new ArrayList<Map<String, Object>>();
        BigDecimal failureRate = asDecimal(failure.get("failureRate"));
        if (failureRate != null && failureRate.compareTo(FAILURE_RATE_ALERT) >= 0)
        {
            alerts.add(alert("critical", "failure_rate_high",
                    "窗口内失败运行率 " + percent(failureRate) + " ≥ " + percent(FAILURE_RATE_ALERT)
                            + "（失败运行 " + failure.get("failedRuns") + " / 终态运行 " + failure.get("runs") + "）"));
        }
        long errorLogs = ((Long) failure.get("errorLogs")).longValue();
        if (errorLogs > 0L)
        {
            alerts.add(alert("warning", "error_logs", "窗口内 ERROR 日志 " + errorLogs + " 条，请到执行日志页排查"));
        }
        BigDecimal uncalibratedRate = asDecimal(degradation.get("uncalibratedRate"));
        if (uncalibratedRate != null && uncalibratedRate.compareTo(UNCALIBRATED_RATE_ALERT) >= 0)
        {
            alerts.add(alert("warning", "llm_uncalibrated",
                    "LLM 未校准率 " + percent(uncalibratedRate) + " ≥ " + percent(UNCALIBRATED_RATE_ALERT)
                            + "（未校准 " + degradation.get("uncalibratedPlans") + " / 已知 "
                            + (longValue(degradation, "totalPlans") - longValue(degradation, "unknownPlans"))
                            + " 行）"));
        }
        long overThreshold = ((Long) pending.get("overThreshold")).longValue();
        if (overThreshold > 0L)
        {
            alerts.add(alert("info", "pending_overdue",
                    "待处理中 " + overThreshold + " 个超过 " + PENDING_THRESHOLD_HOURS + " 小时，请及时确认/调整"));
        }
        return alerts;
    }

    private Map<String, Object> alert(String level, String code, String msg)
    {
        Map<String, Object> alert = new LinkedHashMap<String, Object>();
        alert.put("level", level);
        alert.put("code", code);
        alert.put("msg", msg);
        return alert;
    }

    // ------------------------------------------------------------------------------------
    // 计算 / 取值辅助（除零口径见类注释）
    // ------------------------------------------------------------------------------------

    /**
     * 窗口起点：今天 00:00:00 往前 (windowDays-1) 天（含今天）。
     *
     * @param now        当前时间
     * @param windowDays 窗口天数
     * @return 窗口起点
     */
    private Date startOfWindow(Date now, int windowDays)
    {
        Calendar calendar = Calendar.getInstance();
        calendar.setTime(now);
        calendar.set(Calendar.HOUR_OF_DAY, 0);
        calendar.set(Calendar.MINUTE, 0);
        calendar.set(Calendar.SECOND, 0);
        calendar.set(Calendar.MILLISECOND, 0);
        calendar.add(Calendar.DAY_OF_YEAR, -(windowDays - 1));
        return calendar.getTime();
    }

    /**
     * 比率：分母为 0 返回 null（= 无样本；不显示成 0%）。
     *
     * @param numerator   分子
     * @param denominator 分母
     * @return 0~1 的比率（保留 4 位）或 null
     */
    private BigDecimal ratio(long numerator, long denominator)
    {
        if (denominator <= 0L)
        {
            return null;
        }
        return BigDecimal.valueOf(numerator).divide(BigDecimal.valueOf(denominator), 4, RoundingMode.HALF_UP);
    }

    /**
     * 平均值（保留 1 位）：分母为 0 返回 null。
     *
     * @param total 合计
     * @param count 次数
     * @return 平均值或 null
     */
    private BigDecimal average(long total, long count)
    {
        if (count <= 0L)
        {
            return null;
        }
        return BigDecimal.valueOf(total).divide(BigDecimal.valueOf(count), 1, RoundingMode.HALF_UP);
    }

    /**
     * 平均延迟（毫秒，整数）：calls=0 返回 null。
     *
     * @param row   成本聚合行（含 avgLatencyMs，可能由 SQL 算好）
     * @param calls 调用次数
     * @return 平均延迟或 null
     */
    private Long averageLatencyMs(Map<String, Object> row, long calls)
    {
        if (calls <= 0L || row == null || row.get("avgLatencyMs") == null)
        {
            return null;
        }
        Object value = row.get("avgLatencyMs");
        if (value instanceof Number)
        {
            return Long.valueOf(Math.round(((Number) value).doubleValue()));
        }
        try
        {
            return Long.valueOf(Math.round(Double.parseDouble(String.valueOf(value))));
        }
        catch (NumberFormatException e)
        {
            return null;
        }
    }

    private long longValue(Map<String, Object> row, String key)
    {
        if (row == null)
        {
            return 0L;
        }
        Object value = row.get(key);
        if (value == null)
        {
            return 0L;
        }
        if (value instanceof Number)
        {
            return ((Number) value).longValue();
        }
        try
        {
            return Long.parseLong(String.valueOf(value));
        }
        catch (NumberFormatException e)
        {
            return 0L;
        }
    }

    private BigDecimal decimalValue(Object value)
    {
        if (value == null)
        {
            return null;
        }
        if (value instanceof BigDecimal)
        {
            return (BigDecimal) value;
        }
        if (value instanceof Number)
        {
            return BigDecimal.valueOf(((Number) value).doubleValue());
        }
        try
        {
            return new BigDecimal(String.valueOf(value));
        }
        catch (NumberFormatException e)
        {
            return null;
        }
    }

    private BigDecimal asDecimal(Object value)
    {
        return value instanceof BigDecimal ? (BigDecimal) value : null;
    }

    private String percent(BigDecimal ratio)
    {
        return String.format(Locale.ROOT, "%.1f%%", ratio.multiply(BigDecimal.valueOf(100)).doubleValue());
    }

    private String formatDate(Object value, String pattern)
    {
        if (value == null)
        {
            return null;
        }
        if (value instanceof Date)
        {
            return DateUtils.parseDateToStr(pattern, (Date) value);
        }
        String text = String.valueOf(value);
        return StringUtils.isEmpty(text) ? null : text;
    }
}
