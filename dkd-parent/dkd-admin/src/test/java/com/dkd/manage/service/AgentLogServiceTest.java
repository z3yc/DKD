package com.dkd.manage.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.text.SimpleDateFormat;
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
import com.dkd.manage.agent.AgentQueryWindow;
import com.dkd.manage.domain.AgentLog;
import com.dkd.manage.mapper.AgentLogMapper;
import com.dkd.manage.service.impl.AgentLogServiceImpl;

/**
 * 执行日志只读 Service 单测（Batch A · 排期 2-4）。
 *
 * <p>测试边界：只测 Service 的**护栏与透传**，Mapper 全部 Mockito 打桩，不连 MySQL
 * （AGENTS §8）。SQL 形状另有 {@code AgentLogMapperSqlTest} 用 XML 文本断言守。
 *
 * <p>重点覆盖"该拒绝的有没有拒绝"：
 * 时间窗超过 30 天必须在**查库前**被拒绝（不是查完再报错），且 Mapper 一次都不该被调用。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentLogServiceTest
{
    @Mock
    private AgentLogMapper agentLogMapper;

    @InjectMocks
    private AgentLogServiceImpl agentLogService;

    private static AgentLog log(String level, String message)
    {
        AgentLog log = new AgentLog();
        log.setLevel(level);
        log.setMessage(message);
        return log;
    }

    @Test
    @DisplayName("时间窗超过 30 天：抛业务异常，且完全不查库")
    void 时间窗超限时查库前拒绝()
    {
        AgentLog query = new AgentLog();
        query.setBeginTime("2026-10-01");
        query.setEndTime("2026-11-04");

        assertThatThrownBy(() -> agentLogService.selectAgentLogList(query))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("最大 30 天");
        // 关键：护栏在查库之前生效——否则"先全表扫一遍再报错"等于把主库拖慢一次
        verifyNoInteractions(agentLogMapper);
    }

    @Test
    @DisplayName("时间窗格式非法：明确拒绝并指出字段")
    void 时间窗格式非法时拒绝()
    {
        AgentLog query = new AgentLog();
        query.setEndTime("2026/11/04 25:00:00");

        assertThatThrownBy(() -> agentLogService.selectAgentLogList(query))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("endTime 格式不合法");
        verifyNoInteractions(agentLogMapper);
    }

    @Test
    @DisplayName("过滤条件透传：精确列原样、关键词去首尾空白")
    void 过滤条件透传到Mapper()
    {
        when(agentLogMapper.selectAgentLogList(any(AgentLog.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentLog>());
        AgentLog query = new AgentLog();
        query.setLevel(" ERROR ");
        query.setStatus(" timeout ");
        query.setRunId(" run-1 ");
        query.setRequestId(" req-1 ");
        query.setKeyword("  超时  ");

        agentLogService.selectAgentLogList(query);

        ArgumentCaptor<AgentLog> queryCaptor = ArgumentCaptor.forClass(AgentLog.class);
        verify(agentLogMapper).selectAgentLogList(queryCaptor.capture(), any(AgentQueryWindow.class));
        AgentLog passed = queryCaptor.getValue();
        assertThat(passed.getLevel()).isEqualTo("ERROR");
        assertThat(passed.getStatus()).isEqualTo("timeout");
        assertThat(passed.getRunId()).isEqualTo("run-1");
        assertThat(passed.getRequestId()).isEqualTo("req-1");
        // " 关键词 " 若不 trim 会变成 like '% 关键词 %'，查不到还没有任何提示
        assertThat(passed.getKeyword()).isEqualTo("超时");
    }

    @Test
    @DisplayName("时间窗归一化后传给 Mapper：日期串上界补齐到 23:59:59.999")
    void 归一化时间窗传给Mapper() throws Exception
    {
        when(agentLogMapper.selectAgentLogList(any(AgentLog.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentLog>());
        AgentLog query = new AgentLog();
        query.setBeginTime("2026-11-01");
        query.setEndTime("2026-11-03");

        agentLogService.selectAgentLogList(query);

        ArgumentCaptor<AgentQueryWindow> windowCaptor = ArgumentCaptor.forClass(AgentQueryWindow.class);
        verify(agentLogMapper).selectAgentLogList(any(AgentLog.class), windowCaptor.capture());
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
        assertThat(format.format(windowCaptor.getValue().getBeginTime())).isEqualTo("2026-11-01 00:00:00");
        // 11-03 当天整日都被包含（毫秒级 999）
        assertThat(windowCaptor.getValue().getEndTime().getTime())
                .isEqualTo(format.parse("2026-11-03 23:59:59").getTime() + 999L);
    }

    @Test
    @DisplayName("默认时间窗：不传时是最近 24 小时")
    void 不传时间时默认近24小时()
    {
        when(agentLogMapper.selectAgentLogList(any(AgentLog.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentLog>());

        agentLogService.selectAgentLogList(new AgentLog());

        ArgumentCaptor<AgentQueryWindow> windowCaptor = ArgumentCaptor.forClass(AgentQueryWindow.class);
        verify(agentLogMapper).selectAgentLogList(any(AgentLog.class), windowCaptor.capture());
        long span = windowCaptor.getValue().getEndTime().getTime()
                - windowCaptor.getValue().getBeginTime().getTime();
        assertThat(span).isEqualTo(24L * 60L * 60L * 1000L);
    }

    @Test
    @DisplayName("列表结果原样返回（Service 不做二次过滤）")
    void 列表结果透传()
    {
        when(agentLogMapper.selectAgentLogList(any(AgentLog.class), any(AgentQueryWindow.class)))
                .thenReturn(new ArrayList<AgentLog>(Arrays.asList(
                        log("ERROR", "LLM 调用超时"), log("INFO", "补货分析开始"))));

        List<AgentLog> list = agentLogService.selectAgentLogList(new AgentLog());

        assertThat(list).hasSize(2);
        assertThat(list.get(0).getLevel()).isEqualTo("ERROR");
        assertThat(list.get(1).getMessage()).isEqualTo("补货分析开始");
    }
}
