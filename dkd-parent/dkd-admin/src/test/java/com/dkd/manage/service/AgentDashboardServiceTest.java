package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.mapper.AgentDashboardMapper;
import com.dkd.manage.service.impl.AgentDashboardServiceImpl;

/**
 * 驾驶舱聚合 Service 单测（排期 6-1）。
 *
 * <p>重点覆盖三件容易出错的事：
 * <ol>
 *   <li><b>除零口径</b>：无样本时比率/平均值必须是 null（页面显示「—」），不是 0；</li>
 *   <li><b>未知不进分母</b>：llm_calibrated=NULL 单列 unknown，未校准率分母 = 总数-未知；</li>
 *   <li><b>告警阈值边界</b>：失败率 ≥20%、未校准率 ≥50%、ERROR>0、待处理超时>0 各一条；
 *       无异常必须返回空数组（不能默认给一条"全部正常"的假告警）。</li>
 * </ol>
 * 全部 Mapper 用 Mockito 打桩，不连真库。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentDashboardServiceTest
{
    @Mock
    private AgentDashboardMapper agentDashboardMapper;

    @InjectMocks
    private AgentDashboardServiceImpl agentDashboardService;

    // ------------------------------------------------------------------------------------
    // 夹具
    // ------------------------------------------------------------------------------------

    private static Map<String, Object> map(Object... kv)
    {
        Map<String, Object> map = new LinkedHashMap<String, Object>();
        for (int i = 0; i + 1 < kv.length; i += 2)
        {
            map.put(String.valueOf(kv[i]), kv[i + 1]);
        }
        return map;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> card(Map<String, Object> overview, String key)
    {
        return (Map<String, Object>) overview.get(key);
    }

    private void stubEmpty()
    {
        when(agentDashboardMapper.selectAdoptionAggregate(any(), any()))
                .thenReturn(map("totalPlans", Long.valueOf(0L), "orderedPlans", Long.valueOf(0L), "adjustedPlans",
                        Long.valueOf(0L)));
        when(agentDashboardMapper.selectDegradationAggregate(any(), any()))
                .thenReturn(map("totalPlans", Long.valueOf(0L), "unknownPlans", Long.valueOf(0L), "uncalibratedPlans",
                        Long.valueOf(0L)));
        when(agentDashboardMapper.selectCostAggregate(any(), any()))
                .thenReturn(map("calls", Long.valueOf(0L), "tokensIn", Long.valueOf(0L), "tokensOut",
                        Long.valueOf(0L), "tokensTotal", Long.valueOf(0L), "avgLatencyMs", null));
        when(agentDashboardMapper.selectDailyCost(any(), any()))
                .thenReturn(new ArrayList<Map<String, Object>>());
        when(agentDashboardMapper.selectFailureAggregate(any(), any()))
                .thenReturn(map("runs", Long.valueOf(0L), "failedRuns", Long.valueOf(0L)));
        when(agentDashboardMapper.selectErrorLogCount(any(), any())).thenReturn(map("errorLogs", Long.valueOf(0L)));
        when(agentDashboardMapper.selectPendingAggregate(any(), any(), anyInt()))
                .thenReturn(map("pendingCount", Long.valueOf(0L), "overThreshold", Long.valueOf(0L),
                        "oldestWaitMinutes", Long.valueOf(0L)));
        when(agentDashboardMapper.selectLatestCompareReport()).thenReturn(null);
    }

    // ------------------------------------------------------------------------------------
    // 空数据
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("空数据：计数 0、比率/平均为 null、alerts 为空、compare 不可用")
    void emptyDataReturnsNullRatesAndNoAlerts()
    {
        stubEmpty();

        Map<String, Object> overview = agentDashboardService.selectOverview(null);

        assertThat(overview.get("days")).isEqualTo(7);
        assertThat(overview.get("generatedAt")).isNotNull();

        Map<String, Object> adoption = card(overview, "adoption");
        assertThat(adoption.get("totalPlans")).isEqualTo(0L);
        assertThat(adoption.get("adoptionRate")).as("无样本不是 0%").isNull();
        assertThat(adoption.get("adjustRate")).isNull();

        Map<String, Object> degradation = card(overview, "degradation");
        assertThat(degradation.get("uncalibratedRate")).isNull();

        Map<String, Object> cost = card(overview, "cost");
        assertThat(cost.get("calls")).isEqualTo(0L);
        assertThat(cost.get("avgTokensPerCall")).isNull();
        assertThat(cost.get("avgLatencyMs")).isNull();
        assertThat((List<?>) cost.get("daily")).isEmpty();

        Map<String, Object> failure = card(overview, "failure");
        assertThat(failure.get("failureRate")).isNull();
        assertThat(failure.get("errorLogs")).isEqualTo(0L);

        Map<String, Object> pending = card(overview, "pending");
        assertThat(pending.get("count")).isEqualTo(0L);
        assertThat(pending.get("oldestWaitMinutes")).isEqualTo(0L);
        assertThat(pending.get("thresholdHours")).isEqualTo(24);

        Map<String, Object> compare = card(overview, "compare");
        assertThat(compare.get("available")).isEqualTo(Boolean.FALSE);
        assertThat(compare.get("n")).isEqualTo(0L);
        assertThat((String) compare.get("note")).contains("暂无对比报告");

        assertThat((List<?>) overview.get("alerts")).isEmpty();
    }

    // ------------------------------------------------------------------------------------
    // 有数据计算
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("有数据：四卡比率/合计/趋势/待处理/对比按口径计算")
    void dataAggregationComputesRates()
    {
        when(agentDashboardMapper.selectAdoptionAggregate(any(), any()))
                .thenReturn(map("totalPlans", Long.valueOf(10L), "orderedPlans", Long.valueOf(6L), "adjustedPlans",
                        Long.valueOf(3L)));
        when(agentDashboardMapper.selectDegradationAggregate(any(), any()))
                .thenReturn(map("totalPlans", Long.valueOf(10L), "unknownPlans", Long.valueOf(2L), "uncalibratedPlans",
                        Long.valueOf(4L)));
        when(agentDashboardMapper.selectCostAggregate(any(), any()))
                .thenReturn(map("calls", Long.valueOf(4L), "tokensIn", Long.valueOf(100L), "tokensOut",
                        Long.valueOf(50L), "tokensTotal", Long.valueOf(150L), "avgLatencyMs", Long.valueOf(800L)));
        when(agentDashboardMapper.selectDailyCost(any(), any())).thenReturn(new ArrayList<Map<String, Object>>(
                Arrays.asList(map("statDate", "2026-11-03", "calls", Long.valueOf(2L), "tokensTotal", Long.valueOf(70L)),
                        map("statDate", "2026-11-04", "calls", Long.valueOf(2L), "tokensTotal", Long.valueOf(80L)))));
        when(agentDashboardMapper.selectFailureAggregate(any(), any()))
                .thenReturn(map("runs", Long.valueOf(10L), "failedRuns", Long.valueOf(3L)));
        when(agentDashboardMapper.selectErrorLogCount(any(), any())).thenReturn(map("errorLogs", Long.valueOf(2L)));
        when(agentDashboardMapper.selectPendingAggregate(any(), any(), anyInt()))
                .thenReturn(map("pendingCount", Long.valueOf(5L), "overThreshold", Long.valueOf(2L),
                        "oldestWaitMinutes", Long.valueOf(1500L)));
        when(agentDashboardMapper.selectLatestCompareReport())
                .thenReturn(map("n", Long.valueOf(50L), "meanDeltaPct", new BigDecimal("12.34"), "medianDeltaPct",
                        new BigDecimal("10.00"), "planDate", "2026-11-03", "generatedAt", "2026-11-04 01:00:00"));

        Map<String, Object> overview = agentDashboardService.selectOverview(Integer.valueOf(7));

        Map<String, Object> adoption = card(overview, "adoption");
        assertThat(adoption.get("adoptionRate")).isEqualTo(new BigDecimal("0.6000"));
        assertThat(adoption.get("adjustRate")).isEqualTo(new BigDecimal("0.3000"));

        Map<String, Object> degradation = card(overview, "degradation");
        // 分母 = 10 - 2 = 8；未校准 4/8 = 0.5（未知的 2 行不进分母）
        assertThat(degradation.get("uncalibratedRate")).isEqualTo(new BigDecimal("0.5000"));

        Map<String, Object> cost = card(overview, "cost");
        assertThat(cost.get("tokensTotal")).isEqualTo(150L);
        assertThat(cost.get("avgTokensPerCall")).isEqualTo(new BigDecimal("37.5"));
        assertThat(cost.get("avgLatencyMs")).isEqualTo(800L);
        List<?> daily = (List<?>) cost.get("daily");
        assertThat(daily).hasSize(2);
        @SuppressWarnings("unchecked")
        Map<String, Object> firstDay = (Map<String, Object>) daily.get(0);
        assertThat(firstDay.get("date")).isEqualTo("2026-11-03");
        assertThat(firstDay.get("tokensTotal")).isEqualTo(70L);

        Map<String, Object> failure = card(overview, "failure");
        assertThat(failure.get("failureRate")).isEqualTo(new BigDecimal("0.3000"));
        assertThat(failure.get("errorLogs")).isEqualTo(2L);

        Map<String, Object> pending = card(overview, "pending");
        assertThat(pending.get("count")).isEqualTo(5L);
        assertThat(pending.get("overThreshold")).isEqualTo(2L);
        assertThat(pending.get("oldestWaitMinutes")).isEqualTo(1500L);

        Map<String, Object> compare = card(overview, "compare");
        assertThat(compare.get("available")).isEqualTo(Boolean.TRUE);
        assertThat(compare.get("n")).isEqualTo(50L);
        assertThat((String) compare.get("note")).contains("n=50").contains("n<100");
    }

    @Test
    @DisplayName("告警条：四个阈值各触发一条（失败=critical / ERROR、未校准=warning / 待处理=info）")
    void alertsFireAtThresholds()
    {
        when(agentDashboardMapper.selectFailureAggregate(any(), any()))
                .thenReturn(map("runs", Long.valueOf(10L), "failedRuns", Long.valueOf(2L)));
        when(agentDashboardMapper.selectErrorLogCount(any(), any())).thenReturn(map("errorLogs", Long.valueOf(1L)));
        when(agentDashboardMapper.selectDegradationAggregate(any(), any()))
                .thenReturn(map("totalPlans", Long.valueOf(4L), "unknownPlans", Long.valueOf(0L), "uncalibratedPlans",
                        Long.valueOf(2L)));
        when(agentDashboardMapper.selectPendingAggregate(any(), any(), anyInt()))
                .thenReturn(map("pendingCount", Long.valueOf(3L), "overThreshold", Long.valueOf(1L),
                        "oldestWaitMinutes", Long.valueOf(2000L)));
        when(agentDashboardMapper.selectAdoptionAggregate(any(), any())).thenReturn(map());
        when(agentDashboardMapper.selectCostAggregate(any(), any())).thenReturn(map());
        when(agentDashboardMapper.selectDailyCost(any(), any())).thenReturn(new ArrayList<Map<String, Object>>());
        when(agentDashboardMapper.selectLatestCompareReport()).thenReturn(null);

        Map<String, Object> overview = agentDashboardService.selectOverview(Integer.valueOf(7));

        List<?> alerts = (List<?>) overview.get("alerts");
        assertThat(alerts).hasSize(4);
        assertAlert(alerts, "critical", "failure_rate_high");
        assertAlert(alerts, "warning", "error_logs");
        assertAlert(alerts, "warning", "llm_uncalibrated");
        assertAlert(alerts, "info", "pending_overdue");
    }

    @Test
    @DisplayName("告警条：低于阈值/无样本时不报警（失败率 0.19、未校准率 0.49、无 ERROR、无超时）")
    void alertsDoNotFireBelowThresholds()
    {
        when(agentDashboardMapper.selectFailureAggregate(any(), any()))
                .thenReturn(map("runs", Long.valueOf(100L), "failedRuns", Long.valueOf(19L)));
        when(agentDashboardMapper.selectErrorLogCount(any(), any())).thenReturn(map("errorLogs", Long.valueOf(0L)));
        when(agentDashboardMapper.selectDegradationAggregate(any(), any()))
                .thenReturn(map("totalPlans", Long.valueOf(100L), "unknownPlans", Long.valueOf(0L),
                        "uncalibratedPlans", Long.valueOf(49L)));
        when(agentDashboardMapper.selectPendingAggregate(any(), any(), anyInt()))
                .thenReturn(map("pendingCount", Long.valueOf(2L), "overThreshold", Long.valueOf(0L),
                        "oldestWaitMinutes", Long.valueOf(60L)));
        when(agentDashboardMapper.selectAdoptionAggregate(any(), any())).thenReturn(map());
        when(agentDashboardMapper.selectCostAggregate(any(), any())).thenReturn(map());
        when(agentDashboardMapper.selectDailyCost(any(), any())).thenReturn(new ArrayList<Map<String, Object>>());
        when(agentDashboardMapper.selectLatestCompareReport()).thenReturn(null);

        Map<String, Object> overview = agentDashboardService.selectOverview(Integer.valueOf(7));

        assertThat((List<?>) overview.get("alerts")).isEmpty();
    }

    @Test
    @DisplayName("对比卡：n=0 时 available=false，note 显示样本不足")
    void compareWithZeroSamplesIsUnavailable()
    {
        stubEmpty();
        when(agentDashboardMapper.selectLatestCompareReport())
                .thenReturn(map("n", Long.valueOf(0L), "meanDeltaPct", null, "medianDeltaPct", null, "planDate",
                        "2026-11-03", "generatedAt", "2026-11-04 01:00:00"));

        Map<String, Object> overview = agentDashboardService.selectOverview(Integer.valueOf(7));

        Map<String, Object> compare = card(overview, "compare");
        assertThat(compare.get("available")).isEqualTo(Boolean.FALSE);
        assertThat(compare.get("n")).isEqualTo(0L);
        assertThat((String) compare.get("note")).contains("样本不足");
    }

    @Test
    @DisplayName("对比卡：n≥100 时 note 标注可作内部验收依据")
    void compareWithEnoughSamples()
    {
        stubEmpty();
        when(agentDashboardMapper.selectLatestCompareReport())
                .thenReturn(map("n", Long.valueOf(120L), "meanDeltaPct", new BigDecimal("5.00"), "medianDeltaPct",
                        new BigDecimal("4.00"), "planDate", "2026-11-03", "generatedAt", "2026-11-04 01:00:00"));

        Map<String, Object> overview = agentDashboardService.selectOverview(Integer.valueOf(30));

        Map<String, Object> compare = card(overview, "compare");
        assertThat(compare.get("available")).isEqualTo(Boolean.TRUE);
        assertThat((String) compare.get("note")).contains("可作内部验收依据");
        assertThat(overview.get("days")).isEqualTo(30);
    }

    // ------------------------------------------------------------------------------------
    // 窗口/参数
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("窗口：起点为今天 00:00 往前 days-1 天，终点为当前时间；待处理阈值 24h")
    void windowIsDayAligned()
    {
        stubEmpty();
        Date before = new Date();

        agentDashboardService.selectOverview(Integer.valueOf(7));

        ArgumentCaptor<Date> startCaptor = ArgumentCaptor.forClass(Date.class);
        ArgumentCaptor<Date> endCaptor = ArgumentCaptor.forClass(Date.class);
        verify(agentDashboardMapper).selectAdoptionAggregate(startCaptor.capture(), endCaptor.capture());
        java.util.Calendar cal = java.util.Calendar.getInstance();
        cal.setTime(startCaptor.getValue());
        assertThat(cal.get(java.util.Calendar.HOUR_OF_DAY)).isZero();
        assertThat(cal.get(java.util.Calendar.MINUTE)).isZero();
        assertThat(cal.get(java.util.Calendar.SECOND)).isZero();
        // 起点 = 今天 -(7-1) 天
        java.util.Calendar expected = java.util.Calendar.getInstance();
        expected.setTime(before);
        expected.add(java.util.Calendar.DAY_OF_YEAR, -6);
        assertThat(cal.get(java.util.Calendar.DAY_OF_YEAR))
                .isEqualTo(expected.get(java.util.Calendar.DAY_OF_YEAR));
        assertThat(endCaptor.getValue()).isAfterOrEqualTo(before);

        ArgumentCaptor<Integer> thresholdCaptor = ArgumentCaptor.forClass(Integer.class);
        verify(agentDashboardMapper).selectPendingAggregate(any(), any(), thresholdCaptor.capture());
        assertThat(thresholdCaptor.getValue()).isEqualTo(24 * 60);
    }

    @Test
    @DisplayName("days 越界（0/31）→ 字段错误，不查库")
    void rejectsInvalidDays()
    {
        assertThatThrownBy(() -> agentDashboardService.selectOverview(Integer.valueOf(0)))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("1~30");
        assertThatThrownBy(() -> agentDashboardService.selectOverview(Integer.valueOf(31)))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("1~30");
        org.mockito.Mockito.verifyNoInteractions(agentDashboardMapper);
    }

    private void assertAlert(List<?> alerts, String level, String code)
    {
        for (Object item : alerts)
        {
            Map<?, ?> alert = (Map<?, ?>) item;
            if (code.equals(alert.get("code")))
            {
                assertThat(alert.get("level")).as("告警 " + code + " 的级别").isEqualTo(level);
                assertThat((String) alert.get("msg")).isNotBlank();
                return;
            }
        }
        throw new AssertionError("缺少告警 " + code + "，实际=" + alerts);
    }
}
