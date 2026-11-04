package com.dkd.manage.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;

/**
 * 知识库代理客户端单测（排期 3-6）。
 *
 * <p>用本地假 Python 服务（{@link FakeKbServer}，回环随机端口、不触网）验证：
 * <ol>
 *   <li><b>信封解包</b>：{@code {code,msg,data}} → 只把 data 交给上层；裸 JSON 也兼容；</li>
 *   <li><b>请求形状</b>：路径/方法/查询参数/请求体/服务间密钥头/requestId 头都符合 Python 契约；</li>
 *   <li><b>失败归因</b>：传输失败（503 语义）与"上游有响应但不成功"（502 语义）必须区分，
 *       401 要明确提示"鉴权/密钥"，绝不能报成"服务不可用"（AGENTS §6.1 已知陷阱）；</li>
 *   <li><b>不泄露上游 body</b>：异常消息里不得出现响应体内容（如堆栈/SQL 片段）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentKbClientTest
{
    private static final String SECRET = "test-service-secret";

    private static final String REQUEST_ID = "rid-kb-1";

    private static AgentProperties properties(String baseUrl)
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl(baseUrl);
        properties.setSecret(SECRET);
        properties.setConnectTimeout(2000);
        return properties;
    }

    private static FakeKbServer server(int status, String body) throws IOException
    {
        return new FakeKbServer(Collections.singletonMap("/agents/kb",
                exchange -> FakeKbServer.respond(exchange, status, body)));
    }

    @Test
    @DisplayName("文档列表：解包 data、查询参数编码、带密钥与 requestId 头")
    void listDocumentsUnwrapsDataAndSendsQuery() throws Exception
    {
        try (FakeKbServer server = server(200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":{\"total\":1,\"rows\":[{\"docCode\":\"manual_a\"}]}}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));
            Map<String, String> query = new LinkedHashMap<String, String>();
            query.put("category", "manual");
            query.put("keyword", "补货 策略");
            query.put("pageNum", "2");
            query.put("pageSize", "10");

            Object data = client.listDocuments(query, REQUEST_ID);

            List<FakeKbServer.Recorded> requests = server.getRequests();
            assertThat(requests).hasSize(1);
            FakeKbServer.Recorded recorded = requests.get(0);
            assertThat(recorded.getMethod()).isEqualTo("GET");
            assertThat(recorded.getPath()).isEqualTo("/agents/kb/documents");
            assertThat(recorded.getQuery()).contains("category=manual").contains("pageNum=2").contains("pageSize=10")
                    .contains("keyword=").doesNotContain(" ");
            assertThat(recorded.header(AgentHeaders.SECRET)).isEqualTo(SECRET);
            assertThat(recorded.header(AgentHeaders.REQUEST_ID)).isEqualTo(REQUEST_ID);
            assertThat(data).isInstanceOf(com.alibaba.fastjson2.JSONObject.class);
            assertThat(((com.alibaba.fastjson2.JSONObject) data).getIntValue("total")).isEqualTo(1);
        }
    }

    @Test
    @DisplayName("入库：POST /documents，请求体含 camelCase 字段，operator 由调用方决定")
    void ingestDocumentPostsCamelCaseBody() throws Exception
    {
        try (FakeKbServer server = server(200, "{\"code\":200,\"msg\":\"ok\",\"data\":{\"ingest\":{\"docCode\":\"manual_a\"}}}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("docCode", "manual_a");
            payload.put("title", "补货 SOP");
            payload.put("category", "sop");
            payload.put("content", "正文…");
            payload.put("operator", "tester");

            client.ingestDocument(payload, REQUEST_ID);

            FakeKbServer.Recorded recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("POST");
            assertThat(recorded.getPath()).isEqualTo("/agents/kb/documents");
            assertThat(recorded.getBody()).contains("\"docCode\":\"manual_a\"").contains("\"operator\":\"tester\"");
            assertThat(recorded.header(AgentHeaders.SECRET)).isEqualTo(SECRET);
        }
    }

    @Test
    @DisplayName("启停：PUT /documents/{docCode}/enabled，body 为 {enabled:0/1}")
    void setEnabledUsesPathAndBody() throws Exception
    {
        try (FakeKbServer server = server(200, "{\"code\":200,\"msg\":\"ok\",\"data\":{\"docCode\":\"manual_a\",\"enabled\":0}}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));

            client.setEnabled("manual_a", false, REQUEST_ID);

            FakeKbServer.Recorded recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("PUT");
            assertThat(recorded.getPath()).isEqualTo("/agents/kb/documents/manual_a/enabled");
            assertThat(recorded.getBody()).contains("\"enabled\":0");
        }
    }

    @Test
    @DisplayName("检索：POST /search，请求体含 query/topK/requestId，头也带 requestId")
    void searchPostsPayload() throws Exception
    {
        try (FakeKbServer server = server(200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":{\"hits\":[],\"fallbackReason\":\"no_hit\"}}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("query", "A1000001 库存");
            payload.put("topK", Integer.valueOf(5));
            payload.put("requestId", REQUEST_ID);

            Object data = client.search(payload, REQUEST_ID);

            FakeKbServer.Recorded recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("POST");
            assertThat(recorded.getPath()).isEqualTo("/agents/kb/search");
            assertThat(recorded.getBody()).contains("\"query\":\"A1000001 库存\"").contains("\"topK\":5")
                    .contains("\"requestId\":\"" + REQUEST_ID + "\"");
            assertThat(((com.alibaba.fastjson2.JSONObject) data).getString("fallbackReason")).isEqualTo("no_hit");
        }
    }

    @Test
    @DisplayName("统计：GET /stats 解包 data")
    void statsReturnsData() throws Exception
    {
        try (FakeKbServer server = server(200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":{\"documents\":1,\"chunks\":3,\"retrievals\":4}}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));

            Object data = client.stats(REQUEST_ID);

            FakeKbServer.Recorded recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("GET");
            assertThat(recorded.getPath()).isEqualTo("/agents/kb/stats");
            assertThat(((com.alibaba.fastjson2.JSONObject) data).getIntValue("chunks")).isEqualTo(3);
        }
    }

    @Test
    @DisplayName("上游 500：归因为 502 语义，消息含状态码且不泄露响应体")
    void mapsUpstream500WithoutLeakingBody() throws Exception
    {
        try (FakeKbServer server = server(500,
                "{\"code\":500,\"msg\":\"SQL error: SELECT * FROM agent_kb_document WHERE secret...\"}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.stats(REQUEST_ID))
                    .isInstanceOf(AgentKbUpstreamException.class)
                    .hasMessageContaining("HTTP 500")
                    .hasMessageNotContaining("SQL")
                    .hasMessageNotContaining("agent_kb_document");
            AgentKbUpstreamException e = (AgentKbUpstreamException) org.assertj.core.api.Assertions
                    .catchThrowable(() -> client.stats(REQUEST_ID));
            assertThat(e.isTransportFailure()).isFalse();
            assertThat(e.getHttpStatus()).isEqualTo(500);
        }
    }

    @Test
    @DisplayName("401（带体请求的 HttpRetryException 陷阱）：按状态码提示鉴权，不报成不可达")
    void maps401AsAuthenticationFailure() throws Exception
    {
        try (FakeKbServer server = server(401, "{\"detail\":\"服务间密钥校验失败\"}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("docCode", "manual_a");
            payload.put("title", "t");
            payload.put("category", "sop");
            payload.put("content", "c");

            assertThatThrownBy(() -> client.ingestDocument(payload, REQUEST_ID))
                    .isInstanceOf(AgentKbUpstreamException.class)
                    .hasMessageContaining("鉴权失败")
                    .hasMessageContaining("401")
                    .hasMessageNotContaining("不可用（连接失败");
            AgentKbUpstreamException e = (AgentKbUpstreamException) org.assertj.core.api.Assertions
                    .catchThrowable(() -> client.ingestDocument(payload, REQUEST_ID));
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
            port = FakeKbServer.closedPort();
        }
        catch (IOException e)
        {
            throw new IllegalStateException(e);
        }
        AgentKbClient client = new AgentKbClient(properties("http://127.0.0.1:" + port));

        AgentKbUpstreamException e = (AgentKbUpstreamException) org.assertj.core.api.Assertions
                .catchThrowable(() -> client.stats(REQUEST_ID));

        assertThat(e).isNotNull();
        assertThat(e.isTransportFailure()).isTrue();
        assertThat(e.getHttpStatus()).isNull();
    }

    @Test
    @DisplayName("HTTP 200 但信封 code!=200：按失败处理，不把错误信封当成功数据")
    void rejectsFailedEnvelope() throws Exception
    {
        try (FakeKbServer server = server(200, "{\"code\":400,\"msg\":\"参数校验失败\",\"data\":null}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.stats(REQUEST_ID)).isInstanceOf(AgentKbUpstreamException.class)
                    .hasMessageContaining("code=400");
        }
    }

    @Test
    @DisplayName("空响应体：明确失败（不返回 null 假装成功）")
    void rejectsEmptyBody() throws Exception
    {
        try (FakeKbServer server = server(200, ""))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));

            assertThatThrownBy(() -> client.stats(REQUEST_ID)).isInstanceOf(AgentKbUpstreamException.class)
                    .hasMessageContaining("空响应");
        }
    }

    @Test
    @DisplayName("裸 JSON（无信封）：宽容按 data 返回")
    void acceptsBareJson() throws Exception
    {
        try (FakeKbServer server = server(200, "{\"documents\":2,\"chunks\":5}"))
        {
            AgentKbClient client = new AgentKbClient(properties(server.baseUrl()));

            Object data = client.stats(REQUEST_ID);

            assertThat(((com.alibaba.fastjson2.JSONObject) data).getIntValue("documents")).isEqualTo(2);
        }
    }
}
