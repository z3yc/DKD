package com.dkd.agent;

import java.util.Arrays;
import javax.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.filter.AgentCallbackAuthFilter;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/**
 * 回调鉴权过滤器单测（任务 0-7）。
 *
 * <p>按 AGENTS §8“拒绝路径优先”要求，重点覆盖四条拒绝路径：无密钥 401、伪造密钥 401、
 * 未配置密钥 503（绝不默认放行）、非白名单路径 404；另覆盖正确密钥放行。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class AgentCallbackAuthFilterTest
{
    private static final String SECRET = "test-service-secret";

    private final FilterChain chain = mock(FilterChain.class);

    @Test
    void 正确密钥且在白名单内的路径放行() throws Exception
    {
        MockHttpServletRequest request = callbackRequest("/agent/callback/task", SECRET);
        MockHttpServletResponse response = new MockHttpServletResponse();

        new AgentCallbackAuthFilter(properties(SECRET)).doFilter(request, response, chain);

        verify(chain).doFilter(eq(request), eq(response));
        assertThat(response.getStatus()).isEqualTo(200);
    }

    @Test
    void 无密钥返回401且不进入业务() throws Exception
    {
        MockHttpServletRequest request = callbackRequest("/agent/callback/task", null);
        MockHttpServletResponse response = new MockHttpServletResponse();

        new AgentCallbackAuthFilter(properties(SECRET)).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(401);
        assertThat(envelopeCode(response)).isEqualTo(401);
        assertThat(response.getContentAsString()).contains("服务间密钥校验失败");
        verify(chain, never()).doFilter(any(), any());
    }

    @Test
    void 伪造密钥返回401且不进入业务() throws Exception
    {
        MockHttpServletRequest request = callbackRequest("/agent/callback/task", SECRET + "-forged");
        MockHttpServletResponse response = new MockHttpServletResponse();

        new AgentCallbackAuthFilter(properties(SECRET)).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(401);
        assertThat(envelopeCode(response)).isEqualTo(401);
        verify(chain, never()).doFilter(any(), any());
    }

    @Test
    void 未配置密钥时返回503而不是默认放行() throws Exception
    {
        MockHttpServletRequest request = callbackRequest("/agent/callback/task", SECRET);
        MockHttpServletResponse response = new MockHttpServletResponse();

        // 密钥为空 = 本机忘配，必须 fail-closed，否则回调接口对内网裸奔
        new AgentCallbackAuthFilter(properties("")).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(503);
        assertThat(envelopeCode(response)).isEqualTo(503);
        verify(chain, never()).doFilter(any(), any());
    }

    @Test
    void 非白名单路径返回404且不进入业务() throws Exception
    {
        MockHttpServletRequest request = callbackRequest("/agent/callback/unknown", SECRET);
        MockHttpServletResponse response = new MockHttpServletResponse();

        new AgentCallbackAuthFilter(properties(SECRET)).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(404);
        assertThat(envelopeCode(response)).isEqualTo(404);
        assertThat(response.getContentAsString()).contains("回调路径不在白名单内");
        verify(chain, never()).doFilter(any(), any());
    }

    @Test
    void 非白名单路径在密钥错误时优先报401() throws Exception
    {
        // 鉴权先于路由：未通过鉴权的探测不应得到“该路径是否存在”的信息
        MockHttpServletRequest request = callbackRequest("/agent/callback/unknown", "wrong");
        MockHttpServletResponse response = new MockHttpServletResponse();

        new AgentCallbackAuthFilter(properties(SECRET)).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(401);
        verify(chain, never()).doFilter(any(), any());
    }

    @Test
    void 预检请求放行() throws Exception
    {
        MockHttpServletRequest request = new MockHttpServletRequest("OPTIONS", "/agent/callback/task");
        request.addHeader("X-Agent-Secret", SECRET);

        new AgentCallbackAuthFilter(properties(SECRET)).doFilter(request, new MockHttpServletResponse(), chain);

        verify(chain).doFilter(any(), any());
    }

    private MockHttpServletRequest callbackRequest(String uri, String secret)
    {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", uri);
        if (secret != null)
        {
            request.addHeader("X-Agent-Secret", secret);
        }
        return request;
    }

    private AgentProperties properties(String secret)
    {
        AgentProperties properties = new AgentProperties();
        properties.setSecret(secret);
        properties.setCallbackPathWhitelist(Arrays.asList("/agent/callback/task"));
        return properties;
    }

    private int envelopeCode(MockHttpServletResponse response) throws Exception
    {
        JSONObject body = JSON.parseObject(response.getContentAsString());
        return body.getIntValue("code");
    }
}
