package com.dkd.agent;

import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.controller.AgentGatewayController;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.manage.controller.AgentCallbackController;
import com.dkd.manage.domain.Task;
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.service.ITaskService;
import static org.hamcrest.Matchers.containsString;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 路由优先级测试（任务 0-6 / 0-7 的交叉风险）。
 *
 * <p>为什么必须测：网关有 {@code /agent/**} 泛化代理，回调有 {@code /agent/callback/task} 精确映射，
 * 二者同处 /agent 前缀下。若 Spring 把回调请求派给代理，回调就变成“经网关可访问”，
 * 等于把服务间写入口暴露给前端（安全事件），而且是运行时才暴露、编译期完全看不出来。
 *
 * <p>用 standalone MockMvc：不起 Spring 容器（不连 MySQL/Redis），只验证 MVC 的映射选择。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class AgentRoutingTest
{
    private final AgentUpstreamClient upstreamClient = mock(AgentUpstreamClient.class);

    private final ITaskService taskService = mock(ITaskService.class);

    @Test
    void 回调端点优先命中回调控制器而不是网关代理() throws Exception
    {
        // 打桩必须返回落库实体：回调控制器要用它回传 taskId/taskCode（排期 1-3 起）
        Task created = new Task();
        created.setTaskId(567L);
        created.setTaskCode("202609210001");
        when(taskService.insertTaskDtoReturningTask(any(TaskDto.class))).thenReturn(created);

        MockMvc mockMvc = mockMvc();
        String body = "{\"innerCode\":\"VM-0001\",\"userId\":3,\"productTypeId\":2,"
                + "\"details\":[{\"channelCode\":\"1-1\",\"expectCapacity\":10}]}";

        mockMvc.perform(post("/agent/callback/task").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk()).andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.msg").value("工单创建成功"))
                // 工单号必须回传：Python 侧据此回写 agent_restock_plan.task_id 并做幂等对账
                .andExpect(jsonPath("$.data.taskId").value(567))
                .andExpect(jsonPath("$.data.taskCode").value("202609210001"));

        // 真正证明请求进了回调控制器：服务被调用，且网关上游客户端完全没被碰
        verify(taskService).insertTaskDtoReturningTask(any(TaskDto.class));
        verifyNoInteractions(upstreamClient);
    }

    @Test
    void 对话入口命中网关SSE而不是回调() throws Exception
    {
        mockMvc().perform(post("/agent/chat").contentType(MediaType.APPLICATION_JSON).content("{\"message\":\"hi\"}"))
                .andExpect(status().isOk()).andExpect(header().string("Content-Type", containsString("text/event-stream")));
    }

    @Test
    void 未登记的回调子路径不会被网关转发给上游() throws Exception
    {
        mockMvc().perform(post("/agent/callback/evil").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isNotFound()).andExpect(content().string(containsString("回调路径不可经网关访问")));

        verifyNoInteractions(upstreamClient);
    }

    private MockMvc mockMvc()
    {
        AgentCallbackController callbackController = new AgentCallbackController();
        ReflectionTestUtils.setField(callbackController, "taskService", taskService);
        return MockMvcBuilders.standaloneSetup(callbackController, gatewayController()).build();
    }

    private AgentGatewayController gatewayController()
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl("http://127.0.0.1:8090");
        properties.setEnabled(true);
        properties.setSecret("test-service-secret");
        return new AgentGatewayController(properties, upstreamClient);
    }
}
