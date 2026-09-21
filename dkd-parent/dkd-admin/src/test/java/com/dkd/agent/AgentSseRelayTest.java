package com.dkd.agent;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.controller.AgentGatewayController;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.sun.net.httpserver.HttpExchange;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * 网关 SSE 端到端单测（真实回环 HTTP，任务 0-6 / G1 的 Java 侧证据）。
 *
 * <p>与 {@link AgentGatewayControllerTest}（打桩）互补：这里用真上游进程内服务器，
 * 验证“帧内容、响应头、降级、状态码映射”在真实 HTTP 链路上成立。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class AgentSseRelayTest
{
    private static final String SSE_BODY = "event: meta\ndata: {\"conversation_id\":\"conv-1\"}\n\n"
            + "event: delta\ndata: {\"text\":\"你好\"}\n\n" + "event: done\ndata: {\"finish_reason\":\"echo\"}\n\n";

    @Test
    void 对话转发逐字节透传上游SSE帧() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/chat", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().write(SSE_BODY.getBytes(StandardCharsets.UTF_8));
            exchange.getResponseBody().close();
        }))
        {
            AgentGatewayController controller = controller(server.baseUrl(), true);
            MockHttpServletResponse response = new MockHttpServletResponse();

            controller.chat(chatRequest("rid-sse-1"), response);

            assertThat(response.getContentAsString()).as("帧必须原样透传（不解析、不重组）").isEqualTo(SSE_BODY);
            assertThat(response.getContentType()).isEqualTo("text/event-stream;charset=UTF-8");
            assertThat(response.getHeader(AgentHeaders.REQUEST_ID)).isEqualTo("rid-sse-1");
            assertThat(server.getRequests().get(0).header(AgentHeaders.USER)).isEqualTo("7");
        }
    }

    @Test
    void 对话转发保留上游错误帧() throws Exception
    {
        String errorBody = "event: error\ndata: {\"msg\":\"token 超限\",\"code\":429}\n\n";
        try (FakeAgentServer server = new FakeAgentServer("/agent/chat", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().write(errorBody.getBytes(StandardCharsets.UTF_8));
            exchange.getResponseBody().close();
        }))
        {
            AgentGatewayController controller = controller(server.baseUrl(), true);
            MockHttpServletResponse response = new MockHttpServletResponse();

            controller.chat(chatRequest("rid-sse-2"), response);

            assertThat(response.getContentAsString()).as("业务错误帧（如 429 限额）不能被网关改写").isEqualTo(errorBody);
        }
    }

    @Test
    void 上游不可达时输出降级帧而非抛异常() throws Exception
    {
        AgentGatewayController controller = controller("http://127.0.0.1:" + FakeAgentServer.closedPort(), true);
        MockHttpServletResponse response = new MockHttpServletResponse();

        controller.chat(chatRequest("rid-sse-3"), response);

        String body = response.getContentAsString();
        assertThat(body).contains("event: error").contains("智能体服务暂不可用").contains("\"degrade\":true")
                .contains("event: done").contains("\"finish_reason\":\"degrade\"");
        assertThat(response.getContentType()).as("降级也必须是 SSE，前端只按事件流解析该路径").isEqualTo(
                "text/event-stream;charset=UTF-8");
    }

    @Test
    void 泛化代理遇上游5xx降级为503() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/diagnose/1",
                exchange -> FakeAgentServer.respond(exchange, 500, "application/json", "{\"code\":500,\"msg\":\"内部错误\"}")))
        {
            AgentGatewayController controller = controller(server.baseUrl(), true);

            ResponseEntity<byte[]> response = controller.proxy(new MockHttpServletRequest("GET", "/agent/diagnose/1"));

            assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
            JSONObject body = JSON.parseObject(new String(response.getBody(), StandardCharsets.UTF_8));
            assertThat(body.getIntValue("code")).isEqualTo(503);
            assertThat(new String(response.getBody(), StandardCharsets.UTF_8)).doesNotContain("内部错误");
        }
    }

    @Test
    void 泛化代理透传上游4xx信封与状态码() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/restock/plans",
                exchange -> FakeAgentServer.respond(exchange, 422, "application/json", "{\"code\":422,\"msg\":\"参数校验失败\"}")))
        {
            AgentGatewayController controller = controller(server.baseUrl(), true);

            ResponseEntity<byte[]> response = controller.proxy(new MockHttpServletRequest("POST", "/agent/restock/plans"));

            assertThat(response.getStatusCode()).isEqualTo(HttpStatus.UNPROCESSABLE_ENTITY);
            assertThat(new String(response.getBody(), StandardCharsets.UTF_8)).contains("参数校验失败");
        }
    }

    @Test
    void 状态接口与真实上游对齐() throws Exception
    {
        AtomicReference<String> healthHit = new AtomicReference<String>();
        try (FakeAgentServer server = new FakeAgentServer("/health", exchange -> {
            healthHit.set(exchange.getRequestURI().getPath());
            FakeAgentServer.respond(exchange, 200, "application/json", "{\"status\":\"ok\"}");
        }))
        {
            AgentGatewayController controller = controller(server.baseUrl(), true);

            JSONObject data = JSON.parseObject(JSON.toJSONString(controller.status().get("data")));

            assertThat(data.getBooleanValue("enabled")).isTrue();
            assertThat(data.getString("upstream")).isEqualTo("up");
            assertThat(healthHit.get()).isEqualTo("/health");
        }
    }

    private AgentGatewayController controller(String baseUrl, boolean enabled)
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl(baseUrl);
        properties.setEnabled(enabled);
        properties.setSecret("test-service-secret");
        properties.setConnectTimeout(500);
        properties.setReadTimeout(5000);
        return new AgentGatewayController(properties, new AgentUpstreamClient(properties));
    }

    private MockHttpServletRequest chatRequest(String requestId)
    {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        request.setAttribute(AgentUserContext.ATTRIBUTE, new AgentUserContext(7L, "ops",
                new LinkedHashSet<String>(Arrays.asList("admin")), null));
        request.setAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE, requestId);
        request.setContentType("application/json");
        request.setContent("{\"message\":\"ping\",\"scene\":1}".getBytes(StandardCharsets.UTF_8));
        return request;
    }
}
