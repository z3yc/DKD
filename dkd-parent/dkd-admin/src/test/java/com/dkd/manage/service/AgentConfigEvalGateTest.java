package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.util.Date;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.invocation.InvocationOnMock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.mockito.stubbing.Answer;

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.manage.agent.AgentConfigReloadNotifier;
import com.dkd.manage.agent.AgentEvalGateException;
import com.dkd.manage.agent.AgentEvalGateResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentConfig;
import com.dkd.manage.domain.AgentConfigHistory;
import com.dkd.manage.domain.AgentEvalReport;
import com.dkd.manage.domain.dto.AgentConfigDto;
import com.dkd.manage.mapper.AgentConfigMapper;
import com.dkd.manage.service.impl.AgentConfigServiceImpl;

/**
 * 配置保存的**评测护栏**单测（排期 5-1）。
 *
 * <p>核心断言：
 * <ul>
 *   <li><b>关闭 = 现状</b>：evalGateEnabled=false 时完全不碰评测服务（既有 300+ 用例与线上行为不得变）；</li>
 *   <li><b>开启时拦在"没证据/证据不合格"</b>：无报告 / 最新非 scored / 低于基线 → {@link AgentEvalGateException}
 *       （由 Controller 转 HTTP200+code409+data{field:"evalGate"}）；</li>
 *   <li><b>force 不是后门</b>：必须 force=true 且原因 ≥10 字，原因会写进 change_note（版本历史可追溯）；</li>
 *   <li><b>新增不拦</b>：新智能体还没有评测报告，拦了就没法创建。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentConfigEvalGateTest
{
    private static final String OPERATOR = "tester";

    private static final String FORCE_REASON = "评测服务临时故障，紧急修复提示词错别字，已线下确认";

    @Mock
    private AgentConfigMapper agentConfigMapper;

    @Mock
    private AgentConfigReloadNotifier reloadNotifier;

    @Mock
    private AgentProperties agentProperties;

    @Mock
    private IAgentEvalReportService agentEvalReportService;

    @InjectMocks
    private AgentConfigServiceImpl agentConfigService;

    // ------------------------------------------------------------------------------------
    // 夹具
    // ------------------------------------------------------------------------------------

    private static String chatPrompt()
    {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < 5; i++)
        {
            builder.append("你是帝可得平台的 AI 助手，只能引用工具返回值。");
        }
        return builder.append("{focus} {tool_list} {write_guidance}").toString();
    }

    private static AgentConfig currentRow()
    {
        AgentConfig row = new AgentConfig();
        row.setId(Long.valueOf(5L));
        row.setCode("my_assistant");
        row.setName("我的助手");
        row.setDescription("自定义对话智能体");
        row.setRuntime("chat");
        row.setScene(Integer.valueOf(101));
        row.setBuiltin(Integer.valueOf(0));
        row.setSystemPrompt(chatPrompt());
        row.setTools("get_sales_summary");
        row.setTimeoutS(Integer.valueOf(60));
        row.setMaxRetries(Integer.valueOf(2));
        row.setEnabled(Integer.valueOf(1));
        row.setVersion(Long.valueOf(3L));
        row.setDelFlag("0");
        row.setCreateBy("admin");
        row.setCreateTime(new Date());
        return row;
    }

    private static AgentConfigDto editDto()
    {
        AgentConfig row = currentRow();
        AgentConfigDto dto = new AgentConfigDto();
        dto.setId(row.getId());
        dto.setCode(row.getCode());
        dto.setName(row.getName());
        dto.setDescription(row.getDescription());
        dto.setRuntime(row.getRuntime());
        dto.setBuiltin(row.getBuiltin());
        dto.setSystemPrompt(row.getSystemPrompt());
        dto.setTools(row.getTools());
        dto.setTimeoutS(row.getTimeoutS());
        dto.setMaxRetries(row.getMaxRetries());
        dto.setEnabled(row.getEnabled());
        dto.setVersion(Long.valueOf(3L));
        return dto;
    }

    private static AgentEvalReport scoredReport(BigDecimal accuracy, BigDecimal baseline)
    {
        AgentEvalReport report = new AgentEvalReport();
        report.setId(Long.valueOf(1L));
        report.setConfigCode("my_assistant");
        report.setConfigVersion(Long.valueOf(3L));
        report.setStatus("scored");
        report.setAccuracy(accuracy);
        report.setBaselineAccuracy(baseline);
        return report;
    }

    private void stubUpdateSuccess()
    {
        when(agentConfigMapper.selectAgentConfigById(Long.valueOf(5L))).thenReturn(currentRow());
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(Integer.valueOf(0));
        when(agentConfigMapper.updateAgentConfig(any(AgentConfig.class))).thenReturn(Integer.valueOf(1));
    }

    private String capturedHistoryChangeNote()
    {
        ArgumentCaptor<AgentConfigHistory> historyCaptor = ArgumentCaptor.forClass(AgentConfigHistory.class);
        verify(agentConfigMapper).insertAgentConfigHistory(historyCaptor.capture());
        return historyCaptor.getValue().getChangeNote();
    }

    // ------------------------------------------------------------------------------------
    // 关闭 = 现状
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("护栏关闭：完全不访问评测服务，保存行为与改造前一致")
    void gateDisabledKeepsLegacyBehavior()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.FALSE);
        stubUpdateSuccess();

        agentConfigService.updateAgentConfig(editDto(), OPERATOR);

        verifyNoInteractions(agentEvalReportService);
        assertThat(capturedHistoryChangeNote()).isEqualTo("编辑保存");
    }

    @Test
    @DisplayName("护栏开启但状态读不到（依赖未注入）时按关闭处理，不阻断保存")
    void gateEnabledWithoutServiceFallsBackToDisabled()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        stubUpdateSuccess();
        // 把 evalService 置空模拟"未注入"（生产不会发生；保护既有单测/降级环境）
        org.springframework.test.util.ReflectionTestUtils.setField(agentConfigService, "agentEvalReportService", null);

        agentConfigService.updateAgentConfig(editDto(), OPERATOR);

        assertThat(capturedHistoryChangeNote()).isEqualTo("编辑保存");
    }

    // ------------------------------------------------------------------------------------
    // 开启：通过 / 拒绝
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("护栏开启且评测通过：正常保存，不追加强制说明")
    void gateEnabledAndPassedSaves()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        stubUpdateSuccess();
        when(agentEvalReportService.checkGate("my_assistant", Long.valueOf(3L)))
                .thenReturn(AgentEvalGateResult.pass(scoredReport(new BigDecimal("0.90"), new BigDecimal("0.85"))));

        agentConfigService.updateAgentConfig(editDto(), OPERATOR);

        verify(agentEvalReportService).checkGate("my_assistant", Long.valueOf(3L));
        assertThat(capturedHistoryChangeNote()).isEqualTo("编辑保存");
    }

    @Test
    @DisplayName("护栏开启且无评测报告：拒绝保存（不落库）")
    void gateRejectsWhenNoReport()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        when(agentConfigMapper.selectAgentConfigById(Long.valueOf(5L))).thenReturn(currentRow());
        when(agentEvalReportService.checkGate(anyString(), any()))
                .thenReturn(AgentEvalGateResult.reject("当前版本（v3）没有评测报告：请先运行评测再保存", null));

        assertThatThrownBy(() -> agentConfigService.updateAgentConfig(editDto(), OPERATOR))
                .isInstanceOf(AgentEvalGateException.class)
                .hasMessageContaining("没有评测报告");
        verify(agentConfigMapper, never()).updateAgentConfig(any(AgentConfig.class));
    }

    @Test
    @DisplayName("护栏开启且准确率低于基线：拒绝并带上最新报告")
    void gateRejectsWhenBelowBaseline()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        when(agentConfigMapper.selectAgentConfigById(Long.valueOf(5L))).thenReturn(currentRow());
        AgentEvalReport report = scoredReport(new BigDecimal("0.72"), new BigDecimal("0.85"));
        when(agentEvalReportService.checkGate(anyString(), any()))
                .thenReturn(AgentEvalGateResult.reject("评测准确率 0.72 低于基线 0.85", report));

        AgentEvalGateException e = (AgentEvalGateException) org.assertj.core.api.Assertions
                .catchThrowable(() -> agentConfigService.updateAgentConfig(editDto(), OPERATOR));

        assertThat(e.getReason()).contains("低于基线");
        assertThat(e.getLatest()).isSameAs(report);
        verify(agentConfigMapper, never()).updateAgentConfig(any(AgentConfig.class));
    }

    // ------------------------------------------------------------------------------------
    // force：必须给足原因并留痕
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("force=true 但原因不足 10 字：字段错误（表单标红），不落库")
    void forceWithTooShortReasonRejected()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        when(agentConfigMapper.selectAgentConfigById(Long.valueOf(5L))).thenReturn(currentRow());
        when(agentEvalReportService.checkGate(anyString(), any()))
                .thenReturn(AgentEvalGateResult.reject("当前版本没有评测报告", null));
        AgentConfigDto dto = editDto();
        dto.setForce(Boolean.TRUE);
        dto.setForceReason("太短了");

        assertThatThrownBy(() -> agentConfigService.updateAgentConfig(dto, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("至少 10 字");
        verify(agentConfigMapper, never()).updateAgentConfig(any(AgentConfig.class));
    }

    @Test
    @DisplayName("force=false 不能绕过护栏")
    void forceFalseStillRejected()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        when(agentConfigMapper.selectAgentConfigById(Long.valueOf(5L))).thenReturn(currentRow());
        when(agentEvalReportService.checkGate(anyString(), any()))
                .thenReturn(AgentEvalGateResult.reject("当前版本没有评测报告", null));
        AgentConfigDto dto = editDto();
        dto.setForce(Boolean.FALSE);
        dto.setForceReason(FORCE_REASON);

        assertThatThrownBy(() -> agentConfigService.updateAgentConfig(dto, OPERATOR))
                .isInstanceOf(AgentEvalGateException.class);
        verify(agentConfigMapper, never()).updateAgentConfig(any(AgentConfig.class));
    }

    @Test
    @DisplayName("force=true 且原因合格：放行并把「[强制跳过评测] 原因」写进 change_note")
    void forceBypassWritesAuditNote()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        stubUpdateSuccess();
        when(agentEvalReportService.checkGate(anyString(), any()))
                .thenReturn(AgentEvalGateResult.reject("当前版本没有评测报告", null));
        AgentConfigDto dto = editDto();
        dto.setForce(Boolean.TRUE);
        dto.setForceReason(FORCE_REASON);

        agentConfigService.updateAgentConfig(dto, OPERATOR);

        String note = capturedHistoryChangeNote();
        assertThat(note).contains("[强制跳过评测]").contains(FORCE_REASON);
        assertThat(note.length()).isLessThanOrEqualTo(200);
        verify(agentEvalReportService).checkGate(eq("my_assistant"), eq(Long.valueOf(3L)));
    }

    @Test
    @DisplayName("基础变更说明很长时，强制说明优先完整保留（change_note ≤200）")
    void forceNoteKeepsReasonWhenBaseNoteIsLong()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        stubUpdateSuccess();
        when(agentEvalReportService.checkGate(anyString(), any()))
                .thenReturn(AgentEvalGateResult.reject("当前版本没有评测报告", null));
        StringBuilder longNote = new StringBuilder();
        for (int i = 0; i < 200; i++)
        {
            longNote.append('改');
        }
        AgentConfigDto dto = editDto();
        dto.setChangeNote(longNote.toString());
        dto.setForce(Boolean.TRUE);
        dto.setForceReason(FORCE_REASON);

        agentConfigService.updateAgentConfig(dto, OPERATOR);

        String note = capturedHistoryChangeNote();
        assertThat(note.length()).isLessThanOrEqualTo(200);
        assertThat(note).contains("[强制跳过评测]").contains(FORCE_REASON);
    }

    // ------------------------------------------------------------------------------------
    // 新增不拦
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("新增配置：即使护栏开启也不查评测（新智能体还没有报告）")
    void insertIsNeverGated()
    {
        when(agentProperties.getEvalGateEnabled()).thenReturn(Boolean.TRUE);
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(Integer.valueOf(0));
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(Integer.valueOf(0));
        when(agentConfigMapper.selectMaxScene()).thenReturn(Integer.valueOf(3));
        when(agentConfigMapper.insertAgentConfig(any(AgentConfig.class))).thenAnswer(new Answer<Integer>()
        {
            @Override
            public Integer answer(InvocationOnMock invocation)
            {
                AgentConfig inserted = invocation.getArgument(0);
                inserted.setId(Long.valueOf(9L));
                return Integer.valueOf(1);
            }
        });
        AgentConfigDto dto = editDto();
        dto.setId(null);
        dto.setVersion(null);
        dto.setCode("my_assistant");
        dto.setName("新助手");

        agentConfigService.insertAgentConfig(dto, OPERATOR);

        verify(agentEvalReportService, never()).checkGate(anyString(), any());
    }
}
