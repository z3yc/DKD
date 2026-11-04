package com.dkd.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.controller.AgentGatewayController;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.agent.support.AgentUpstreamException;
import com.dkd.common.core.domain.AjaxResult;

/**
 * 排期 3-3：{@code GET /agent/status} 扩展 {@code data.agents[]}（场景后端下发）的单测。
 *
 * <p>被测语义与"为什么要这么测"：
 * <ol>
 *   <li><b>成功透传</b>：上游 {@code GET /agent/agents} 的 {@code data.agents} 原样进入
 *       {@code status.data.agents}；请求头只带服务间密钥与 requestId，**不带任何用户身份头**；</li>
 *   <li><b>失败回落</b>：上游不可达/非 2xx/JSON 损坏/结构不符 → {@code agents=[]} + WARN，
 *       且 {@code status} 仍是 HTTP 200。这是 3-3 灰度的核心：前端靠空数组回落硬编码 3 项，
 *       如果这里 500，连 {@code enabled}/{@code upstream} 都拿不到，助手抽屉会整体不可用；</li>
 *   <li><b>开关/探活闸门</b>：{@code enabled=false} 或探活 down 时**不调用**场景上游
 *       （省一次跨进程调用，也避免"开关关了还显示场景"的误导）；</li>
 *   <li><b>fail-closed 不变</b>：前端直连 {@code /agent/agents} 仍 404 —— 该路径只允许 status
 *       内部调用，不因为本次扩展变成新的对外路由。</li>
 * </ol>
 *
 * <p>全部 Mockito 打桩、不连真库/真上游（AGENTS §8）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentGatewayStatusAgentsTest
{
    private static final String SERVICE_SECRET = "test-service-secret";

    private static final String REQUEST_ID = "rid-status-1";

    private final AgentUpstreamClient upstreamClient = org.mockito.Mockito.mock(AgentUpstreamClient.class);

    @Test
    @DisplayName("上游返回场景清单：data.agents 原样透传，且只带密钥+requestId（不伪造用户头）")
    void 场景清单成功透传()
    {
        when(upstreamClient.pingHealth()).thenReturn(true);
        String body = "{\"code\":200,\"msg\":\"ok\",\"data\":{\"agents\":["
                + "{\"code\":\"chat\",\"name\":\"通用助手\",\"scene\":1,\"runtime\":\"chat\","
                + "\"builtin\":1,\"tools\":\"get_machine_inventory\",\"version\":2},"
                + "{\"code\":\"ops_sop\",\"name\":\"运营SOP\",\"scene\":101,\"runtime\":\"chat\","
                + "\"builtin\":0,\"tools\":\"list_restock_suggestions\",\"version\":3}]}}";
        when(upstreamClient.forward(eq("GET"), eq("/agent/agents"), isNull(), anyMap(), any(byte[].class)))
                .thenReturn(new AgentUpstreamResult(200, "application/json", body.getBytes(StandardCharsets.UTF_8),
                        REQUEST_ID));
        AgentGatewayController controller = controller(true);
        MockHttpServletRequest request = statusRequest(REQUEST_ID);
        // 攻击者视角：即使客户端伪造身份头，场景清单请求也不能带出去
        request.addHeader(AgentHeaders.USER, "999");
        request.addHeader(AgentHeaders.ROLES, "super-admin");

        AjaxResult result = controller.status(request);

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        Map<?, ?> data = (Map<?, ?>) result.get("data");
        assertThat(data.get("enabled")).isEqualTo(Boolean.TRUE);
        assertThat(data.get("upstream")).isEqualTo("up");
        List<?> agents = (List<?>) data.get("agents");
        assertThat(agents).hasSize(2);
        Map<?, ?> first = (Map<?, ?>) agents.get(0);
        assertThat(first.get("code")).isEqualTo("chat");
        assertThat(first.get("scene")).isEqualTo(1);
        assertThat(first.get("runtime")).isEqualTo("chat");
        assertThat(first.get("builtin")).isEqualTo(1);
        assertThat(first.get("version")).isEqualTo(2);
        assertThat(((Map<?, ?>) agents.get(1)).get("scene")).isEqualTo(101);

        @SuppressWarnings("unchecked")
        ArgumentCaptor<Map<String, String>> headersCaptor = ArgumentCaptor.forClass(Map.class);
        verify(upstreamClient).forward(eq("GET"), eq("/agent/agents"), isNull(), headersCaptor.capture(),
                any(byte[].class));
        Map<String, String> headers = headersCaptor.getValue();
        assertThat(headers.get(AgentHeaders.SECRET)).as("服务间密钥必须来自配置").isEqualTo(SERVICE_SECRET);
        assertThat(headers.get(AgentHeaders.REQUEST_ID)).as("requestId 必须贯穿上游调用").isEqualTo(REQUEST_ID);
        assertThat(headers).as("场景清单是全局配置，不带任何用户身份头")
                .doesNotContainKeys(AgentHeaders.USER, AgentHeaders.USERNAME, AgentHeaders.ROLES, AgentHeaders.REGION);
        assertThat(headers.values()).as("客户端伪造的身份/密钥不得透传").doesNotContain("999", "super-admin");
    }

    @Test
    @DisplayName("上游不可达：agents=[] + WARN，status 仍 200（前端回落硬编码 3 项）")
    void 上游不可达时回落空数组()
    {
        when(upstreamClient.pingHealth()).thenReturn(true);
        when(upstreamClient.forward(anyString(), anyString(), any(), anyMap(), any(byte[].class)))
                .thenThrow(new AgentUpstreamException(AgentUpstreamException.STATUS_TRANSPORT_FAILURE, "unreachable"));
        AgentGatewayController controller = controller(true);

        AjaxResult result = controller.status(statusRequest(REQUEST_ID));

        assertThat(result.get(AjaxResult.CODE_TAG)).as("场景清单失败不能把 status 打成 500").isEqualTo(200);
        Map<?, ?> data = (Map<?, ?>) result.get("data");
        assertThat(data.get("upstream")).isEqualTo("up");
        assertThat((List<?>) data.get("agents")).isEmpty();
    }

    @Test
    @DisplayName("上游非 2xx：agents=[]，status 仍 200（仍要看得到 enabled/upstream）")
    void 上游非2xx时回落空数组()
    {
        when(upstreamClient.pingHealth()).thenReturn(true);
        when(upstreamClient.forward(anyString(), anyString(), any(), anyMap(), any(byte[].class)))
                .thenReturn(new AgentUpstreamResult(503, "application/json",
                        "{\"code\":503,\"msg\":\"down\"}".getBytes(StandardCharsets.UTF_8), null));
        AgentGatewayController controller = controller(true);

        AjaxResult result = controller.status(statusRequest(REQUEST_ID));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        Map<?, ?> data = (Map<?, ?>) result.get("data");
        assertThat(data.get("upstream")).isEqualTo("up");
        assertThat((List<?>) data.get("agents")).isEmpty();
    }

    @Test
    @DisplayName("上游 2xx 但响应不是合法 JSON：agents=[]，不抛异常")
    void 上游响应JSON损坏时回落空数组()
    {
        when(upstreamClient.pingHealth()).thenReturn(true);
        when(upstreamClient.forward(anyString(), anyString(), any(), anyMap(), any(byte[].class)))
                .thenReturn(new AgentUpstreamResult(200, "application/json",
                        "{\"data\":{\"agents\":[".getBytes(StandardCharsets.UTF_8), null));
        AgentGatewayController controller = controller(true);

        AjaxResult result = controller.status(statusRequest(REQUEST_ID));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat((List<?>) ((Map<?, ?>) result.get("data")).get("agents")).isEmpty();
    }

    @Test
    @DisplayName("上游 2xx 但缺少 data.agents：agents=[]（结构不符按失败回落，不猜别的数组）")
    void 上游响应缺少agents字段时回落空数组()
    {
        when(upstreamClient.pingHealth()).thenReturn(true);
        when(upstreamClient.forward(anyString(), anyString(), any(), anyMap(), any(byte[].class)))
                .thenReturn(new AgentUpstreamResult(200, "application/json",
                        "{\"code\":200,\"data\":{\"items\":[]}}".getBytes(StandardCharsets.UTF_8), null));
        AgentGatewayController controller = controller(true);

        AjaxResult result = controller.status(statusRequest(REQUEST_ID));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat((List<?>) ((Map<?, ?>) result.get("data")).get("agents")).isEmpty();
    }

    @Test
    @DisplayName("enabled=false：agents=[] 且完全不访问上游（探活与场景都不调）")
    void 开关关闭时不访问上游()
    {
        AgentGatewayController controller = controller(false);

        AjaxResult result = controller.status(statusRequest(REQUEST_ID));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        Map<?, ?> data = (Map<?, ?>) result.get("data");
        assertThat(data.get("enabled")).isEqualTo(Boolean.FALSE);
        assertThat(data.get("upstream")).isEqualTo("down");
        assertThat((List<?>) data.get("agents")).isEmpty();
        verifyNoInteractions(upstreamClient);
    }

    @Test
    @DisplayName("探活 down：agents=[] 且不拉场景清单（省一次跨进程调用）")
    void 探活失败时不拉场景清单()
    {
        when(upstreamClient.pingHealth()).thenReturn(false);
        AgentGatewayController controller = controller(true);

        AjaxResult result = controller.status(statusRequest(REQUEST_ID));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat((List<?>) ((Map<?, ?>) result.get("data")).get("agents")).isEmpty();
        verify(upstreamClient, never()).forward(anyString(), anyString(), any(), anyMap(), any(byte[].class));
    }

    @Test
    @DisplayName("fail-closed 不变：前端直连 /agent/agents 仍 404（不新增网关白名单路由）")
    void 场景清单不暴露给前端()
    {
        AgentGatewayController controller = controller(true);

        ResponseEntity<byte[]> response = controller.proxy(new MockHttpServletRequest("GET", "/agent/agents"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        verify(upstreamClient, never()).forward(anyString(), anyString(), any(), anyMap(), any(byte[].class));
    }

    private AgentGatewayController controller(boolean enabled)
    {
        AgentProperties properties = new AgentProperties();
        properties.setEnabled(enabled);
        properties.setBaseUrl("http://127.0.0.1:8090");
        properties.setSecret(SERVICE_SECRET);
        return new AgentGatewayController(properties, upstreamClient);
    }

    private MockHttpServletRequest statusRequest(String requestId)
    {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/agent/status");
        request.setAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE, requestId);
        return request;
    }
}
