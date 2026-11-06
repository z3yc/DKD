package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Arrays;
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

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.manage.agent.AgentEvalGateResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentEvalReport;
import com.dkd.manage.domain.dto.AgentEvalReportDto;
import com.dkd.manage.mapper.AgentEvalReportMapper;
import com.dkd.manage.service.impl.AgentEvalReportServiceImpl;

/**
 * 评测报告 Service 单测（排期 5-1）。
 *
 * <p>重点覆盖**该拒绝的有没有拒绝**：
 * <ul>
 *   <li>枚举/计数/accuracy 口径：{@code scored} 必须有准确率，{@code blocked/failed} 必须没有
 *       （否则护栏会拿"没测"当"0 分"、拿"跑挂"当"满分"）；</li>
 *   <li>{@code passed+failed+blocked == total}：分母错了，准确率与护栏判断都无意义；</li>
 *   <li>护栏：无报告/最新非 scored/低于基线 → 拒绝；基线为空不伪造（只要 scored 就放行）；</li>
 *   <li>关闭护栏时 gate 查询恒 passed=true（保存链路会直接跳过，不拿它当拒绝依据）。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentEvalReportServiceTest
{
    private static final String CODE = "my_assistant";

    @Mock
    private AgentEvalReportMapper agentEvalReportMapper;

    @Mock
    private AgentProperties agentProperties;

    @InjectMocks
    private AgentEvalReportServiceImpl agentEvalReportService;

    // ------------------------------------------------------------------------------------
    // 夹具
    // ------------------------------------------------------------------------------------

    private static AgentEvalReportDto newDto()
    {
        AgentEvalReportDto dto = new AgentEvalReportDto();
        dto.setConfigCode(CODE);
        dto.setConfigVersion(Long.valueOf(3L));
        dto.setMode("live");
        dto.setStatus("scored");
        dto.setAccuracy(new BigDecimal("0.87654"));
        dto.setTotal(Integer.valueOf(10));
        dto.setPassed(Integer.valueOf(7));
        dto.setFailed(Integer.valueOf(2));
        dto.setBlocked(Integer.valueOf(1));
        dto.setBaselineAccuracy(new BigDecimal("0.80"));
        dto.setBaselineN(Integer.valueOf(50));
        dto.setReportJson("{\"cases\":[]}");
        return dto;
    }

    private static AgentEvalReport report(String status, BigDecimal accuracy, BigDecimal baseline)
    {
        AgentEvalReport report = new AgentEvalReport();
        report.setId(Long.valueOf(1L));
        report.setConfigCode(CODE);
        report.setConfigVersion(Long.valueOf(3L));
        report.setStatus(status);
        report.setAccuracy(accuracy);
        report.setBaselineAccuracy(baseline);
        return report;
    }

    // ------------------------------------------------------------------------------------
    // 上报：正向
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("上报成功：accuracy 按列精度四舍五入、runBy 缺省取操作者、createTime 由服务端写")
    void insertAppliesRoundingAndDefaults()
    {
        when(agentEvalReportMapper.insertAgentEvalReport(any(AgentEvalReport.class))).thenReturn(Integer.valueOf(1));

        agentEvalReportService.insertReport(newDto(), "ci-runner");

        ArgumentCaptor<AgentEvalReport> captor = ArgumentCaptor.forClass(AgentEvalReport.class);
        verify(agentEvalReportMapper).insertAgentEvalReport(captor.capture());
        AgentEvalReport saved = captor.getValue();
        assertThat(saved.getConfigCode()).isEqualTo(CODE);
        assertThat(saved.getConfigVersion()).isEqualTo(3L);
        assertThat(saved.getMode()).isEqualTo("live");
        assertThat(saved.getStatus()).isEqualTo("scored");
        assertThat(saved.getAccuracy()).isEqualTo(new BigDecimal("0.8765"));
        assertThat(saved.getTotal()).isEqualTo(10);
        assertThat(saved.getPassed()).isEqualTo(7);
        assertThat(saved.getFailed()).isEqualTo(2);
        assertThat(saved.getBlocked()).isEqualTo(1);
        assertThat(saved.getBaselineAccuracy()).isEqualTo(new BigDecimal("0.8000"));
        assertThat(saved.getRunBy()).isEqualTo("ci-runner");
        assertThat(saved.getCreateTime()).isNotNull();
    }

    // ------------------------------------------------------------------------------------
    // 上报：拒绝路径
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("未知 mode/status → 字段错误")
    void insertRejectsUnknownEnums()
    {
        AgentEvalReportDto badMode = newDto();
        badMode.setMode("sandbox");
        assertThatThrownBy(() -> agentEvalReportService.insertReport(badMode, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("未知评测模式");

        AgentEvalReportDto badStatus = newDto();
        badStatus.setStatus("running");
        assertThatThrownBy(() -> agentEvalReportService.insertReport(badStatus, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("未知评测状态");
        verify(agentEvalReportMapper, never()).insertAgentEvalReport(any(AgentEvalReport.class));
    }

    @Test
    @DisplayName("计数不一致（passed+failed+blocked != total）→ 字段错误")
    void insertRejectsInconsistentCounts()
    {
        AgentEvalReportDto dto = newDto();
        dto.setTotal(Integer.valueOf(11));

        assertThatThrownBy(() -> agentEvalReportService.insertReport(dto, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("必须等于用例总数");
    }

    @Test
    @DisplayName("scored 缺准确率 → 拒绝")
    void insertRejectsScoredWithoutAccuracy()
    {
        AgentEvalReportDto dto = newDto();
        dto.setAccuracy(null);

        assertThatThrownBy(() -> agentEvalReportService.insertReport(dto, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("status=scored 时准确率必填");
    }

    @Test
    @DisplayName("blocked/failed 带准确率 → 拒绝（不能伪造'测了但有分数'）")
    void insertRejectsAccuracyForNonScored()
    {
        AgentEvalReportDto blocked = newDto();
        blocked.setStatus("blocked");
        assertThatThrownBy(() -> agentEvalReportService.insertReport(blocked, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("不能填准确率");

        AgentEvalReportDto failed = newDto();
        failed.setStatus("failed");
        failed.setAccuracy(null);
        when(agentEvalReportMapper.insertAgentEvalReport(any(AgentEvalReport.class))).thenReturn(Integer.valueOf(1));
        // failed 且 accuracy=null 是合法的：应落库且 accuracy=null
        AgentEvalReport saved = agentEvalReportService.insertReport(failed, "ci");
        assertThat(saved.getAccuracy()).isNull();
        assertThat(saved.getStatus()).isEqualTo("failed");
    }

    @Test
    @DisplayName("accuracy 超出 0~1 → 拒绝")
    void insertRejectsAccuracyOutOfRange()
    {
        AgentEvalReportDto above = newDto();
        above.setAccuracy(new BigDecimal("1.2"));
        assertThatThrownBy(() -> agentEvalReportService.insertReport(above, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("0~1");

        AgentEvalReportDto below = newDto();
        below.setAccuracy(new BigDecimal("-0.1"));
        assertThatThrownBy(() -> agentEvalReportService.insertReport(below, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("0~1");
    }

    @Test
    @DisplayName("configCode 非法/版本缺失 → 字段错误")
    void insertRejectsInvalidCodeAndVersion()
    {
        AgentEvalReportDto badCode = newDto();
        badCode.setConfigCode("Bad-Code");
        assertThatThrownBy(() -> agentEvalReportService.insertReport(badCode, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("配置编码格式不正确");

        AgentEvalReportDto zeroVersion = newDto();
        zeroVersion.setConfigVersion(Long.valueOf(0L));
        assertThatThrownBy(() -> agentEvalReportService.insertReport(zeroVersion, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("配置版本必填");
    }

    @Test
    @DisplayName("reportJson 必填且 ≤1MB → 拒绝")
    void insertRejectsBadReportJson()
    {
        AgentEvalReportDto empty = newDto();
        empty.setReportJson("  ");
        assertThatThrownBy(() -> agentEvalReportService.insertReport(empty, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("评测报告正文必填");

        StringBuilder oversized = new StringBuilder(1024 * 1024 + 1);
        for (int i = 0; i <= 1024 * 1024; i++)
        {
            oversized.append('a');
        }
        AgentEvalReportDto tooLarge = newDto();
        tooLarge.setReportJson(oversized.toString());
        assertThatThrownBy(() -> agentEvalReportService.insertReport(tooLarge, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("超过 1MB");
    }

    @Test
    @DisplayName("runBy 超长 → 拒绝")
    void insertRejectsLongRunBy()
    {
        StringBuilder runBy = new StringBuilder();
        for (int i = 0; i < 65; i++)
        {
            runBy.append('r');
        }
        AgentEvalReportDto dto = newDto();
        dto.setRunBy(runBy.toString());

        assertThatThrownBy(() -> agentEvalReportService.insertReport(dto, "ci"))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("执行人最长");
    }

    @Test
    @DisplayName("列表：limit 校验（1~100）并透传")
    void listValidatesLimit()
    {
        when(agentEvalReportMapper.selectLatestByCode(CODE, 5))
                .thenReturn(new ArrayList<AgentEvalReport>(Arrays.asList(report("scored", new BigDecimal("0.9"), null))));

        assertThat(agentEvalReportService.selectLatestByCode(CODE, Integer.valueOf(5))).hasSize(1);
        verify(agentEvalReportMapper).selectLatestByCode(CODE, 5);

        assertThatThrownBy(() -> agentEvalReportService.selectLatestByCode(CODE, Integer.valueOf(0)))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("条数需在 1~100");
        assertThatThrownBy(() -> agentEvalReportService.selectLatestByCode(CODE, Integer.valueOf(101)))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("条数需在 1~100");
    }

    // ------------------------------------------------------------------------------------
    // 护栏判定
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("护栏：无报告 → 拒绝")
    void gateRejectsWithoutReport()
    {
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L))).thenReturn(null);

        AgentEvalGateResult gate = agentEvalReportService.checkGate(CODE, Long.valueOf(3L));

        assertThat(gate.isPassed()).isFalse();
        assertThat(gate.getReason()).contains("没有评测报告");
        assertThat(gate.getLatest()).isNull();
    }

    @Test
    @DisplayName("护栏：最新报告 blocked/failed → 拒绝（不能表现成'从未评测'）")
    void gateRejectsWhenLatestNotScored()
    {
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L)))
                .thenReturn(report("blocked", null, null));

        AgentEvalGateResult gate = agentEvalReportService.checkGate(CODE, Long.valueOf(3L));

        assertThat(gate.isPassed()).isFalse();
        assertThat(gate.getReason()).contains("blocked");
        assertThat(gate.getLatest()).isNotNull();
    }

    @Test
    @DisplayName("护栏：scored 且低于基线 → 拒绝；等于/高于基线 → 通过")
    void gateComparesAccuracyWithBaseline()
    {
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L)))
                .thenReturn(report("scored", new BigDecimal("0.70"), new BigDecimal("0.85")));
        AgentEvalGateResult below = agentEvalReportService.checkGate(CODE, Long.valueOf(3L));
        assertThat(below.isPassed()).isFalse();
        assertThat(below.getReason()).contains("低于基线");

        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L)))
                .thenReturn(report("scored", new BigDecimal("0.85"), new BigDecimal("0.85")));
        assertThat(agentEvalReportService.checkGate(CODE, Long.valueOf(3L)).isPassed()).isTrue();

        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L)))
                .thenReturn(report("scored", new BigDecimal("0.91"), new BigDecimal("0.85")));
        assertThat(agentEvalReportService.checkGate(CODE, Long.valueOf(3L)).isPassed()).isTrue();
    }

    @Test
    @DisplayName("护栏：未设基线（NULL）不伪造，scored 有准确率即通过")
    void gatePassesWhenBaselineIsNull()
    {
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L)))
                .thenReturn(report("scored", new BigDecimal("0.60"), null));

        assertThat(agentEvalReportService.checkGate(CODE, Long.valueOf(3L)).isPassed()).isTrue();
    }

    @Test
    @DisplayName("护栏：scored 但缺准确率（历史脏数据）→ 拒绝，不当成通过")
    void gateRejectsScoredWithoutAccuracy()
    {
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L)))
                .thenReturn(report("scored", null, null));

        AgentEvalGateResult gate = agentEvalReportService.checkGate(CODE, Long.valueOf(3L));

        assertThat(gate.isPassed()).isFalse();
        assertThat(gate.getReason()).contains("缺少准确率");
    }

    // ------------------------------------------------------------------------------------
    // gate 查询（页面预览）
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("gate 查询：护栏关闭时 passed 恒 true，但仍回传最新报告")
    void gateQueryDisabledReportsPassed()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.FALSE);
        AgentEvalReport latest = report("scored", new BigDecimal("0.5"), new BigDecimal("0.9"));
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L))).thenReturn(latest);

        Map<String, Object> gate = agentEvalReportService.selectGate(CODE, Long.valueOf(3L));

        assertThat(gate.get("enabled")).isEqualTo(Boolean.FALSE);
        assertThat(gate.get("passed")).isEqualTo(Boolean.TRUE);
        assertThat(gate.get("latest")).isSameAs(latest);
        assertThat((String) gate.get("reason")).contains("未启用");
    }

    @Test
    @DisplayName("gate 查询：护栏开启且无报告 → passed=false，reason 提示先评测")
    void gateQueryEnabledReportsReject()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        when(agentEvalReportMapper.selectLatestForVersion(CODE, Long.valueOf(3L))).thenReturn(null);

        Map<String, Object> gate = agentEvalReportService.selectGate(CODE, Long.valueOf(3L));

        assertThat(gate.get("enabled")).isEqualTo(Boolean.TRUE);
        assertThat(gate.get("passed")).isEqualTo(Boolean.FALSE);
        assertThat(gate.get("latest")).isNull();
        assertThat((String) gate.get("reason")).contains("没有评测报告");
    }

    @Test
    @DisplayName("gate 查询：未传 configVersion → 不判定（passed=true），如实说明")
    void gateQueryWithoutVersion()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);

        Map<String, Object> gate = agentEvalReportService.selectGate(CODE, null);

        assertThat(gate.get("enabled")).isEqualTo(Boolean.TRUE);
        assertThat(gate.get("passed")).isEqualTo(Boolean.TRUE);
        assertThat(gate.get("latest")).isNull();
        assertThat((String) gate.get("reason")).contains("configVersion");
        verify(agentEvalReportMapper, never()).selectLatestForVersion(anyString(), any());
    }

    @Test
    @DisplayName("DTO 没有密钥字段（报告正文由脚本生成，Java 只做结构校验）")
    void dtoHasNoSecretFields()
    {
        for (Field field : AgentEvalReportDto.class.getDeclaredFields())
        {
            String name = field.getName().toLowerCase();
            assertThat(name).doesNotContain("apikey").doesNotContain("secret").doesNotContain("password");
        }
    }
}
