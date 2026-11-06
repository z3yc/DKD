package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.invocation.InvocationOnMock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.mockito.stubbing.Answer;

import com.dkd.common.exception.ServiceException;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentLlmProbeClient;
import com.dkd.manage.domain.AgentLlmProbe;
import com.dkd.manage.domain.AgentLlmProfile;
import com.dkd.manage.domain.dto.AgentLlmProfileDto;
import com.dkd.manage.domain.dto.AgentLlmProfileEnabledDto;
import com.dkd.manage.mapper.AgentLlmProfileMapper;
import com.dkd.manage.mapper.AgentLlmProbeMapper;
import com.dkd.manage.service.impl.AgentLlmProfileServiceImpl;

/**
 * 模型档案 Service 单测（排期 3-5）。
 *
 * <p>覆盖三类语义，每类都对应一条"出事会很贵"的路径：
 * <ol>
 *   <li><b>校验拒绝</b>：code/name/baseUrl/apiKeyEnv/timeoutS/maxRetries 越界；
 *       尤其 {@code apiKeyEnv} 必须是**变量名**——把密钥值粘进这个字段会被正则拒绝，
 *       否则密钥会直接落库（AGENTS §7.1 红线）；</li>
 *   <li><b>isDefault 互斥</b>：新增/编辑默认档案时必须**先清其它行再写本行**（顺序断言）；
 *       顺序反了中途失败会留下两个默认档案，页面就会同时显示两个"默认"；</li>
 *   <li><b>探测记录必落库</b>：Python 可达但结论失败 → 不抛异常且记录 ok=0；
 *       Python 不可达 → 先落失败记录再抛业务错误（失败也要留证据）。</li>
 * </ol>
 *
 * <p>全部 Mapper/Client 用 Mockito 打桩，不连真库/真上游（AGENTS §8）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentLlmProfileServiceTest
{
    private static final String OPERATOR = "tester";

    @Mock
    private AgentLlmProfileMapper agentLlmProfileMapper;

    @Mock
    private AgentLlmProbeMapper agentLlmProbeMapper;

    @Mock
    private AgentLlmProbeClient agentLlmProbeClient;

    @InjectMocks
    private AgentLlmProfileServiceImpl agentLlmProfileService;

    // ------------------------------------------------------------------------------------
    // 数据构造
    // ------------------------------------------------------------------------------------

    private static AgentLlmProfile profile()
    {
        AgentLlmProfile profile = new AgentLlmProfile();
        profile.setId(Long.valueOf(5L));
        profile.setCode("deepseek");
        profile.setName("DeepSeek（默认）");
        profile.setProvider("deepseek");
        profile.setBaseUrl("https://api.deepseek.com");
        profile.setModel("deepseek-chat");
        profile.setApiKeyEnv("DKD_DEEPSEEK_API_KEY");
        profile.setTimeoutS(Integer.valueOf(60));
        profile.setMaxRetries(Integer.valueOf(2));
        profile.setEnabled(Integer.valueOf(1));
        profile.setIsDefault(Integer.valueOf(1));
        profile.setDescription("默认档案");
        profile.setDelFlag("0");
        return profile;
    }

    /** 合法新增入参（默认值全部留空，用来验证后端补默认值） */
    private static AgentLlmProfileDto newDto()
    {
        AgentLlmProfileDto dto = new AgentLlmProfileDto();
        dto.setCode("backup");
        dto.setName("备用模型");
        dto.setBaseUrl("https://api.example.com");
        dto.setModel("deepseek-chat");
        dto.setApiKeyEnv("DKD_BACKUP_API_KEY");
        return dto;
    }

    private void stubInsert() 
    {
        when(agentLlmProfileMapper.insertAgentLlmProfile(any(AgentLlmProfile.class)))
                .thenAnswer(new Answer<Integer>()
                {
                    @Override
                    public Integer answer(InvocationOnMock invocation)
                    {
                        AgentLlmProfile inserted = invocation.getArgument(0);
                        inserted.setId(Long.valueOf(11L));
                        return Integer.valueOf(1);
                    }
                });
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(11L))).thenReturn(profile());
    }

    private void stubUpdate()
    {
        when(agentLlmProfileMapper.updateAgentLlmProfile(any(AgentLlmProfile.class))).thenReturn(Integer.valueOf(1));
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
    }

    private static AgentLlmProbeClient.ProbeOutcome outcome(boolean ok, boolean transportFailure, Integer httpStatus,
            int latencyMs, String errorType, String errorMsg)
    {
        AgentLlmProbeClient.ProbeOutcome outcome = new AgentLlmProbeClient.ProbeOutcome();
        outcome.setOk(ok);
        outcome.setTransportFailure(transportFailure);
        outcome.setHttpStatus(httpStatus);
        outcome.setLatencyMs(latencyMs);
        outcome.setModelServed(ok ? "deepseek-flash" : null);
        outcome.setErrorType(errorType);
        outcome.setErrorMsg(errorMsg);
        return outcome;
    }

    // ------------------------------------------------------------------------------------
    // 列表
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("列表：code 过滤去空白后透传")
    void listTrimsCodeFilter()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileList(any(AgentLlmProfile.class)))
                .thenReturn(new ArrayList<AgentLlmProfile>(Arrays.asList(profile())));
        AgentLlmProfile query = new AgentLlmProfile();
        query.setCode("  deepseek ");

        List<AgentLlmProfile> list = agentLlmProfileService.selectAgentLlmProfileList(query);

        ArgumentCaptor<AgentLlmProfile> captor = ArgumentCaptor.forClass(AgentLlmProfile.class);
        verify(agentLlmProfileMapper).selectAgentLlmProfileList(captor.capture());
        assertThat(captor.getValue().getCode()).isEqualTo("deepseek");
        assertThat(list).hasSize(1);
    }

    // ------------------------------------------------------------------------------------
    // 新增
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("新增成功：补默认值（provider/timeoutS/maxRetries/enabled/isDefault）并写审计列")
    void insertAppliesDefaults()
    {
        when(agentLlmProfileMapper.countAgentLlmProfileByCode("backup", null)).thenReturn(Integer.valueOf(0));
        stubInsert();

        agentLlmProfileService.insertAgentLlmProfile(newDto(), OPERATOR);

        ArgumentCaptor<AgentLlmProfile> captor = ArgumentCaptor.forClass(AgentLlmProfile.class);
        verify(agentLlmProfileMapper).insertAgentLlmProfile(captor.capture());
        AgentLlmProfile inserted = captor.getValue();
        assertThat(inserted.getCode()).isEqualTo("backup");
        assertThat(inserted.getProvider()).isEqualTo("openai_compatible");
        assertThat(inserted.getTimeoutS()).isEqualTo(60);
        assertThat(inserted.getMaxRetries()).isEqualTo(2);
        assertThat(inserted.getEnabled()).isEqualTo(1);
        assertThat(inserted.getIsDefault()).isEqualTo(0);
        assertThat(inserted.getCreateBy()).isEqualTo(OPERATOR);
        assertThat(inserted.getCreateTime()).isNotNull();
        assertThat(inserted.getDelFlag()).isEqualTo("0");
        // 非默认档案不应触发"清其它默认"的写操作
        verify(agentLlmProfileMapper, never()).clearDefaultFlag(any(), any(), any(Date.class));
    }

    @Test
    @DisplayName("新增默认档案：先清其它行、再插入（顺序断言，反了会留两个默认）")
    void insertDefaultClearsOthersFirst()
    {
        when(agentLlmProfileMapper.countAgentLlmProfileByCode("backup", null)).thenReturn(Integer.valueOf(0));
        stubInsert();
        AgentLlmProfileDto dto = newDto();
        dto.setIsDefault(Integer.valueOf(1));

        agentLlmProfileService.insertAgentLlmProfile(dto, OPERATOR);

        InOrder order = inOrder(agentLlmProfileMapper);
        order.verify(agentLlmProfileMapper).clearDefaultFlag(isNull(), eq(OPERATOR), any(Date.class));
        order.verify(agentLlmProfileMapper).insertAgentLlmProfile(any(AgentLlmProfile.class));
        ArgumentCaptor<AgentLlmProfile> captor = ArgumentCaptor.forClass(AgentLlmProfile.class);
        verify(agentLlmProfileMapper).insertAgentLlmProfile(captor.capture());
        assertThat(captor.getValue().getIsDefault()).isEqualTo(1);
    }

    @Test
    @DisplayName("新增：编码重复 → 字段错误 code")
    void insertRejectsDuplicateCode()
    {
        when(agentLlmProfileMapper.countAgentLlmProfileByCode("backup", null)).thenReturn(Integer.valueOf(1));

        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(newDto(), OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("编码已存在");
        verify(agentLlmProfileMapper, never()).insertAgentLlmProfile(any(AgentLlmProfile.class));
    }

    @Test
    @DisplayName("新增：编码格式非法 → 字段错误 code")
    void insertRejectsInvalidCode()
    {
        AgentLlmProfileDto upper = newDto();
        upper.setCode("Backup");
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(upper, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("编码格式不正确");

        AgentLlmProfileDto tooShort = newDto();
        tooShort.setCode("ab");
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(tooShort, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("编码格式不正确");
    }

    @Test
    @DisplayName("新增：名称 2~50 字校验")
    void insertRejectsInvalidName()
    {
        AgentLlmProfileDto tooShort = newDto();
        tooShort.setName("a");
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(tooShort, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("名称需 2~50 字");

        StringBuilder longName = new StringBuilder();
        for (int i = 0; i < 51; i++)
        {
            longName.append('a');
        }
        AgentLlmProfileDto tooLong = newDto();
        tooLong.setName(longName.toString());
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(tooLong, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("名称需 2~50 字");
    }

    @Test
    @DisplayName("新增：baseUrl 必须是 http/https")
    void insertRejectsNonHttpBaseUrl()
    {
        AgentLlmProfileDto dto = newDto();
        dto.setBaseUrl("ftp://api.example.com");

        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(dto, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("http:// 或 https://");
    }

    @Test
    @DisplayName("新增：apiKeyEnv 只接受环境变量名——密钥值形态会被拒绝（防密钥落库）")
    void insertRejectsSecretLikeApiKeyEnv()
    {
        AgentLlmProfileDto lowercase = newDto();
        lowercase.setApiKeyEnv("dkd_backup_key");
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(lowercase, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("只填变量名");

        AgentLlmProfileDto secretValue = newDto();
        secretValue.setApiKeyEnv("sk-abcdefghijklmnopqrstuvwxyz0123456789");
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(secretValue, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("只填变量名");
        verify(agentLlmProfileMapper, never()).insertAgentLlmProfile(any(AgentLlmProfile.class));
    }

    @Test
    @DisplayName("新增：timeoutS / maxRetries 越界 → 字段错误")
    void insertRejectsOutOfRangeNumbers()
    {
        AgentLlmProfileDto timeout = newDto();
        timeout.setTimeoutS(Integer.valueOf(301));
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(timeout, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("超时需在 5~300 秒");

        AgentLlmProfileDto retries = newDto();
        retries.setMaxRetries(Integer.valueOf(6));
        assertThatThrownBy(() -> agentLlmProfileService.insertAgentLlmProfile(retries, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("重试次数需在 0~5");
    }

    // ------------------------------------------------------------------------------------
    // 编辑 / 启停
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("编辑：编码不可修改 → 字段错误")
    void updateRejectsCodeChange()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        AgentLlmProfileDto dto = newDto();
        dto.setId(Long.valueOf(5L));
        dto.setCode("renamed");

        assertThatThrownBy(() -> agentLlmProfileService.updateAgentLlmProfile(dto, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("编码不可修改");
    }

    @Test
    @DisplayName("编辑默认档案：先清其它行（排除自己）再更新")
    void updateDefaultClearsOthersExcludingSelf()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        stubUpdate();
        AgentLlmProfileDto dto = newDto();
        dto.setId(Long.valueOf(5L));
        dto.setCode("deepseek");
        dto.setIsDefault(Integer.valueOf(1));

        agentLlmProfileService.updateAgentLlmProfile(dto, OPERATOR);

        verify(agentLlmProfileMapper).clearDefaultFlag(eq(Long.valueOf(5L)), eq(OPERATOR), any(Date.class));
        ArgumentCaptor<AgentLlmProfile> captor = ArgumentCaptor.forClass(AgentLlmProfile.class);
        verify(agentLlmProfileMapper).updateAgentLlmProfile(captor.capture());
        assertThat(captor.getValue().getId()).isEqualTo(5L);
        assertThat(captor.getValue().getIsDefault()).isEqualTo(1);
        assertThat(captor.getValue().getUpdateBy()).isEqualTo(OPERATOR);
    }

    @Test
    @DisplayName("编辑：档案不存在 → 业务错误")
    void updateRejectsMissingProfile()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(null);
        AgentLlmProfileDto dto = newDto();
        dto.setId(Long.valueOf(5L));

        assertThatThrownBy(() -> agentLlmProfileService.updateAgentLlmProfile(dto, OPERATOR))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("模型档案不存在");
    }

    @Test
    @DisplayName("启停：成功写入并返回更新后的档案")
    void updateEnabledWritesFlag()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        when(agentLlmProfileMapper.updateAgentLlmProfileEnabled(eq(Long.valueOf(5L)), any(), eq(OPERATOR),
                any(Date.class))).thenReturn(Integer.valueOf(1));
        AgentLlmProfileEnabledDto dto = new AgentLlmProfileEnabledDto();
        dto.setEnabled(Integer.valueOf(0));

        agentLlmProfileService.updateEnabled(Long.valueOf(5L), dto, OPERATOR);

        verify(agentLlmProfileMapper).updateAgentLlmProfileEnabled(eq(Long.valueOf(5L)), eq(Integer.valueOf(0)),
                eq(OPERATOR), any(Date.class));
    }

    @Test
    @DisplayName("启停：enabled 非 0/1 → 字段错误；不存在 → 业务错误")
    void updateEnabledRejectsInvalid()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        AgentLlmProfileEnabledDto invalid = new AgentLlmProfileEnabledDto();
        invalid.setEnabled(Integer.valueOf(2));
        assertThatThrownBy(() -> agentLlmProfileService.updateEnabled(Long.valueOf(5L), invalid, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("只能是 0 或 1");

        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(6L))).thenReturn(null);
        AgentLlmProfileEnabledDto valid = new AgentLlmProfileEnabledDto();
        valid.setEnabled(Integer.valueOf(1));
        assertThatThrownBy(() -> agentLlmProfileService.updateEnabled(Long.valueOf(6L), valid, OPERATOR))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("模型档案不存在");
    }

    // ------------------------------------------------------------------------------------
    // 连通性探测
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("探测成功：记录 ok=1 并带上模型/延迟/requestId/操作者")
    void probeSuccessWritesRecord()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        when(agentLlmProbeClient.ping(any(AgentLlmProfile.class), eq("rid-probe")))
                .thenReturn(outcome(true, false, Integer.valueOf(200), 321, null, null));
        when(agentLlmProbeMapper.insertAgentLlmProbe(any(AgentLlmProbe.class))).thenReturn(Integer.valueOf(1));

        AgentLlmProbe probe = agentLlmProfileService.probe(Long.valueOf(5L), "rid-probe", OPERATOR);

        assertThat(probe.getOk()).isEqualTo(1);
        assertThat(probe.getProfileCode()).isEqualTo("deepseek");
        assertThat(probe.getLatencyMs()).isEqualTo(321);
        assertThat(probe.getModelServed()).isEqualTo("deepseek-flash");
        assertThat(probe.getRequestId()).isEqualTo("rid-probe");
        assertThat(probe.getCreateBy()).isEqualTo(OPERATOR);
        assertThat(probe.getCreateTime()).isNotNull();
        verify(agentLlmProbeMapper).insertAgentLlmProbe(any(AgentLlmProbe.class));
    }

    @Test
    @DisplayName("探测结论失败（Python 可达）：不抛异常、记录 ok=0，errorMsg 截断到 500")
    void probeConclusionFailureStillReturnsRecord()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        StringBuilder longError = new StringBuilder();
        for (int i = 0; i < 600; i++)
        {
            longError.append('错');
        }
        when(agentLlmProbeClient.ping(any(AgentLlmProfile.class), eq("rid-probe")))
                .thenReturn(outcome(false, false, Integer.valueOf(401), 12, "AuthenticationError",
                        longError.toString()));
        when(agentLlmProbeMapper.insertAgentLlmProbe(any(AgentLlmProbe.class))).thenReturn(Integer.valueOf(1));

        AgentLlmProbe probe = agentLlmProfileService.probe(Long.valueOf(5L), "rid-probe", OPERATOR);

        assertThat(probe.getOk()).isEqualTo(0);
        assertThat(probe.getHttpStatus()).isEqualTo(401);
        assertThat(probe.getErrorType()).isEqualTo("AuthenticationError");
        assertThat(probe.getErrorMsg()).hasSize(500);
    }

    @Test
    @DisplayName("探测：Python 不可达 → 先落失败记录，再抛业务错误（失败也是证据）")
    void probeTransportFailureWritesRecordThenThrows()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        when(agentLlmProbeClient.ping(any(AgentLlmProfile.class), eq("rid-probe")))
                .thenReturn(outcome(false, true, null, 5, "ResourceAccessException", "Connection refused"));
        when(agentLlmProbeMapper.insertAgentLlmProbe(any(AgentLlmProbe.class))).thenReturn(Integer.valueOf(1));

        assertThatThrownBy(() -> agentLlmProfileService.probe(Long.valueOf(5L), "rid-probe", OPERATOR))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("不可达")
                .hasMessageContaining("已记录");

        // 关键：抛异常之前记录必须已经写入（probe 方法刻意不加事务，见实现类注释）
        ArgumentCaptor<AgentLlmProbe> captor = ArgumentCaptor.forClass(AgentLlmProbe.class);
        verify(agentLlmProbeMapper).insertAgentLlmProbe(captor.capture());
        assertThat(captor.getValue().getOk()).isEqualTo(0);
        assertThat(captor.getValue().getErrorType()).isEqualTo("ResourceAccessException");
    }

    @Test
    @DisplayName("探测：档案不存在 → 业务错误且不触达上游、不写记录")
    void probeRejectsMissingProfile()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(9L))).thenReturn(null);

        assertThatThrownBy(() -> agentLlmProfileService.probe(Long.valueOf(9L), "rid-probe", OPERATOR))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("模型档案不存在");
        verifyNoInteractions(agentLlmProbeClient, agentLlmProbeMapper);
    }

    @Test
    @DisplayName("探测记录列表：按档案编码查（不存在 → 业务错误）")
    void selectProbeListByProfileCode()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(5L))).thenReturn(profile());
        when(agentLlmProbeMapper.selectAgentLlmProbeList("deepseek"))
                .thenReturn(new ArrayList<AgentLlmProbe>(Arrays.asList(new AgentLlmProbe())));

        List<AgentLlmProbe> probes = agentLlmProfileService.selectProbeList(Long.valueOf(5L));

        assertThat(probes).hasSize(1);
        verify(agentLlmProbeMapper).selectAgentLlmProbeList("deepseek");

        when(agentLlmProfileMapper.selectAgentLlmProfileById(Long.valueOf(6L))).thenReturn(null);
        assertThatThrownBy(() -> agentLlmProfileService.selectProbeList(Long.valueOf(6L)))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("模型档案不存在");
        verify(agentLlmProbeMapper, never()).selectAgentLlmProbeList(eq(""));
    }

    // ------------------------------------------------------------------------------------
    // 红线：入参/实体结构上不允许密钥
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("入参 DTO 没有任何密钥值字段（apiKeyEnv 除外）——密钥从类型层就进不来")
    void dtoHasNoSecretValueField()
    {
        for (Field field : AgentLlmProfileDto.class.getDeclaredFields())
        {
            String name = field.getName().toLowerCase();
            if ("apikeyenv".equals(name))
            {
                continue;
            }
            assertThat(name).as("DTO 出现疑似密钥字段：" + field.getName()).doesNotContain("apikey")
                    .doesNotContain("secret").doesNotContain("password").isNotEqualTo("token");
        }
    }

    @Test
    @DisplayName("列表原样返回 Mapper 结果（Service 不做二次过滤）")
    void listPassesThrough()
    {
        when(agentLlmProfileMapper.selectAgentLlmProfileList(any(AgentLlmProfile.class)))
                .thenReturn(null);

        List<AgentLlmProfile> list = agentLlmProfileService.selectAgentLlmProfileList(null);

        assertThat(list).isNotNull().isEmpty();
    }
}
