package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.assertj.core.api.ThrowableAssert.ThrowingCallable;
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

import com.dkd.common.exception.ServiceException;
import com.dkd.manage.agent.AgentConfigConflictException;
import com.dkd.manage.agent.AgentConfigReloadNotifier;
import com.dkd.manage.agent.AgentConfigValidationException;
import com.dkd.manage.domain.AgentConfig;
import com.dkd.manage.domain.AgentConfigHistory;
import com.dkd.manage.domain.dto.AgentConfigDto;
import com.dkd.manage.domain.dto.AgentConfigRollbackDto;
import com.dkd.manage.domain.vo.AgentConfigVo;
import com.dkd.manage.mapper.AgentConfigMapper;
import com.dkd.manage.service.impl.AgentConfigServiceImpl;

/**
 * 智能体配置 Service 单测（排期 2-2，契约 §4.8）。
 *
 * <p>测试边界：只测 Service 的**校验链 / 版本链 / 快照与回滚**，Mapper 全部 Mockito 打桩，
 * 不连 MySQL（AGENTS §8）。SQL 形状另有 {@code AgentConfigMapperSqlTest} 守。
 *
 * <p>重点覆盖的是**该拒绝的有没有拒绝**（AGENTS §8：「该拒绝的没拒绝」比「该通过的没通过」严重）：
 * 名称重复 / 未知占位符 / 工具越界 / 参数区间矛盾 / 停用无原因 / 内置改 code / 非 chat 新增 / 乐观锁冲突。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentConfigServiceTest
{
    private static final String OPERATOR = "tester";

    @Mock
    private AgentConfigMapper agentConfigMapper;

    @Mock
    private AgentConfigReloadNotifier reloadNotifier;

    @InjectMocks
    private AgentConfigServiceImpl agentConfigService;

    // ------------------------------------------------------------------------------------
    // 正向路径
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("新增成功：写 v1、scene=101、同事务写历史快照，并通知 Python 重载")
    void 新增成功_写入v1与历史快照并通知重载()
    {
        stubInsertSuccess();

        AgentConfigVo vo = agentConfigService.insertAgentConfig(newChatDto(), OPERATOR);

        assertThat(vo.getId()).isEqualTo(9L);
        assertThat(vo.getVersion()).isEqualTo(1L);
        assertThat(vo.getScene()).isEqualTo(101);
        assertThat(vo.getBuiltin()).isEqualTo(0);
        assertThat(vo.getParamsJson()).isEmpty();

        ArgumentCaptor<AgentConfig> configCaptor = ArgumentCaptor.forClass(AgentConfig.class);
        verify(agentConfigMapper).insertAgentConfig(configCaptor.capture());
        AgentConfig inserted = configCaptor.getValue();
        assertThat(inserted.getVersion()).isEqualTo(1L);
        assertThat(inserted.getRuntime()).isEqualTo("chat");
        assertThat(inserted.getBuiltin()).isEqualTo(0);
        assertThat(inserted.getScene()).isEqualTo(101);
        assertThat(inserted.getCreateBy()).isEqualTo(OPERATOR);
        assertThat(inserted.getDelFlag()).isEqualTo("0");

        ArgumentCaptor<AgentConfigHistory> historyCaptor = ArgumentCaptor.forClass(AgentConfigHistory.class);
        verify(agentConfigMapper).insertAgentConfigHistory(historyCaptor.capture());
        AgentConfigHistory history = historyCaptor.getValue();
        assertThat(history.getVersion()).isEqualTo(1L);
        assertThat(history.getCode()).isEqualTo("my_assistant");
        // 快照是 snake_case 整行 JSON，params_json 内嵌为对象（无参数时为 null）
        assertThat(history.getSnapshot()).contains("\"version\":1")
                .contains("\"code\":\"my_assistant\"")
                .contains("\"system_prompt\"")
                .contains("\"params_json\":{}");
        verify(reloadNotifier).notifyReload();
    }

    @Test
    @DisplayName("编辑成功：CAS 用提交版本，落库后版本 +1 并追加一条历史快照")
    void 编辑成功_版本加一并追加历史()
    {
        AgentConfig current = chatRow(5L, 3L);
        when(agentConfigMapper.selectAgentConfigById(5L)).thenReturn(current);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        when(agentConfigMapper.updateAgentConfig(any(AgentConfig.class))).thenReturn(1);

        AgentConfigDto dto = editDtoOf(current);
        dto.setName("我的助手改名");
        dto.setVersion(3L);
        dto.setChangeNote("改名");

        AgentConfigVo vo = agentConfigService.updateAgentConfig(dto, OPERATOR);

        assertThat(vo.getVersion()).isEqualTo(4L);
        assertThat(vo.getName()).isEqualTo("我的助手改名");

        ArgumentCaptor<AgentConfig> updateCaptor = ArgumentCaptor.forClass(AgentConfig.class);
        verify(agentConfigMapper).updateAgentConfig(updateCaptor.capture());
        // WHERE version = 提交版本（不是新版本）——写错乐观锁就失效了
        assertThat(updateCaptor.getValue().getVersion()).isEqualTo(3L);
        assertThat(updateCaptor.getValue().getUpdateBy()).isEqualTo(OPERATOR);

        ArgumentCaptor<AgentConfigHistory> historyCaptor = ArgumentCaptor.forClass(AgentConfigHistory.class);
        verify(agentConfigMapper).insertAgentConfigHistory(historyCaptor.capture());
        assertThat(historyCaptor.getValue().getVersion()).isEqualTo(4L);
        assertThat(historyCaptor.getValue().getChangeNote()).isEqualTo("改名");
        assertThat(historyCaptor.getValue().getSnapshot()).contains("\"version\":4");
        verify(reloadNotifier).notifyReload();
    }

    @Test
    @DisplayName("启停：版本 +1、写历史；停用原因写入快照")
    void 停用成功_版本加一且写历史()
    {
        AgentConfig current = chatRow(5L, 3L);
        when(agentConfigMapper.selectAgentConfigById(5L)).thenReturn(current);
        when(agentConfigMapper.updateAgentConfig(any(AgentConfig.class))).thenReturn(1);

        com.dkd.manage.domain.dto.AgentConfigEnabledDto dto = new com.dkd.manage.domain.dto.AgentConfigEnabledDto();
        dto.setEnabled(Integer.valueOf(0));
        dto.setDisabledReason("按运营要求临时停用");
        dto.setVersion(Long.valueOf(3L));

        AgentConfigVo vo = agentConfigService.updateEnabled(5L, dto, OPERATOR);

        assertThat(vo.getVersion()).isEqualTo(4L);
        assertThat(vo.getEnabled()).isEqualTo(0);
        assertThat(vo.getDisabledReason()).isEqualTo("按运营要求临时停用");
        verify(reloadNotifier).notifyReload();
    }

    @Test
    @DisplayName("回滚：历史快照复制为 newVersion=当前+1，并再写一条历史（不是原地回退）")
    void 回滚成功_复制历史为新版本并追加历史()
    {
        AgentConfig current = chatRow(5L, 3L);
        current.setName("当前名称");
        when(agentConfigMapper.selectAgentConfigById(5L)).thenReturn(current);
        AgentConfigHistory target = new AgentConfigHistory();
        target.setCode("my_assistant");
        target.setVersion(Long.valueOf(1L));
        target.setSnapshot(snapshotJson("历史名称", "chat", chatPrompt(), 1, null));
        when(agentConfigMapper.selectAgentConfigHistoryByCodeAndVersion("my_assistant", Long.valueOf(1L)))
                .thenReturn(target);
        when(agentConfigMapper.updateAgentConfig(any(AgentConfig.class))).thenReturn(1);

        AgentConfigRollbackDto dto = new AgentConfigRollbackDto();
        dto.setTargetVersion(Long.valueOf(1L));
        dto.setVersion(Long.valueOf(3L));

        AgentConfigVo vo = agentConfigService.rollback(5L, dto, OPERATOR);

        assertThat(vo.getVersion()).isEqualTo(4L);
        assertThat(vo.getName()).isEqualTo("历史名称");
        assertThat(vo.getRuntime()).isEqualTo("chat");

        ArgumentCaptor<AgentConfig> updateCaptor = ArgumentCaptor.forClass(AgentConfig.class);
        verify(agentConfigMapper).updateAgentConfig(updateCaptor.capture());
        assertThat(updateCaptor.getValue().getVersion()).isEqualTo(3L);
        assertThat(updateCaptor.getValue().getName()).isEqualTo("历史名称");

        ArgumentCaptor<AgentConfigHistory> historyCaptor = ArgumentCaptor.forClass(AgentConfigHistory.class);
        verify(agentConfigMapper).insertAgentConfigHistory(historyCaptor.capture());
        assertThat(historyCaptor.getValue().getVersion()).isEqualTo(4L);
        assertThat(historyCaptor.getValue().getChangeNote()).isEqualTo("回滚至 v1");
        assertThat(historyCaptor.getValue().getSnapshot()).contains("\"version\":4");
        verify(reloadNotifier).notifyReload();
    }

    // ------------------------------------------------------------------------------------
    // 拒绝路径（契约 §4.8 点名要求的几条）
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("乐观锁冲突：抛 AgentConfigConflictException（当前 5 / 提交 3），不写历史不通知")
    void 乐观锁冲突_返回当前与提交版本()
    {
        AgentConfig latest = chatRow(5L, 5L);
        when(agentConfigMapper.selectAgentConfigById(5L)).thenReturn(latest);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        when(agentConfigMapper.updateAgentConfig(any(AgentConfig.class))).thenReturn(0);
        AgentConfigDto dto = editDtoOf(latest);
        dto.setVersion(3L);

        AgentConfigConflictException conflict = catchConflict(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.updateAgentConfig(dto, OPERATOR);
            }
        });

        assertThat(conflict.getCurrentVersion()).isEqualTo(5L);
        assertThat(conflict.getSubmittedVersion()).isEqualTo(3L);
        verify(agentConfigMapper, org.mockito.Mockito.never()).insertAgentConfigHistory(any(AgentConfigHistory.class));
        verify(reloadNotifier, org.mockito.Mockito.never()).notifyReload();
    }

    @Test
    @DisplayName("新增被拒：名称重复")
    void 新增被拒_名称重复()
    {
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(0);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(1);
        final AgentConfigDto dto = newChatDto();

        assertField(catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.insertAgentConfig(dto, OPERATOR);
            }
        }), "name");
    }

    @Test
    @DisplayName("新增被拒：提示词出现未知占位符 {window_days}")
    void 新增被拒_未知占位符()
    {
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(0);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        final AgentConfigDto dto = newChatDto();
        dto.setSystemPrompt(chatPrompt() + "{window_days}");

        AgentConfigValidationException e = catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.insertAgentConfig(dto, OPERATOR);
            }
        });
        assertField(e, "systemPrompt");
        assertThat(e.getMessage()).contains("window_days");
    }

    @Test
    @DisplayName("新增被拒：tools 不在白名单")
    void 新增被拒_工具越界()
    {
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(0);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        final AgentConfigDto dto = newChatDto();
        dto.setTools("get_sales_summary,bogus_tool");

        AgentConfigValidationException e = catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.insertAgentConfig(dto, OPERATOR);
            }
        });
        assertField(e, "tools");
        assertThat(e.getMessage()).contains("bogus_tool");
    }

    @Test
    @DisplayName("编辑被拒：restock 的 factor_min >= factor_max")
    void 编辑被拒_补货系数区间矛盾()
    {
        AgentConfig current = restockRow(7L, 2L);
        when(agentConfigMapper.selectAgentConfigById(7L)).thenReturn(current);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        final AgentConfigDto dto = editDtoOf(current);
        dto.setVersion(2L);
        Map<String, Object> params = new LinkedHashMap<String, Object>();
        params.put("factor_min", Double.valueOf(1.8));
        params.put("factor_max", Double.valueOf(1.5));
        dto.setParamsJson(params);

        assertField(catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.updateAgentConfig(dto, OPERATOR);
            }
        }), "paramsJson");
    }

    @Test
    @DisplayName("新增被拒：停用但没有填原因")
    void 新增被拒_停用无原因()
    {
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(0);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        final AgentConfigDto dto = newChatDto();
        dto.setEnabled(Integer.valueOf(0));
        dto.setDisabledReason(null);

        assertField(catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.insertAgentConfig(dto, OPERATOR);
            }
        }), "disabledReason");
    }

    @Test
    @DisplayName("编辑被拒：内置行不允许改 code")
    void 编辑被拒_内置行改编码()
    {
        AgentConfig current = restockRow(7L, 2L);
        when(agentConfigMapper.selectAgentConfigById(7L)).thenReturn(current);
        final AgentConfigDto dto = editDtoOf(current);
        dto.setVersion(2L);
        dto.setCode("restock_v2");

        assertField(catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.updateAgentConfig(dto, OPERATOR);
            }
        }), "code");
    }

    @Test
    @DisplayName("新增被拒：本期只允许 chat 载体（runtime=restock 被拒）")
    void 新增被拒_非chat载体()
    {
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(0);
        final AgentConfigDto dto = newChatDto();
        dto.setRuntime("restock");

        assertField(catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.insertAgentConfig(dto, OPERATOR);
            }
        }), "runtime");
    }

    @Test
    @DisplayName("编辑被拒：chat 载体 paramsJson 非空")
    void 编辑被拒_chat载体带参数()
    {
        AgentConfig current = chatRow(5L, 3L);
        when(agentConfigMapper.selectAgentConfigById(5L)).thenReturn(current);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        final AgentConfigDto dto = editDtoOf(current);
        dto.setVersion(3L);
        Map<String, Object> params = new LinkedHashMap<String, Object>();
        params.put("quantile", Double.valueOf(0.75));
        dto.setParamsJson(params);

        assertField(catchValidation(new ThrowingCallable()
        {
            @Override
            public void call()
            {
                agentConfigService.updateAgentConfig(dto, OPERATOR);
            }
        }), "paramsJson");
    }

    @Test
    @DisplayName("回滚被拒：历史版本不存在 → 业务异常（code 500 通道）")
    void 回滚被拒_历史版本不存在()
    {
        when(agentConfigMapper.selectAgentConfigById(5L)).thenReturn(chatRow(5L, 3L));
        when(agentConfigMapper.selectAgentConfigHistoryByCodeAndVersion("my_assistant", Long.valueOf(99L)))
                .thenReturn(null);
        final AgentConfigRollbackDto dto = new AgentConfigRollbackDto();
        dto.setTargetVersion(Long.valueOf(99L));
        dto.setVersion(Long.valueOf(3L));

        try
        {
            agentConfigService.rollback(5L, dto, OPERATOR);
            org.junit.jupiter.api.Assertions.fail("历史版本不存在时应抛业务异常");
        }
        catch (ServiceException e)
        {
            assertThat(e.getMessage()).contains("不存在");
        }
    }

    @Test
    @DisplayName("详情不存在 → 业务异常")
    void 详情不存在_抛业务异常()
    {
        when(agentConfigMapper.selectAgentConfigById(404L)).thenReturn(null);
        try
        {
            agentConfigService.selectAgentConfigById(404L);
            org.junit.jupiter.api.Assertions.fail("配置不存在时应抛业务异常");
        }
        catch (ServiceException e)
        {
            assertThat(e.getMessage()).contains("不存在");
        }
    }

    // ------------------------------------------------------------------------------------
    // meta（契约 §4.6）
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("meta(runtime=chat)：模型/工具/占位符/默认提示词可用，sceneBase=101，无参数")
    void meta_chat返回新增表单所需元数据()
    {
        Map<String, Object> meta = agentConfigService.selectMeta("chat");

        assertThat(meta.get("sceneBase")).isEqualTo(101);
        assertThat((List<?>) meta.get("models")).contains("deepseek-chat", "deepseek-reasoner");
        assertThat((List<?>) meta.get("tools")).contains("build_device_diagnosis", "get_sales_summary");
        assertThat((List<?>) meta.get("placeholders")).contains("focus", "tool_list", "write_guidance");
        assertThat((String) meta.get("defaultPrompt")).startsWith("你是「帝可得");
        assertThat((Map<?, ?>) meta.get("defaultParams")).isEmpty();
        assertThat((List<?>) meta.get("paramSpecs")).isEmpty();
        assertThat((List<?>) meta.get("runtimes")).hasSize(3);
    }

    @Test
    @DisplayName("meta(runtime=restock)：参数区间与默认值与 Python agent_defaults 对齐")
    void meta_restock返回参数区间与默认值()
    {
        Map<String, Object> meta = agentConfigService.selectMeta("restock");

        List<?> specs = (List<?>) meta.get("paramSpecs");
        assertThat(specs).hasSize(6);
        Map<?, ?> defaults = (Map<?, ?>) meta.get("defaultParams");
        assertThat(defaults.get("quantile")).isEqualTo(Double.valueOf(0.75));
        assertThat(defaults.get("batch_size")).isEqualTo(Integer.valueOf(20));
        assertThat((List<?>) meta.get("placeholders")).contains("fmin", "fmax");
        assertThat((String) meta.get("defaultPrompt")).contains("补货计划的校准助手");
    }

    // ------------------------------------------------------------------------------------
    // 测试夹具
    // ------------------------------------------------------------------------------------

    private void stubInsertSuccess()
    {
        when(agentConfigMapper.countAgentConfigByCode(anyString())).thenReturn(0);
        when(agentConfigMapper.countAgentConfigByName(anyString(), any())).thenReturn(0);
        when(agentConfigMapper.selectMaxScene()).thenReturn(Integer.valueOf(3));
        when(agentConfigMapper.insertAgentConfig(any(AgentConfig.class))).thenAnswer(new Answer<Integer>()
        {
            @Override
            public Integer answer(InvocationOnMock invocation)
            {
                AgentConfig config = invocation.getArgument(0);
                config.setId(Long.valueOf(9L));
                return Integer.valueOf(1);
            }
        });
    }

    private AgentConfigDto newChatDto()
    {
        AgentConfigDto dto = new AgentConfigDto();
        dto.setCode("my_assistant");
        dto.setName("我的助手");
        dto.setDescription("自定义对话智能体");
        dto.setRuntime("chat");
        dto.setSystemPrompt(chatPrompt());
        dto.setTools("get_sales_summary");
        dto.setTimeoutS(Integer.valueOf(60));
        dto.setMaxRetries(Integer.valueOf(2));
        dto.setEnabled(Integer.valueOf(1));
        return dto;
    }

    private AgentConfig chatRow(long id, long version)
    {
        AgentConfig row = new AgentConfig();
        row.setId(Long.valueOf(id));
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
        row.setVersion(Long.valueOf(version));
        row.setDelFlag("0");
        row.setCreateBy("admin");
        row.setCreateTime(new Date());
        return row;
    }

    private AgentConfig restockRow(long id, long version)
    {
        AgentConfig row = new AgentConfig();
        row.setId(Long.valueOf(id));
        row.setCode("restock");
        row.setName("智能补货 Agent");
        row.setDescription("按分位销量与容量约束生成补货建议");
        row.setRuntime("restock");
        row.setScene(Integer.valueOf(2));
        row.setBuiltin(Integer.valueOf(1));
        row.setSystemPrompt(longPrompt("你是自动售货机补货计划的校准助手，只输出 JSON。"));
        row.setTools(null);
        row.setTimeoutS(Integer.valueOf(60));
        row.setMaxRetries(Integer.valueOf(2));
        row.setEnabled(Integer.valueOf(1));
        row.setVersion(Long.valueOf(version));
        row.setDelFlag("0");
        row.setCreateBy("admin");
        row.setCreateTime(new Date());
        return row;
    }

    private AgentConfigDto editDtoOf(AgentConfig row)
    {
        AgentConfigDto dto = new AgentConfigDto();
        dto.setId(row.getId());
        dto.setCode(row.getCode());
        dto.setName(row.getName());
        dto.setDescription(row.getDescription());
        dto.setRuntime(row.getRuntime());
        dto.setBuiltin(row.getBuiltin());
        dto.setSystemPrompt(row.getSystemPrompt());
        dto.setTools(row.getTools());
        dto.setModel(row.getModel());
        dto.setTemperature(row.getTemperature());
        dto.setMaxTokens(row.getMaxTokens());
        dto.setTimeoutS(row.getTimeoutS());
        dto.setMaxRetries(row.getMaxRetries());
        dto.setEnabled(row.getEnabled());
        dto.setDisabledReason(row.getDisabledReason());
        dto.setVersion(row.getVersion());
        return dto;
    }

    /** 长度 ≥100 且含合法占位符的 chat 提示词。 */
    private String chatPrompt()
    {
        return longPrompt("你是帝可得平台的 AI 助手，只能引用工具返回值。")
                + "{focus} {tool_list} {write_guidance}";
    }

    private String longPrompt(String sentence)
    {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < 5; i++)
        {
            builder.append(sentence);
        }
        return builder.toString();
    }

    private String snapshotJson(String name, String runtime, String prompt, int enabled, String disabledReason)
    {
        String reason = disabledReason == null ? "null" : "\"" + disabledReason + "\"";
        return "{\"builtin\":1,\"code\":\"my_assistant\",\"description\":\"历史描述\","
                + "\"disabled_reason\":" + reason + ",\"enabled\":" + enabled + ","
                + "\"max_retries\":2,\"max_tokens\":null,\"model\":null,\"name\":\"" + name + "\","
                + "\"params_json\":null,\"runtime\":\"" + runtime + "\",\"scene\":101,"
                + "\"system_prompt\":\"" + prompt + "\",\"temperature\":1.20,\"timeout_s\":60,"
                + "\"tools\":\"get_sales_summary\",\"version\":1}";
    }

    private AgentConfigValidationException catchValidation(ThrowingCallable callable)
    {
        try
        {
            callable.call();
        }
        catch (AgentConfigValidationException e)
        {
            return e;
        }
        catch (Throwable t)
        {
            throw new AssertionError("期望字段校验异常，实际：" + t, t);
        }
        throw new AssertionError("期望抛出字段校验异常，但没有异常");
    }

    private AgentConfigConflictException catchConflict(ThrowingCallable callable)
    {
        try
        {
            callable.call();
        }
        catch (AgentConfigConflictException e)
        {
            return e;
        }
        catch (Throwable t)
        {
            throw new AssertionError("期望乐观锁冲突异常，实际：" + t, t);
        }
        throw new AssertionError("期望抛出乐观锁冲突异常，但没有异常");
    }

    private void assertField(AgentConfigValidationException e, String field)
    {
        assertThat(e.getField()).isEqualTo(field);
    }
}
