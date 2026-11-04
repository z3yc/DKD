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

import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

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
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentMcpClient;
import com.dkd.manage.domain.AgentMcpServer;
import com.dkd.manage.domain.dto.AgentMcpServerDto;
import com.dkd.manage.domain.dto.AgentMcpServerEnabledDto;
import com.dkd.manage.domain.vo.AgentMcpServerVo;
import com.dkd.manage.mapper.AgentMcpServerMapper;
import com.dkd.manage.service.impl.AgentMcpServerServiceImpl;

/**
 * MCP server 配置 Service 单测（排期 3-7）。
 *
 * <p>重点覆盖"该拒绝的有没有拒绝"（AGENTS §8）：
 * <ul>
 *   <li><b>stdio 明确拒绝</b>：本期只支持 http，不能静默改回或忽略；</li>
 *   <li><b>readOnlyOnly 固定 1</b>：允许改这个开关 = 允许接入写工具，绕过人工确认红线；</li>
 *   <li><b>envKeys 只收变量名</b>：密钥形态的值（sk-...）/小写变量名被正则拒绝，杜绝密钥落库；</li>
 *   <li><b>范围与唯一性</b>：timeoutMs 1000~5000、rowLimit 1~200、编码唯一且编辑只读。</li>
 * </ul>
 * 全部 Mapper/Client 用 Mockito 打桩，不连真库/真上游。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentMcpServerServiceTest
{
    private static final String OPERATOR = "tester";

    private static final String ENV_JSON = "{\"X-Api-Key\":\"DKD_MCP_X_KEY\"}";

    @Mock
    private AgentMcpServerMapper agentMcpServerMapper;

    @Mock
    private AgentMcpClient agentMcpClient;

    @InjectMocks
    private AgentMcpServerServiceImpl agentMcpServerService;

    // ------------------------------------------------------------------------------------
    // 数据构造
    // ------------------------------------------------------------------------------------

    private static AgentMcpServer existingServer()
    {
        AgentMcpServer server = new AgentMcpServer();
        server.setId(Long.valueOf(7L));
        server.setCode("demo");
        server.setName("MCP Demo");
        server.setTransport("http");
        server.setEndpoint("https://mcp.example.com/rpc");
        server.setEnvKeysJson(ENV_JSON);
        server.setEnabled(Integer.valueOf(1));
        server.setTimeoutMs(Integer.valueOf(5000));
        server.setRowLimit(Integer.valueOf(200));
        server.setReadOnlyOnly(Integer.valueOf(1));
        server.setStatus("ok");
        server.setDelFlag("0");
        return server;
    }

    private static AgentMcpServerDto newDto()
    {
        AgentMcpServerDto dto = new AgentMcpServerDto();
        dto.setCode("demo2");
        dto.setName("MCP Demo 2");
        dto.setTransport("http");
        dto.setEndpoint("https://mcp.example.com/rpc");
        Map<String, String> envKeys = new LinkedHashMap<String, String>();
        envKeys.put("X-Api-Key", "DKD_MCP_X_KEY");
        dto.setEnvKeys(envKeys);
        dto.setTimeoutMs(Integer.valueOf(3000));
        dto.setRowLimit(Integer.valueOf(100));
        return dto;
    }

    private void stubInsert()
    {
        when(agentMcpServerMapper.insertAgentMcpServer(any(AgentMcpServer.class)))
                .thenAnswer(new Answer<Integer>()
                {
                    @Override
                    public Integer answer(InvocationOnMock invocation)
                    {
                        AgentMcpServer inserted = invocation.getArgument(0);
                        inserted.setId(Long.valueOf(11L));
                        return Integer.valueOf(1);
                    }
                });
        AgentMcpServer stored = existingServer();
        stored.setId(Long.valueOf(11L));
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(11L))).thenReturn(stored);
    }

    private void stubUpdate()
    {
        when(agentMcpServerMapper.updateAgentMcpServer(any(AgentMcpServer.class))).thenReturn(Integer.valueOf(1));
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(7L))).thenReturn(existingServer());
    }

    // ------------------------------------------------------------------------------------
    // 新增
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("新增成功：默认停用、只读强制 1、envKeys 只存变量名、审计列写入")
    void insertAppliesSafeDefaults()
    {
        when(agentMcpServerMapper.countAgentMcpServerByCode("demo2", null)).thenReturn(Integer.valueOf(0));
        stubInsert();

        agentMcpServerService.insertServer(newDto(), OPERATOR);

        ArgumentCaptor<AgentMcpServer> captor = ArgumentCaptor.forClass(AgentMcpServer.class);
        verify(agentMcpServerMapper).insertAgentMcpServer(captor.capture());
        AgentMcpServer inserted = captor.getValue();
        assertThat(inserted.getEnabled()).as("默认停用：必须人工安全复核后再开").isEqualTo(0);
        assertThat(inserted.getReadOnlyOnly()).isEqualTo(1);
        assertThat(inserted.getTimeoutMs()).isEqualTo(3000);
        assertThat(inserted.getRowLimit()).isEqualTo(100);
        assertThat(inserted.getStatus()).isEqualTo("unknown");
        assertThat(inserted.getCreateBy()).isEqualTo(OPERATOR);
        assertThat(inserted.getDelFlag()).isEqualTo("0");
        assertThat(inserted.getEnvKeysJson()).contains("DKD_MCP_X_KEY").doesNotContain("sk-");
    }

    @Test
    @DisplayName("新增：显式 enabled=1 被接受（审计仍可追溯）")
    void insertAcceptsExplicitEnabled()
    {
        when(agentMcpServerMapper.countAgentMcpServerByCode("demo2", null)).thenReturn(Integer.valueOf(0));
        stubInsert();
        AgentMcpServerDto dto = newDto();
        dto.setEnabled(Integer.valueOf(1));

        agentMcpServerService.insertServer(dto, OPERATOR);

        ArgumentCaptor<AgentMcpServer> captor = ArgumentCaptor.forClass(AgentMcpServer.class);
        verify(agentMcpServerMapper).insertAgentMcpServer(captor.capture());
        assertThat(captor.getValue().getEnabled()).isEqualTo(1);
    }

    @Test
    @DisplayName("新增：编码重复/格式非法 → 字段错误")
    void insertRejectsCode()
    {
        when(agentMcpServerMapper.countAgentMcpServerByCode("demo2", null)).thenReturn(Integer.valueOf(1));
        assertThatThrownBy(() -> agentMcpServerService.insertServer(newDto(), OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("编码已存在");

        AgentMcpServerDto bad = newDto();
        bad.setCode("Demo2");
        assertThatThrownBy(() -> agentMcpServerService.insertServer(bad, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("编码格式不正确");
        verify(agentMcpServerMapper, never()).insertAgentMcpServer(any(AgentMcpServer.class));
    }

    @Test
    @DisplayName("新增：stdio transport 明确拒绝（本期仅 http）")
    void insertRejectsStdioTransport()
    {
        AgentMcpServerDto dto = newDto();
        dto.setTransport("stdio");

        assertThatThrownBy(() -> agentMcpServerService.insertServer(dto, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("stdio")
                .hasMessageContaining("仅支持 http");
        verify(agentMcpServerMapper, never()).insertAgentMcpServer(any(AgentMcpServer.class));
    }

    @Test
    @DisplayName("新增：未知 transport / endpoint 非 http(s) 拒绝")
    void insertRejectsTransportAndEndpoint()
    {
        AgentMcpServerDto unknown = newDto();
        unknown.setTransport("sse");
        assertThatThrownBy(() -> agentMcpServerService.insertServer(unknown, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("未知 transport");

        AgentMcpServerDto badEndpoint = newDto();
        badEndpoint.setEndpoint("ftp://mcp.example.com");
        assertThatThrownBy(() -> agentMcpServerService.insertServer(badEndpoint, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("http:// 或 https://");
    }

    @Test
    @DisplayName("新增：timeoutMs 1000~5000、rowLimit 1~200 越界拒绝")
    void insertRejectsOutOfRangeNumbers()
    {
        AgentMcpServerDto timeout = newDto();
        timeout.setTimeoutMs(Integer.valueOf(999));
        assertThatThrownBy(() -> agentMcpServerService.insertServer(timeout, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("超时需在 1000~5000");

        AgentMcpServerDto rowLimit = newDto();
        rowLimit.setRowLimit(Integer.valueOf(201));
        assertThatThrownBy(() -> agentMcpServerService.insertServer(rowLimit, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("行数上限需在 1~200");
    }

    @Test
    @DisplayName("新增：readOnlyOnly 非 1 拒绝（写工具不接入的红线）")
    void insertRejectsReadOnlyOnlyChange()
    {
        AgentMcpServerDto dto = newDto();
        dto.setReadOnlyOnly(Integer.valueOf(0));

        assertThatThrownBy(() -> agentMcpServerService.insertServer(dto, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("只读限制固定为 1");
    }

    @Test
    @DisplayName("新增：envKeys 头名非法/大小写重复拒绝")
    void insertRejectsInvalidHeaderNames()
    {
        AgentMcpServerDto badHeader = newDto();
        Map<String, String> bad = new LinkedHashMap<String, String>();
        bad.put("X Api Key", "DKD_MCP_X_KEY");
        badHeader.setEnvKeys(bad);
        assertThatThrownBy(() -> agentMcpServerService.insertServer(badHeader, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("HTTP 头名不合法");

        AgentMcpServerDto duplicate = newDto();
        Map<String, String> dup = new LinkedHashMap<String, String>();
        dup.put("X-Api-Key", "DKD_MCP_X_KEY");
        dup.put("x-api-key", "DKD_MCP_Y_KEY");
        duplicate.setEnvKeys(dup);
        assertThatThrownBy(() -> agentMcpServerService.insertServer(duplicate, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("头名重复");
    }

    @Test
    @DisplayName("新增：envKeys 值必须是环境变量名——密钥形态值被拒绝（防密钥落库）")
    void insertRejectsSecretValueInEnvKeys()
    {
        AgentMcpServerDto lowercase = newDto();
        Map<String, String> lowercaseMap = new LinkedHashMap<String, String>();
        lowercaseMap.put("X-Api-Key", "dkd_mcp_key");
        lowercase.setEnvKeys(lowercaseMap);
        assertThatThrownBy(() -> agentMcpServerService.insertServer(lowercase, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("只填变量名不要填密钥值");

        AgentMcpServerDto secretLike = newDto();
        Map<String, String> secretMap = new LinkedHashMap<String, String>();
        secretMap.put("Authorization", "sk-abcdefghijklmnopqrstuvwxyz0123456789");
        secretLike.setEnvKeys(secretMap);
        assertThatThrownBy(() -> agentMcpServerService.insertServer(secretLike, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("只填变量名不要填密钥值");
        verify(agentMcpServerMapper, never()).insertAgentMcpServer(any(AgentMcpServer.class));
    }

    // ------------------------------------------------------------------------------------
    // 编辑 / 启停
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("编辑：编码不可修改 → 字段错误")
    void updateRejectsCodeChange()
    {
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(7L))).thenReturn(existingServer());
        AgentMcpServerDto dto = newDto();
        dto.setId(Long.valueOf(7L));
        dto.setCode("renamed");

        assertThatThrownBy(() -> agentMcpServerService.updateServer(dto, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("编码不可修改");
    }

    @Test
    @DisplayName("编辑：不存在 → 业务错误")
    void updateRejectsMissingServer()
    {
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(7L))).thenReturn(null);
        AgentMcpServerDto dto = newDto();
        dto.setId(Long.valueOf(7L));

        assertThatThrownBy(() -> agentMcpServerService.updateServer(dto, OPERATOR))
                .isInstanceOf(ServiceException.class).hasMessageContaining("MCP server 不存在");
    }

    @Test
    @DisplayName("编辑：不传 envKeys 沿用旧映射；不传 enabled 沿用当前值")
    void updateKeepsUnspecifiedFields()
    {
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(7L))).thenReturn(existingServer());
        stubUpdate();
        AgentMcpServerDto dto = newDto();
        dto.setId(Long.valueOf(7L));
        dto.setCode("demo");
        dto.setEnvKeys(null);
        dto.setEnabled(null);

        agentMcpServerService.updateServer(dto, OPERATOR);

        ArgumentCaptor<AgentMcpServer> captor = ArgumentCaptor.forClass(AgentMcpServer.class);
        verify(agentMcpServerMapper).updateAgentMcpServer(captor.capture());
        assertThat(captor.getValue().getEnvKeysJson()).isEqualTo(ENV_JSON);
        assertThat(captor.getValue().getEnabled()).isEqualTo(1);
        assertThat(captor.getValue().getReadOnlyOnly()).isEqualTo(1);
        assertThat(captor.getValue().getUpdateBy()).isEqualTo(OPERATOR);
    }

    @Test
    @DisplayName("启停：成功写入；非法值/不存在拒绝")
    void updateEnabledValidations()
    {
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(7L))).thenReturn(existingServer());
        when(agentMcpServerMapper.updateAgentMcpServerEnabled(eq(Long.valueOf(7L)), any(), eq(OPERATOR), any()))
                .thenReturn(Integer.valueOf(1));
        AgentMcpServerEnabledDto dto = new AgentMcpServerEnabledDto();
        dto.setEnabled(Integer.valueOf(0));

        agentMcpServerService.updateEnabled(Long.valueOf(7L), dto, OPERATOR);

        verify(agentMcpServerMapper).updateAgentMcpServerEnabled(eq(Long.valueOf(7L)), eq(Integer.valueOf(0)),
                eq(OPERATOR), any());
        assertThatThrownBy(() -> agentMcpServerService.updateEnabled(Long.valueOf(7L), null, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("启用状态必填");
        AgentMcpServerEnabledDto invalid = new AgentMcpServerEnabledDto();
        invalid.setEnabled(Integer.valueOf(2));
        assertThatThrownBy(() -> agentMcpServerService.updateEnabled(Long.valueOf(7L), invalid, OPERATOR))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("只能是 0 或 1");
    }

    // ------------------------------------------------------------------------------------
    // 代理
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("probe/sync/tools：按 server code 代理，Python 不可达异常向上抛给控制器映射")
    void proxyCallsUseServerCode()
    {
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(7L))).thenReturn(existingServer());
        Object probeData = new LinkedHashMap<String, Object>();
        when(agentMcpClient.probe("demo", "rid-1")).thenReturn(probeData);
        when(agentMcpClient.sync("demo", "rid-1")).thenReturn(probeData);
        when(agentMcpClient.tools("demo", "rid-1")).thenReturn(probeData);

        assertThat(agentMcpServerService.probe(Long.valueOf(7L), "rid-1")).isSameAs(probeData);
        assertThat(agentMcpServerService.sync(Long.valueOf(7L), "rid-1")).isSameAs(probeData);
        assertThat(agentMcpServerService.tools(Long.valueOf(7L), "rid-1")).isSameAs(probeData);
        verify(agentMcpClient).probe("demo", "rid-1");
        verify(agentMcpClient).sync("demo", "rid-1");
        verify(agentMcpClient).tools("demo", "rid-1");
    }

    @Test
    @DisplayName("代理：server 不存在 → 业务错误且不触达 Python")
    void proxyRejectsMissingServer()
    {
        when(agentMcpServerMapper.selectAgentMcpServerById(Long.valueOf(9L))).thenReturn(null);

        assertThatThrownBy(() -> agentMcpServerService.probe(Long.valueOf(9L), "rid-1"))
                .isInstanceOf(ServiceException.class).hasMessageContaining("MCP server 不存在");
        verifyNoInteractions(agentMcpClient);
    }

    // ------------------------------------------------------------------------------------
    // 列表与结构红线
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("列表：envKeys 解析为变量名映射；损坏 JSON 不 500（按未配置处理）")
    void listParsesEnvKeysAndToleratesCorruptJson()
    {
        AgentMcpServer corrupt = existingServer();
        corrupt.setId(Long.valueOf(8L));
        corrupt.setCode("corrupt");
        corrupt.setEnvKeysJson("{not-json");
        when(agentMcpServerMapper.selectAgentMcpServerList(any(AgentMcpServer.class)))
                .thenReturn(java.util.Arrays.asList(existingServer(), corrupt));

        List<AgentMcpServerVo> list = agentMcpServerService.selectServerList();

        assertThat(list).hasSize(2);
        assertThat(list.get(0).getEnvKeys()).containsEntry("X-Api-Key", "DKD_MCP_X_KEY");
        assertThat(list.get(1).getEnvKeys()).isNotNull().isEmpty();
    }

    @Test
    @DisplayName("DTO 没有任何密钥值字段（envKeys 为变量名映射）——密钥从类型层就进不来")
    void dtoHasNoSecretValueField()
    {
        for (Field field : AgentMcpServerDto.class.getDeclaredFields())
        {
            String name = field.getName().toLowerCase();
            if ("envkeys".equals(name))
            {
                continue;
            }
            assertThat(name).doesNotContain("apikey").doesNotContain("secret").doesNotContain("password")
                    .doesNotContain("token");
        }
        // VO 同样不能有密钥值字段（只有 envKeys 变量名映射）
        for (Field field : AgentMcpServerVo.class.getDeclaredFields())
        {
            String name = field.getName().toLowerCase();
            if ("envkeys".equals(name))
            {
                continue;
            }
            assertThat(name).doesNotContain("apikey").doesNotContain("secret").doesNotContain("password")
                    .doesNotContain("token");
        }
    }
}
