package com.dkd.manage.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowable;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;

/**
 * MCP 管理代理客户端单测（排期 3-7）。
 *
 * <p>用本地假 Python 服务（{@link FakeMcpServer}，回环随机端口、不触网）验证：
 * <ol>
 *   <li>probe/sync/tools 三个代理的路径/方法/查询串/密钥头/requestId 都符合 Python 契约；</li>
 *   <li>信封 data 被正确解包；非 2xx / 信封失败 / 空响应明确失败；</li>
 *   <li>传输失败（503 语义）与上游拒绝（502 语义）区分，401 明示"服务间密钥"而非"不可达"；</li>
 *   <li>异常消息不泄露上游响应体（AGENTS §6.1）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentMcpClientTest
{
    private static final String SECRET = "test-service-secret";

    private static final String REQUEST_ID = "rid-mcp-1";

    private static AgentProperties properties(String baseUrl)
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl(baseUrl);
        properties.setSecret(SECRET);
        properties.setConnectTimeout(2000);
        return properties;
    }

    private static FakeMcpServer server(int status, String body) throws java.io.IOException
    {
        return new FakeMcpServer(exchange -> FakeMcpServer.respond(exchange, status, body));
    }

    @Test
    @DisplayName("探测：POST /agents/mcp/probe/{code}，解包 data 并带密钥/requestId")
    void probeUnwrapsData() throws Exception
    {
        try (FakeMcpServer server = server(200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":{\"ok\":true,\"latencyMs\":120,"
                        + "\"tools\":[{\"name\":\"read_x\"}],\"rejectedCount\":1,\"errorType\":null}}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            Object data = client.probe("demo", REQUEST_ID);

            FakeMcpServer.Recorded recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("POST");
            assertThat(recorded.getPath()).isEqualTo("/agents/mcp/probe/demo");
            assertThat(recorded.header(AgentHeaders.SECRET)).isEqualTo(SECRET);
            assertThat(recorded.header(AgentHeaders.REQUEST_ID)).isEqualTo(REQUEST_ID);
            assertThat(recorded.getBody()).isEqualTo("{}");
            JSONObject json = (JSONObject) data;
            assertThat(json.getBooleanValue("ok")).isTrue();
            assertThat(json.getIntValue("latencyMs")).isEqualTo(120);
            assertThat(json.getIntValue("rejectedCount")).isEqualTo(1);
        }
    }

    @Test
    @DisplayName("同步：POST /agents/mcp/sync/{code}，解包 synced/rejected/disabled")
    void syncUnwrapsData() throws Exception
    {
        try (FakeMcpServer server = server(200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":{\"synced\":3,\"rejected\":1,\"disabled\":2}}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            JSONObject data = (JSONObject) client.sync("demo", REQUEST_ID);

            assertThat(server.getRequests().get(0).getPath()).isEqualTo("/agents/mcp/sync/demo");
            assertThat(data.getIntValue("synced")).isEqualTo(3);
            assertThat(data.getIntValue("rejected")).isEqualTo(1);
            assertThat(data.getIntValue("disabled")).isEqualTo(2);
        }
    }

    @Test
    @DisplayName("工具清单：GET /agents/mcp/tools?serverCode={code}")
    void toolsCarriesServerCodeQuery() throws Exception
    {
        try (FakeMcpServer server = server(200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":[{\"code\":\"mcp.demo.read_x\",\"readOnly\":1}]}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            Object data = client.tools("demo", REQUEST_ID);

            FakeMcpServer.Recorded recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("GET");
            assertThat(recorded.getPath()).isEqualTo("/agents/mcp/tools");
            assertThat(recorded.getQuery()).contains("serverCode=demo");
            assertThat((java.util.List<?>) data).hasSize(1);
        }
    }

    @Test
    @DisplayName("上游 500：归因为 502 语义且不泄露响应体")
    void mapsUpstream500WithoutLeakingBody() throws Exception
    {
        try (FakeMcpServer server = server(500,
                "{\"code\":500,\"msg\":\"SQL error: SELECT * FROM agent_mcp_server ... token=abc\"}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.probe("demo", REQUEST_ID)).isInstanceOf(AgentMcpUpstreamException.class)
                    .hasMessageContaining("HTTP 500").hasMessageNotContaining("SQL").hasMessageNotContaining("token");
            AgentMcpUpstreamException e = (AgentMcpUpstreamException) catchThrowable(
                    () -> client.probe("demo", REQUEST_ID));
            assertThat(e.isTransportFailure()).isFalse();
            assertThat(e.getHttpStatus()).isEqualTo(500);
        }
    }

    @Test
    @DisplayName("401（带体请求的 HttpRetryException 陷阱）：提示鉴权，不报成不可达")
    void maps401AsAuthenticationFailure() throws Exception
    {
        try (FakeMcpServer server = server(401, "{\"detail\":\"服务间密钥校验失败\"}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.sync("demo", REQUEST_ID)).isInstanceOf(AgentMcpUpstreamException.class)
                    .hasMessageContaining("鉴权失败").hasMessageContaining("401");
            AgentMcpUpstreamException e = (AgentMcpUpstreamException) catchThrowable(
                    () -> client.sync("demo", REQUEST_ID));
            assertThat(e.isTransportFailure()).isFalse();
            assertThat(e.getHttpStatus()).isEqualTo(401);
        }
    }

    @Test
    @DisplayName("Python 不可达：transportFailure=true（控制器据此返回 503）")
    void marksTransportFailure()
    {
        int port = 0;
        try
        {
            port = FakeMcpServer.closedPort();
        }
        catch (java.io.IOException e)
        {
            throw new IllegalStateException(e);
        }
        AgentMcpClient client = new AgentMcpClient(properties("http://127.0.0.1:" + port));

        AgentMcpUpstreamException e = (AgentMcpUpstreamException) catchThrowable(
                () -> client.probe("demo", REQUEST_ID));

        assertThat(e).isNotNull();
        assertThat(e.isTransportFailure()).isTrue();
        assertThat(e.getHttpStatus()).isNull();
    }

    @Test
    @DisplayName("404（未启用/不存在）：映射成可操作提示而不是笼统错误")
    void maps404AsActionableMessage() throws Exception
    {
        try (FakeMcpServer server = server(404,
                "{\"code\":404,\"msg\":\"MCP server 不存在或未启用\"}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.probe("demo", REQUEST_ID))
                    .isInstanceOf(AgentMcpUpstreamException.class)
                    .hasMessageContaining("未启用")
                    .hasMessageContaining("请先启用");
            AgentMcpUpstreamException e = (AgentMcpUpstreamException) catchThrowable(
                    () -> client.probe("demo", REQUEST_ID));
            assertThat(e.isTransportFailure()).isFalse();
            assertThat(e.getHttpStatus()).isEqualTo(404);
        }
    }

    @Test
    @DisplayName("HTTP 200 但信封 code!=200：按失败处理，不把错误信封当成功数据")
    void rejectsFailedEnvelope() throws Exception
    {
        try (FakeMcpServer server = server(200, "{\"code\":404,\"msg\":\"server 不存在\",\"data\":null}"))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.probe("demo", REQUEST_ID))
                    .isInstanceOf(AgentMcpUpstreamException.class).hasMessageContaining("code=404");
        }
    }

    @Test
    @DisplayName("空响应体：明确失败")
    void rejectsEmptyBody() throws Exception
    {
        try (FakeMcpServer server = server(200, ""))
        {
            AgentMcpClient client = new AgentMcpClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.tools("demo", REQUEST_ID))
                    .isInstanceOf(AgentMcpUpstreamException.class).hasMessageContaining("空响应");
        }
    }
}
