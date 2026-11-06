package com.dkd.manage.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.manage.domain.AgentLlmProfile;

/**
 * 模型连通性探测客户端单测（排期 3-5）。
 *
 * <p>用本地假上游（{@link FakeLlmProbeServer}，回环随机端口、不触网）验证四类语义：
 * <ol>
 *   <li><b>成功/失败两种结论都能被解析</b>：Python 返回 {@code ok:true/false} 都要原样带回；</li>
 *   <li><b>非 2xx 与"非 JSON 响应"不能抛异常</b>：探测结果必须能落库，异常会让失败记录丢失；</li>
 *   <li><b>传输失败要与业务失败区分</b>：{@code transportFailure=true} 是 Service 决定
 *       "先落库再抛业务错误"的唯一依据；</li>
 *   <li><b>请求里只有环境变量名</b>：body 带 {@code apiKeyEnv} 变量名，绝不带密钥值
 *       （AGENTS §7.1）——用断言把这条红线钉在测试里。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentLlmProbeClientTest
{
    private static final String SECRET = "test-service-secret";

    private static final String REQUEST_ID = "rid-probe-1";

    private static AgentLlmProfile profile()
    {
        AgentLlmProfile profile = new AgentLlmProfile();
        profile.setCode("deepseek");
        profile.setBaseUrl("https://api.deepseek.com");
        profile.setModel("deepseek-chat");
        profile.setApiKeyEnv("DKD_DEEPSEEK_API_KEY");
        profile.setTimeoutS(Integer.valueOf(60));
        profile.setMaxRetries(Integer.valueOf(2));
        return profile;
    }

    private static AgentProperties properties(String baseUrl)
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl(baseUrl);
        properties.setSecret(SECRET);
        properties.setLlmProbeTimeoutMs(3000);
        return properties;
    }

    @Test
    @DisplayName("成功：解析 ok/latencyMs/modelServed，httpStatus 取响应状态")
    void parsesSuccessfulProbe() throws Exception
    {
        try (FakeLlmProbeServer server = new FakeLlmProbeServer(exchange -> FakeLlmProbeServer.respond(exchange, 200,
                "{\"ok\":true,\"latencyMs\":123,\"modelServed\":\"deepseek-flash\",\"errorType\":null,"
                        + "\"errorMsg\":null,\"httpStatus\":200}")))
        {
            AgentLlmProbeClient client = new AgentLlmProbeClient(properties(server.baseUrl()));

            AgentLlmProbeClient.ProbeOutcome outcome = client.ping(profile(), REQUEST_ID);

            assertThat(outcome.isTransportFailure()).isFalse();
            assertThat(outcome.isOk()).isTrue();
            assertThat(outcome.getLatencyMs()).isEqualTo(123);
            assertThat(outcome.getModelServed()).isEqualTo("deepseek-flash");
            assertThat(outcome.getHttpStatus()).isEqualTo(200);
            assertThat(outcome.getErrorType()).isNull();
        }
    }

    @Test
    @DisplayName("Python 返回 ok=false（缺环境变量）：仍按正常结论解析，不算传输失败")
    void parsesFailedProbeConclusion() throws Exception
    {
        try (FakeLlmProbeServer server = new FakeLlmProbeServer(exchange -> FakeLlmProbeServer.respond(exchange, 200,
                "{\"ok\":false,\"latencyMs\":12,\"errorType\":\"MissingApiKey\","
                        + "\"errorMsg\":\"环境变量 DKD_DEEPSEEK_API_KEY 未配置\",\"httpStatus\":null}")))
        {
            AgentLlmProbeClient client = new AgentLlmProbeClient(properties(server.baseUrl()));

            AgentLlmProbeClient.ProbeOutcome outcome = client.ping(profile(), REQUEST_ID);

            assertThat(outcome.isTransportFailure()).isFalse();
            assertThat(outcome.isOk()).isFalse();
            assertThat(outcome.getErrorType()).isEqualTo("MissingApiKey");
            assertThat(outcome.getErrorMsg()).contains("DKD_DEEPSEEK_API_KEY");
        }
    }

    @Test
    @DisplayName("兼容 RuoYi 信封 {data:{ok...}} 形状")
    void parsesEnvelopeShape() throws Exception
    {
        try (FakeLlmProbeServer server = new FakeLlmProbeServer(exchange -> FakeLlmProbeServer.respond(exchange, 200,
                "{\"code\":200,\"msg\":\"ok\",\"data\":{\"ok\":true,\"latencyMs\":8,\"modelServed\":\"deepseek-chat\"}}")))
        {
            AgentLlmProbeClient client = new AgentLlmProbeClient(properties(server.baseUrl()));

            AgentLlmProbeClient.ProbeOutcome outcome = client.ping(profile(), REQUEST_ID);

            assertThat(outcome.isOk()).isTrue();
            assertThat(outcome.getLatencyMs()).isEqualTo(8);
            assertThat(outcome.getModelServed()).isEqualTo("deepseek-chat");
        }
    }

    @Test
    @DisplayName("非 2xx（服务间密钥错误）：解析 detail 为 errorMsg，不抛异常")
    void parsesNon2xxResponse() throws Exception
    {
        try (FakeLlmProbeServer server = new FakeLlmProbeServer(exchange -> FakeLlmProbeServer.respond(exchange, 401,
                "{\"detail\":\"服务间密钥校验失败（/agent/** 仅允许回环或持密钥的服务间调用）\"}")))
        {
            AgentLlmProbeClient client = new AgentLlmProbeClient(properties(server.baseUrl()));

            AgentLlmProbeClient.ProbeOutcome outcome = client.ping(profile(), REQUEST_ID);

            assertThat(outcome.isTransportFailure()).isFalse();
            assertThat(outcome.isOk()).isFalse();
            assertThat(outcome.getHttpStatus()).isEqualTo(401);
            assertThat(outcome.getErrorType()).isEqualTo("HttpStatus401");
            // JDK 对「带请求体的流式请求」收到 401 会抛 HttpRetryException 并丢弃响应体，
            // 所以这里只能断言"按状态码归因"，不能指望拿到 detail 文案（客户端已处理该陷阱）
            assertThat(outcome.getErrorMsg()).contains("HTTP 401");
        }
    }

    @Test
    @DisplayName("非 JSON 响应（网关错误页）：归为失败结论，不回显正文")
    void parsesNonJsonResponse() throws Exception
    {
        try (FakeLlmProbeServer server = new FakeLlmProbeServer(
                exchange -> FakeLlmProbeServer.respond(exchange, 502, "<html>Bad Gateway</html>")))
        {
            AgentLlmProbeClient client = new AgentLlmProbeClient(properties(server.baseUrl()));

            AgentLlmProbeClient.ProbeOutcome outcome = client.ping(profile(), REQUEST_ID);

            assertThat(outcome.isOk()).isFalse();
            assertThat(outcome.getErrorType()).isEqualTo("NonJsonResponse");
            assertThat(outcome.getErrorMsg()).doesNotContain("Bad Gateway");
        }
    }

    @Test
    @DisplayName("Python 不可达：transportFailure=true 且 httpStatus 为空")
    void marksTransportFailureWhenUnreachable() throws Exception
    {
        int port = FakeLlmProbeServer.closedPort();
        AgentLlmProbeClient client = new AgentLlmProbeClient(properties("http://127.0.0.1:" + port));

        AgentLlmProbeClient.ProbeOutcome outcome = client.ping(profile(), REQUEST_ID);

        assertThat(outcome.isTransportFailure()).isTrue();
        assertThat(outcome.isOk()).isFalse();
        assertThat(outcome.getHttpStatus()).isNull();
        assertThat(outcome.getErrorType()).isNotBlank();
    }

    @Test
    @DisplayName("请求只带环境变量名与服务间密钥头，绝不携带密钥值")
    void requestCarriesEnvNameButNotSecretValue() throws Exception
    {
        try (FakeLlmProbeServer server = new FakeLlmProbeServer(exchange -> FakeLlmProbeServer.respond(exchange, 200,
                "{\"ok\":true}")))
        {
            AgentLlmProbeClient client = new AgentLlmProbeClient(properties(server.baseUrl()));

            client.ping(profile(), REQUEST_ID);

            List<FakeLlmProbeServer.Recorded> requests = server.getRequests();
            assertThat(requests).hasSize(1);
            FakeLlmProbeServer.Recorded recorded = requests.get(0);
            assertThat(recorded.getPath()).isEqualTo("/agents/llm/ping");
            assertThat(recorded.header(AgentHeaders.SECRET)).isEqualTo(SECRET);
            assertThat(recorded.header(AgentHeaders.REQUEST_ID)).isEqualTo(REQUEST_ID);
            String body = recorded.getBody();
            assertThat(body).contains("\"apiKeyEnv\"");
            assertThat(body).contains("DKD_DEEPSEEK_API_KEY");
            // 红线断言：请求体里不可能出现密钥值（只有变量名），也不应出现 secret 头值
            assertThat(body).doesNotContain(SECRET);
        }
    }
}
