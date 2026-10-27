package com.dkd.manage.service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.manage.domain.AgentRun;
import com.dkd.manage.domain.AgentStep;
import com.dkd.manage.domain.AgentToolCall;
import com.dkd.manage.mapper.AgentRunMapper;
import com.dkd.manage.service.impl.AgentRunServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * 运行时间线的分组逻辑单测（F-03）。
 *
 * <p>被测语义只有一条：**把工具调用按 step_no 挂到对应轮次**。这是页面逐轮回放的前提，
 * 挂错轮次比"不显示"更糟——排障时会把 B 轮的工具调用读到 A 轮上。
 *
 * <p>为什么用 Mockito：分组是纯内存逻辑，与 SQL 无关（AGENTS §8：Java 单测不依赖 MySQL）。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentRunServiceTest
{
    @Mock
    private AgentRunMapper agentRunMapper;

    @InjectMocks
    private AgentRunServiceImpl agentRunService;

    private AgentRun run(String runId)
    {
        AgentRun run = new AgentRun();
        run.setRunId(runId);
        run.setSceneCode("fault");
        run.setStatus("done");
        return run;
    }

    private AgentStep step(int stepNo, String role)
    {
        AgentStep step = new AgentStep();
        step.setStepNo(stepNo);
        step.setRoleCode(role);
        step.setAction("diagnose");
        return step;
    }

    private AgentToolCall call(int stepNo, int callSeq, String toolCode)
    {
        AgentToolCall call = new AgentToolCall();
        call.setStepNo(stepNo);
        call.setCallSeq(callSeq);
        call.setToolCode(toolCode);
        return call;
    }

    @Test
    void 工具调用按轮次挂载且保持顺序()
    {
        when(agentRunMapper.selectAgentRunByRunId("r1")).thenReturn(run("r1"));
        when(agentRunMapper.selectAgentStepsByRunId("r1"))
                .thenReturn(new ArrayList<AgentStep>(Arrays.asList(
                        step(1, "diagnostician"), step(2, "dispatcher"))));
        when(agentRunMapper.selectAgentToolCallsByRunId("r1"))
                .thenReturn(new ArrayList<AgentToolCall>(Arrays.asList(
                        call(1, 1, "get_machine_profile"),
                        call(1, 2, "list_order_anomalies"),
                        call(2, 1, "list_region_assignees"))));

        Map<String, Object> timeline = agentRunService.selectTimeline("r1");

        assertThat(timeline.get("run")).isNotNull();
        @SuppressWarnings("unchecked")
        List<AgentStep> steps = (List<AgentStep>) timeline.get("steps");
        assertThat(steps).hasSize(2);
        assertThat(steps.get(0).getToolCalls()).hasSize(2);
        assertThat(steps.get(0).getToolCalls().get(0).getToolCode()).isEqualTo("get_machine_profile");
        assertThat(steps.get(0).getToolCalls().get(1).getToolCode()).isEqualTo("list_order_anomalies");
        assertThat(steps.get(1).getToolCalls()).hasSize(1);
        assertThat(steps.get(1).getToolCalls().get(0).getToolCode()).isEqualTo("list_region_assignees");
    }

    @Test
    void 没有工具调用的轮次给空列表而不是null()
    {
        when(agentRunMapper.selectAgentStepsByRunId("r1"))
                .thenReturn(new ArrayList<AgentStep>(Arrays.asList(step(1, "supervisor"))));
        when(agentRunMapper.selectAgentToolCallsByRunId("r1")).thenReturn(new ArrayList<AgentToolCall>());

        List<AgentStep> steps = agentRunService.selectStepsWithToolCalls("r1");

        // 前端少一处判空；null 会在模板里炸成 undefined.length
        assertThat(steps.get(0).getToolCalls()).isNotNull().isEmpty();
    }

    @Test
    void 找不到轮次的工具调用不被挂到别人身上()
    {
        when(agentRunMapper.selectAgentStepsByRunId("r1"))
                .thenReturn(new ArrayList<AgentStep>(Arrays.asList(step(1, "diagnostician"))));
        when(agentRunMapper.selectAgentToolCallsByRunId("r1"))
                .thenReturn(new ArrayList<AgentToolCall>(Arrays.asList(
                        // step_no=9 没有对应轮次：属写入侧缺 step 的数据缺陷
                        call(9, 1, "orphan_tool"))));

        List<AgentStep> steps = agentRunService.selectStepsWithToolCalls("r1");

        assertThat(steps).hasSize(1);
        assertThat(steps.get(0).getToolCalls()).isEmpty();
    }

    @Test
    void 运行不存在时返回空轮次列表()
    {
        when(agentRunMapper.selectAgentRunByRunId("missing")).thenReturn(null);

        Map<String, Object> timeline = agentRunService.selectTimeline("missing");

        assertThat(timeline.get("run")).isNull();
        @SuppressWarnings("unchecked")
        List<AgentStep> steps = (List<AgentStep>) timeline.get("steps");
        assertThat(steps).isEmpty();
    }
}
