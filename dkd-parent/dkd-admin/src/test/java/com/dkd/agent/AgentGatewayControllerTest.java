package com.dkd.agent;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.controller.AgentGatewayController;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.agent.support.AgentUpstreamException;
import com.dkd.common.core.domain.AjaxResult;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 网关控制器单测（任务 0-6）：转发头重建、降级语义、回调路径兜底。
 *
 * <p>核心安全断言：**客户端伪造的 X-Agent-\* 头绝不能到达上游**（AGENTS §7.5）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class AgentGatewayControllerTest
{
    private static final String SERVICE_SECRET = "test-service-secret";

    private final AgentUpstreamClient upstreamClient = mock(AgentUpstreamClient.class);

    @Test
    void 转发时重建身份头且丢弃客户端伪造头() throws Exception
    {
        AgentGatewayController controller = controller(true);
        MockHttpServletRequest request = chatRequest("rid-42");
        // 攻击者视角：直接冲网关注入身份/密钥
        request.addHeader("X-Agent-User", "999");
        request.addHeader("X-Agent-Username", "root");
        request.addHeader("X-Agent-Roles", "super-admin");
        request.addHeader("X-Agent-Secret", "forged-secret");
        request.addHeader("Last-Event-ID", "5");

        controller.chat(request, new MockHttpServletResponse());

        ArgumentCaptor<Map<String, String>> headersCaptor = headersCaptor();
        verify(upstreamClient).relayStream(eq("POST"), eq("/agent/chat"), any(), headersCaptor.capture(), any(), any());
        Map<String, String> forwarded = headersCaptor.getValue();
        assertThat(forwarded.get(AgentHeaders.USER)).as("必须以网关解析的身份为准").isEqualTo("7");
        assertThat(forwarded.get(AgentHeaders.USERNAME)).isEqualTo("ops");
        assertThat(forwarded.get(AgentHeaders.ROLES)).isEqualTo("admin");
        assertThat(forwarded.get(AgentHeaders.SECRET)).as("密钥只能来自配置，不能用客户端提交的").isEqualTo(SERVICE_SECRET);
        assertThat(forwarded.get(AgentHeaders.REQUEST_ID)).isEqualTo("rid-42");
        assertThat(forwarded.get(AgentHeaders.LAST_EVENT_ID)).isEqualTo("5");
        assertThat(forwarded.values()).as("伪造值不得出现在任何转发头里").doesNotContain("999", "root", "forged-secret",
                "super-admin");
    }

    @Test
    void 客户端未提供requestId时网关生成并回带() throws Exception
    {
        AgentGatewayController controller = controller(true);
        MockHttpServletRequest request = chatRequest(null);
        MockHttpServletResponse response = new MockHttpServletResponse();

        controller.chat(request, response);

        ArgumentCaptor<Map<String, String>> headersCaptor = headersCaptor();
        verify(upstreamClient).relayStream(anyString(), anyString(), any(), headersCaptor.capture(), any(), any());
        String generated = headersCaptor.getValue().get(AgentHeaders.REQUEST_ID);
        assertThat(generated).isNotBlank();
        assertThat(response.getHeader(AgentHeaders.REQUEST_ID)).as("requestId 必须回带前端便于对日志").isEqualTo(generated);
    }

    @Test
    void SSE响应头禁止缓冲() throws Exception
    {
        AgentGatewayController controller = controller(true);
        MockHttpServletResponse response = new MockHttpServletResponse();

        controller.chat(chatRequest("rid-1"), response);

        assertThat(response.getContentType()).isEqualTo("text/event-stream;charset=UTF-8");
        assertThat(response.getHeader("Cache-Control")).isEqualTo("no-cache, no-transform");
        assertThat(response.getHeader("X-Accel-Buffering")).isEqualTo("no");
    }

    @Test
    void 降级开关关闭时走SSE降级帧且不访问上游() throws Exception
    {
        AgentGatewayController controller = controller(false);
        MockHttpServletResponse response = new MockHttpServletResponse();

        controller.chat(chatRequest("rid-1"), response);

        String body = response.getContentAsString();
        assertThat(body).contains("event: error").contains("智能体服务未启用").contains("\"degrade\":true")
                .contains("event: done");
        verifyNoInteractions(upstreamClient);
    }

    @Test
    void 上游不可达时写降级帧而非抛异常() throws Exception
    {
        doThrow(new AgentUpstreamException(AgentUpstreamException.STATUS_TRANSPORT_FAILURE, "unreachable"))
                .when(upstreamClient).relayStream(anyString(), anyString(), any(), any(), any(), any());
        AgentGatewayController controller = controller(true);
        MockHttpServletResponse response = new MockHttpServletResponse();

        controller.chat(chatRequest("rid-1"), response);

        String body = response.getContentAsString();
        assertThat(body).contains("event: error").contains("智能体服务暂不可用").contains("\"code\":503")
                .contains("event: done");
    }

    @Test
    void 泛化代理遇到上游不可达返回503信封() throws Exception
    {
        when(upstreamClient.forward(anyString(), anyString(), any(), any(), any()))
                .thenThrow(new AgentUpstreamException(AgentUpstreamException.STATUS_TRANSPORT_FAILURE, "unreachable"));
        AgentGatewayController controller = controller(true);

        ResponseEntity<byte[]> response = controller.proxy(new MockHttpServletRequest("GET", "/agent/restock/plans"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        JSONObject body = JSON.parseObject(new String(response.getBody(), StandardCharsets.UTF_8));
        assertThat(body.getIntValue("code")).isEqualTo(503);
        assertThat(body.getString("msg")).isEqualTo("智能体服务暂不可用，请稍后重试");
    }

    @Test
    void 泛化代理不回显上游5xx报文() throws Exception
    {
        when(upstreamClient.forward(anyString(), anyString(), any(), any(), any())).thenReturn(new AgentUpstreamResult(500,
                "application/json", "{\"stack\":\"Traceback ... internal\"}".getBytes(StandardCharsets.UTF_8), null));
        AgentGatewayController controller = controller(true);

        ResponseEntity<byte[]> response = controller.proxy(new MockHttpServletRequest("GET", "/agent/diagnose/1"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(new String(response.getBody(), StandardCharsets.UTF_8)).doesNotContain("Traceback");
    }

    @Test
    void 回调路径禁止经网关访问() throws Exception
    {
        AgentGatewayController controller = controller(true);

        // 含“无尾斜杠”写法：过滤器按 /agent/callback/* 前缀映射注册，盖不到这一种
        for (String callbackPath : Arrays.asList("/agent/callback/task", "/agent/callback"))
        {
            ResponseEntity<byte[]> response = controller.proxy(new MockHttpServletRequest("POST", callbackPath));

            assertThat(response.getStatusCode()).as(callbackPath).isEqualTo(HttpStatus.NOT_FOUND);
            assertThat(new String(response.getBody(), StandardCharsets.UTF_8)).contains("404");
        }
        verify(upstreamClient, never()).forward(anyString(), anyString(), any(), any(), any());
    }

    @Test
    void 状态接口在探活失败时返回down() throws Exception
    {
        when(upstreamClient.pingHealth()).thenReturn(false);
        AgentGatewayController controller = controller(true);

        AjaxResult result = controller.status();

        Map<?, ?> data = (Map<?, ?>) result.get("data");
        assertThat(data.get("enabled")).isEqualTo(Boolean.TRUE);
        assertThat(data.get("upstream")).isEqualTo("down");
    }

    @Test
    void 状态接口在开关关闭时不探活() throws Exception
    {
        AgentGatewayController controller = controller(false);

        AjaxResult result = controller.status();

        Map<?, ?> data = (Map<?, ?>) result.get("data");
        assertThat(data.get("enabled")).isEqualTo(Boolean.FALSE);
        assertThat(data.get("upstream")).isEqualTo("down");
        verify(upstreamClient, never()).pingHealth();
    }

    private AgentGatewayController controller(boolean enabled)
    {
        return new AgentGatewayController(properties(enabled), upstreamClient);
    }

    private AgentProperties properties(boolean enabled)
    {
        AgentProperties properties = new AgentProperties();
        properties.setEnabled(enabled);
        properties.setBaseUrl("http://127.0.0.1:8090");
        properties.setSecret(SERVICE_SECRET);
        return properties;
    }

    private MockHttpServletRequest chatRequest(String requestId)
    {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        request.setAttribute(AgentUserContext.ATTRIBUTE, new AgentUserContext(7L, "ops",
                new LinkedHashSet<String>(Arrays.asList("admin")), null));
        if (requestId != null)
        {
            request.setAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE, requestId);
        }
        request.setContentType("application/json");
        request.setContent("{\"message\":\"ping\",\"scene\":1}".getBytes(StandardCharsets.UTF_8));
        return request;
    }

    @SuppressWarnings("unchecked")
    private ArgumentCaptor<Map<String, String>> headersCaptor()
    {
        return (ArgumentCaptor<Map<String, String>>) (ArgumentCaptor<?>) ArgumentCaptor.forClass(Map.class);
    }
}
