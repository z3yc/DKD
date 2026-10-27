package com.dkd.agent;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashSet;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.controller.AgentGatewayController;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.core.domain.model.LoginUser;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 网关白名单与权限点单测（P0 安全修复：智能体网关越权写）。
 *
 * <p>为什么必须有这组“拒绝路径”用例（AGENTS §8）：越权类缺陷的严重性在于“该拒绝的没拒绝”，
 * 只测“有权限能通过”无法证明修复有效；因此每条权限点都同时覆盖 403 与放行两侧，
 * 并断言拒绝时**上游一次都没被调用**（拒绝必须发生在转发之前，不能只靠 Python 兜底）。
 *
 * <p>权限点映射（与 {@link AgentGatewayController} 的 requiredPermission 保持一致）：
 * <ul>
 *   <li>GET /agent/restock/plans、GET /agent/restock/pause → {@code manage:task:list}</li>
 *   <li>POST /agent/restock/**（confirm/adjust/skip/restore/pause/resume）→ {@code manage:task:add}</li>
 *   <li>/agent/status、/agent/chat → 仅要求登录（不在本测试范围，保持现状）</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-10-21
 */
class AgentGatewayAuthzTest
{
    private final AgentUpstreamClient upstreamClient = mock(AgentUpstreamClient.class);

    @AfterEach
    void clearSecurityContext()
    {
        SecurityContextHolder.clearContext();
    }

    @Test
    void 白名单外路径一律404且不访问上游()
    {
        AgentGatewayController controller = controller(true);

        // 包含：Python 不存在过的旧路径、POST 读接口、GET 写动作、非 GET/POST 方法
        for (String[] request : Arrays.asList(
                new String[] { "GET", "/agent/diagnose/1" },
                new String[] { "POST", "/agent/restock/plans" },
                new String[] { "GET", "/agent/restock/plans/5/confirm" },
                new String[] { "PUT", "/agent/restock/plans" },
                new String[] { "DELETE", "/agent/restock/resume" }))
        {
            ResponseEntity<byte[]> response = controller.proxy(request(request[0], request[1]));

            assertThat(response.getStatusCode()).as(request[0] + " " + request[1]).isEqualTo(HttpStatus.NOT_FOUND);
            assertThat(body(response).getIntValue("code")).isEqualTo(404);
        }
        verifyNoInteractions(upstreamClient);
    }

    @Test
    void 读接口缺list权限返回403信封()
    {
        AgentGatewayController controller = controller(true);
        login(2L);

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/plans"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        JSONObject json = body(response);
        assertThat(json.getIntValue("code")).as("必须返回 RuoYi AjaxResult 信封，前端统一拦截器才能识别")
                .isEqualTo(403);
        assertThat(json.getString("msg")).contains("无权限");
        verify(upstreamClient, never()).forward(anyString(), anyString(), any(), any(), any());
    }

