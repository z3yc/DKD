package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.common.exception.ServiceException;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentTool;
import com.dkd.manage.domain.dto.AgentToolEditDto;
import com.dkd.manage.mapper.AgentToolMapper;
import com.dkd.manage.service.impl.AgentToolServiceImpl;

/**
 * 工具中心 Service 单测（排期 3-4）。
 *
 * <p>重点覆盖**拒绝路径**（AGENTS §8：该拒绝的没拒绝比该通过的没通过更严重）：
 * 改 code/source/readOnly/builtin、超时/行数越界、启用值非法、工具不存在。
 * 其中 {@code readOnly} 是红线：把写工具改成只读就能被模型直接调用。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentToolServiceTest
{
    @Mock
    private AgentToolMapper agentToolMapper;

    @InjectMocks
    private AgentToolServiceImpl agentToolService;

    private static AgentTool tool()
    {
        AgentTool tool = new AgentTool();
        tool.setId(Long.valueOf(3L));
        tool.setCode("get_machine_inventory");
        tool.setName("设备库存");
        tool.setScope("chat");
        tool.setSource("local");
        tool.setReadOnly(Integer.valueOf(1));
        tool.setEnabled(Integer.valueOf(1));
        tool.setTimeoutMs(Integer.valueOf(10000));
        tool.setRowLimit(Integer.valueOf(2000));
        tool.setBuiltin(Integer.valueOf(1));
        return tool;
    }

    private static AgentToolEditDto dto()
    {
        AgentToolEditDto dto = new AgentToolEditDto();
        dto.setEnabled(Integer.valueOf(0));
        dto.setTimeoutMs(Integer.valueOf(5000));
        dto.setRowLimit(Integer.valueOf(500));
        return dto;
    }

    @Test
    @DisplayName("列表：过滤值去空白后透传给 Mapper")
    void listTrimsFilters()
    {
        when(agentToolMapper.selectAgentToolList(any(AgentTool.class)))
                .thenReturn(new ArrayList<AgentTool>(Arrays.asList(tool())));
        AgentTool query = new AgentTool();
        query.setScope("  chat ");
        query.setSource(" local ");

        List<AgentTool> list = agentToolService.selectAgentToolList(query);

        ArgumentCaptor<AgentTool> captor = ArgumentCaptor.forClass(AgentTool.class);
        verify(agentToolMapper).selectAgentToolList(captor.capture());
        assertThat(captor.getValue().getScope()).isEqualTo("chat");
        assertThat(captor.getValue().getSource()).isEqualTo("local");
        assertThat(list).hasSize(1);
    }

    @Test
    @DisplayName("工具不存在：业务错误且不发起更新")
    void rejectsMissingTool()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(9L))).thenReturn(null);

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(9L), dto(), "tester"))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("工具不存在");
        verify(agentToolMapper, never()).updateAgentTool(any(AgentTool.class));
    }

    @Test
    @DisplayName("改 code：字段错误（工具编码不可修改）")
    void rejectsCodeChange()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        AgentToolEditDto dto = dto();
        dto.setCode("other_tool");

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), dto, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("编码不可修改");
    }

    @Test
    @DisplayName("改 source：字段错误")
    void rejectsSourceChange()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        AgentToolEditDto dto = dto();
        dto.setSource("mcp");

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), dto, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("来源不可修改");
    }

    @Test
    @DisplayName("改 readOnly：字段错误（红线：写工具绝不能被改成可被模型直调）")
    void rejectsReadOnlyChange()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        AgentToolEditDto dto = dto();
        dto.setReadOnly(Integer.valueOf(0));

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), dto, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("只读属性不可修改");
    }

    @Test
    @DisplayName("改 builtin：字段错误")
    void rejectsBuiltinChange()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        AgentToolEditDto dto = dto();
        dto.setBuiltin(Integer.valueOf(0));

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), dto, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("内置标记不可修改");
    }

    @Test
    @DisplayName("timeoutMs 越界：字段错误（100~30000）")
    void rejectsTimeoutOutOfRange()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());

        AgentToolEditDto tooSmall = dto();
        tooSmall.setTimeoutMs(Integer.valueOf(99));
        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), tooSmall, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("超时需在 100~30000");

        AgentToolEditDto tooLarge = dto();
        tooLarge.setTimeoutMs(Integer.valueOf(30001));
        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), tooLarge, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("超时需在 100~30000");
    }

    @Test
    @DisplayName("rowLimit 越界：字段错误（1~5000）")
    void rejectsRowLimitOutOfRange()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());

        AgentToolEditDto tooSmall = dto();
        tooSmall.setRowLimit(Integer.valueOf(0));
        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), tooSmall, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("行数上限需在 1~5000");

        AgentToolEditDto tooLarge = dto();
        tooLarge.setRowLimit(Integer.valueOf(5001));
        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), tooLarge, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("行数上限需在 1~5000");
    }

    @Test
    @DisplayName("enabled 非 0/1：字段错误")
    void rejectsInvalidEnabled()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        AgentToolEditDto dto = dto();
        dto.setEnabled(Integer.valueOf(2));

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), dto, "tester"))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("启用状态只能是 0 或 1");
    }

    @Test
    @DisplayName("只传 enabled：超时/行数沿用库内值，审计列写入操作者")
    void mergesPartialUpdateWithCurrentValues()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        when(agentToolMapper.updateAgentTool(any(AgentTool.class))).thenReturn(Integer.valueOf(1));
        AgentTool updated = tool();
        updated.setEnabled(Integer.valueOf(0));
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool(), updated);
        AgentToolEditDto dto = new AgentToolEditDto();
        dto.setEnabled(Integer.valueOf(0));

        AgentTool result = agentToolService.updateAgentTool(Long.valueOf(3L), dto, "tester");

        ArgumentCaptor<AgentTool> captor = ArgumentCaptor.forClass(AgentTool.class);
        verify(agentToolMapper).updateAgentTool(captor.capture());
        AgentTool passed = captor.getValue();
        assertThat(passed.getId()).isEqualTo(3L);
        assertThat(passed.getEnabled()).isEqualTo(0);
        // 未传的字段不能被置空/置 0：必须沿用库内值
        assertThat(passed.getTimeoutMs()).isEqualTo(10000);
        assertThat(passed.getRowLimit()).isEqualTo(2000);
        assertThat(passed.getUpdateBy()).isEqualTo("tester");
        assertThat(passed.getUpdateTime()).isNotNull();
        assertThat(result.getEnabled()).isEqualTo(0);
    }

    @Test
    @DisplayName("三个可改字段一个都不传：业务错误（不静默成功）")
    void rejectsEmptyUpdate()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), new AgentToolEditDto(), "tester"))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("至少传一个");
    }

    @Test
    @DisplayName("更新影响 0 行（并发删除）：业务错误而不是假装成功")
    void rejectsUpdateRace()
    {
        when(agentToolMapper.selectAgentToolById(Long.valueOf(3L))).thenReturn(tool());
        when(agentToolMapper.updateAgentTool(any(AgentTool.class))).thenReturn(Integer.valueOf(0));

        assertThatThrownBy(() -> agentToolService.updateAgentTool(Long.valueOf(3L), dto(), "tester"))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("记录可能已被删除");
    }
}