    @Test
    void 写接口缺add权限返回403且不转发()
    {
        AgentGatewayController controller = controller(true);
        // 只有“能看清单”的权限，没有建单权限：这正是审计发现的低权限账号场景
        login(2L, "manage:task:list");

        ResponseEntity<byte[]> response = controller
                .proxy(request("POST", "/agent/restock/plans/9/confirm"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(body(response).getIntValue("code")).isEqualTo(403);
        verify(upstreamClient, never()).forward(anyString(), anyString(), any(), any(), any());
    }

    @Test
    void 读接口有list权限时转发()
    {
        when(upstreamClient.forward(anyString(), anyString(), any(), any(), any())).thenReturn(okResult());
        AgentGatewayController controller = controller(true);
        login(2L, "manage:task:list");

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/plans"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        verify(upstreamClient).forward(eq("GET"), eq("/agent/restock/plans"), any(), any(), any());
    }

    @Test
    void 写接口有add权限时转发()
    {
        when(upstreamClient.forward(anyString(), anyString(), any(), any(), any())).thenReturn(okResult());
        AgentGatewayController controller = controller(true);
        login(2L, "manage:task:add");

        ResponseEntity<byte[]> response = controller.proxy(request("POST", "/agent/restock/pause"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        verify(upstreamClient).forward(eq("POST"), eq("/agent/restock/pause"), any(), any(), any());
    }

    @Test
    void 超管无显式权限串也放行()
    {
        when(upstreamClient.forward(anyString(), anyString(), any(), any(), any())).thenReturn(okResult());
        AgentGatewayController controller = controller(true);
        // userId=1 是 RuoYi 内置超管：permissions 可能为空（角色配置缺失），判权语义必须与 @ss.hasPermi 一致
        login(1L);

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/pause"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        verify(upstreamClient).forward(eq("GET"), eq("/agent/restock/pause"), any(), any(), any());
    }

    @Test
    void 暂停查询走读权限而非写权限()
    {
        AgentGatewayController controller = controller(true);
        // 只有 add 权限 → GET /pause 应 403（读接口不因“能写”而放行）
        login(2L, "manage:task:add");

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/pause"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        verify(upstreamClient, never()).forward(anyString(), anyString(), any(), any(), any());
    }

    @Test
    void 未登录按403拒绝而非抛异常()
    {
        // 生产链路里 Spring Security 已挡掉未登录；此处覆盖“身份上下文缺失时不得放行、也不得 500”
        AgentGatewayController controller = controller(true);

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/plans"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(body(response).getIntValue("code")).isEqualTo(403);
        verifyNoInteractions(upstreamClient);
    }

    @Test
    void 非数字planId不在白名单内()
    {
        AgentGatewayController controller = controller(true);
        login(1L);

        ResponseEntity<byte[]> response = controller.proxy(
                request("POST", "/agent/restock/plans/not-a-number/confirm"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        verifyNoInteractions(upstreamClient);
    }

    @Test
    void 无权限优先于降级开关()
    {
        AgentGatewayController controller = controller(false);
        login(2L);

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/plans"));

        assertThat(response.getStatusCode()).as("越权请求不应通过 503 得知服务是否存在").isEqualTo(HttpStatus.FORBIDDEN);
        verifyNoInteractions(upstreamClient);
    }

    @Test
    void 有权限但开关关闭仍返回503()
    {
        AgentGatewayController controller = controller(false);
        login(1L);

        ResponseEntity<byte[]> response = controller.proxy(request("GET", "/agent/restock/plans"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(body(response).getIntValue("code")).isEqualTo(503);
        verifyNoInteractions(upstreamClient);
    }

    /**
     * 写入 SecurityContext 的登录用户。
     *
     * @param userId 用户 ID
     * @param permissions 权限串（超管可留空）
     */
    private void login(Long userId, String... permissions)
    {
        LoginUser loginUser = new LoginUser();
        loginUser.setUserId(userId);
        loginUser.setPermissions(new LinkedHashSet<String>(Arrays.asList(permissions)));
        SecurityContextHolder.getContext()
                .setAuthentication(new UsernamePasswordAuthenticationToken(loginUser, null));
    }

    private AgentGatewayController controller(boolean enabled)
    {
        AgentProperties properties = new AgentProperties();
        properties.setEnabled(enabled);
        properties.setBaseUrl("http://127.0.0.1:8090");
        properties.setSecret("test-service-secret");
        return new AgentGatewayController(properties, upstreamClient);
    }

    private MockHttpServletRequest request(String method, String path)
    {
        return new MockHttpServletRequest(method, path);
    }

    private AgentUpstreamResult okResult()
    {
        return new AgentUpstreamResult(200, "application/json",
                "{\"code\":200,\"msg\":\"ok\",\"data\":{}}".getBytes(StandardCharsets.UTF_8), null);
    }

    private JSONObject body(ResponseEntity<byte[]> response)
    {
        return JSON.parseObject(new String(response.getBody(), StandardCharsets.UTF_8));
    }
}
