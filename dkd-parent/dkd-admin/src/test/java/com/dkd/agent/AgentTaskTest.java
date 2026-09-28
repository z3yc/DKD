package com.dkd.agent;

import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.utils.DateUtils;
import com.dkd.quartz.task.AgentTask;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 补货定时任务单测（排期任务 1-9）。
 *
 * <p>四条验收口径各自成例（对应 {@link AgentTask} 类注释里的四条约定）：
 * 手动触发成功、暂停与开关不是失败、4xx 不重试而 5xx 重试、最终失败必须抛异常
 * （RuoYi 会据此写入 sys_job_log 的失败记录，这是“失败可告警”的落地方式）。
 *
 * <p>全部用回环假服务：不触网、不依赖 Python 进程、不依赖 MySQL（AGENTS §8）。
 *
 * @author ruoyi
 * @date 2026-09-23
 */
class AgentTaskTest
{
    private static final String PATH = "/agents/restock/analyze";

    private static final String SECRET = "unit-test-secret";

    @Test
    void 触发成功并带上服务间密钥与全链路requestId() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer(PATH,
                exchange -> FakeAgentServer.respond(exchange, 200, "application/json", envelope(
                        "{\"status\":\"analyzed\",\"deviceCount\":3,\"channelCount\":5,\"totalQuantity\":21,\"interrupted\":true}"))))
        {
            newTask(properties(server.baseUrl(), SECRET, true, 0)).restockAnalyze();

            assertThat(server.getRequests()).hasSize(1);
            FakeAgentServer.RecordedRequest request = server.getRequests().get(0);
            assertThat(request.getMethod()).isEqualTo("POST");
            assertThat(request.header("X-Agent-Secret")).as("服务间密钥必须真的发出（否则线上 401 无从查起）")
                    .isEqualTo(SECRET);
            assertThat(request.header("X-Request-Id")).as("全链路 requestId 让 Python 日志可被检索")
                    .startsWith("restock-job-");
            // planDate 由 Java 侧算出传入：两端各自 today() 在跨时区/跨机部署时不一致
            assertThat(request.bodyAsString()).contains("\"planDate\":\"" + DateUtils.getDate() + "\"");
        }
    }

    @Test
    void 被暂停跳过时不抛异常也不重试() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer(PATH,
                exchange -> FakeAgentServer.respond(exchange, 200, "application/json",
                        envelope("{\"status\":\"skipped\",\"reason\":\"盘点一周\",\"pausedBy\":\"7\"}"))))
        {
            AgentProperties properties = properties(server.baseUrl(), SECRET, true, 2);

            // 不抛异常：暂停是运营主动决定，不是故障；每天一条失败告警会让团队对告警脱敏
            newTask(properties).restockAnalyze();

            assertThat(server.getRequests()).as("跳过是一次成功的调用，不该重试").hasSize(1);
        }
    }

    @Test
    void 上游4xx不重试直接失败() throws Exception
    {
        AtomicInteger calls = new AtomicInteger();
        try (FakeAgentServer server = new FakeAgentServer(PATH, exchange -> {
            calls.incrementAndGet();
            FakeAgentServer.respond(exchange, 401, "application/json",
                    "{\"code\":401,\"msg\":\"服务间密钥校验失败\",\"data\":null}");
        }))
        {
            // 回归关注点：JDK 在带请求体的请求上收到 401 会抛 HttpRetryException（丢掉响应体），
            // 客户端已特判成“按状态码透传”（见 AgentUpstreamClientTest）。若没有那个特判，
            // 这里会退化成“不可达”——告警把运维引向完全错误的方向。
            assertThatThrownBy(() -> newTask(properties(server.baseUrl(), SECRET, true, 2)).restockAnalyze())
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("401")
                    .hasMessageContaining("已尝试 1/3 次");
            assertThat(calls.get()).as("4xx 是配置/鉴权问题，重试不会变好，只会刷满日志").isEqualTo(1);
        }
    }

    @Test
    void 上游业务拒绝的中文原因进告警消息() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer(PATH,
                exchange -> FakeAgentServer.respond(exchange, 422, "application/json",
                        "{\"code\":422,\"msg\":\"参数校验失败\",\"data\":null}")))
        {
            assertThatThrownBy(() -> newTask(properties(server.baseUrl(), SECRET, true, 0)).restockAnalyze())
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("422")
                    .hasMessageContaining("参数校验失败");
        }
    }

    @Test
    void 上游5xx重试后成功() throws Exception
    {
        AtomicInteger calls = new AtomicInteger();
        try (FakeAgentServer server = new FakeAgentServer(PATH, exchange -> {
            if (calls.incrementAndGet() == 1)
            {
                FakeAgentServer.respond(exchange, 500, "application/json",
                        "{\"code\":500,\"msg\":\"上游暂时不可用\",\"data\":null}");
                return;
            }
            FakeAgentServer.respond(exchange, 200, "application/json",
                    envelope("{\"status\":\"analyzed\",\"deviceCount\":1}"));
        }))
        {
            newTask(properties(server.baseUrl(), SECRET, true, 1)).restockAnalyze();

            assertThat(calls.get()).as("第一次 5xx、第二次成功").isEqualTo(2);
        }
    }

    @Test
    void 全部尝试失败后抛异常由任务日志留痕() throws Exception
    {
        AtomicInteger calls = new AtomicInteger();
        try (FakeAgentServer server = new FakeAgentServer(PATH, exchange -> {
            calls.incrementAndGet();
            FakeAgentServer.respond(exchange, 502, "application/json",
                    "{\"code\":502,\"msg\":\"补货分析运行失败：TimeoutError: 取数超时\",\"data\":null}");
        }))
        {
            assertThatThrownBy(() -> newTask(properties(server.baseUrl(), SECRET, true, 2)).restockAnalyze())
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("已尝试 3/3 次")
                    .hasMessageContaining("TimeoutError");
            assertThat(calls.get()).isEqualTo(3);
        }
    }

    @Test
    void 服务不可达时重试并最终失败() throws Exception
    {
        int closedPort = FakeAgentServer.closedPort();
        AgentProperties properties = properties("http://127.0.0.1:" + closedPort, SECRET, true, 0);

        assertThatThrownBy(() -> newTask(properties).restockAnalyze())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("不可达");
    }

    @Test
    void 开关关闭时不调用上游且不算失败() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer(PATH,
                exchange -> FakeAgentServer.respond(exchange, 200, "application/json", envelope("{}"))))
        {
            newTask(properties(server.baseUrl(), SECRET, false, 2)).restockAnalyze();

            assertThat(server.getRequests()).as("agent.enabled=false 时不该产生任何外部调用").isEmpty();
        }
    }

    @Test
    void 密钥未配置时快速失败且不发出请求() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer(PATH,
                exchange -> FakeAgentServer.respond(exchange, 200, "application/json", envelope("{}"))))
        {
            assertThatThrownBy(() -> newTask(properties(server.baseUrl(), "", true, 2)).restockAnalyze())
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("agent.secret 未配置");
            assertThat(server.getRequests()).isEmpty();
        }
    }

    @Test
    void 响应体不可解析时不影响成功判定() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer(PATH,
                exchange -> FakeAgentServer.respond(exchange, 200, "text/html", "<html>502 Bad Gateway</html>")))
        {
            // 上游 200 但响应体不是 JSON（反向代理插页）：任务本身不能因此判失败，
            // 否则会把“代理配置问题”误报成“补货分析失败”
            newTask(properties(server.baseUrl(), SECRET, true, 0)).restockAnalyze();

            assertThat(server.getRequests()).hasSize(1);
        }
    }

    private static String envelope(String data)
    {
        return "{\"code\":200,\"msg\":\"ok\",\"data\":" + data + "}";
    }

    private static AgentProperties properties(String baseUrl, String secret, boolean enabled, int retryMax)
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl(baseUrl);
        properties.setSecret(secret);
        properties.setEnabled(enabled);
        properties.setAnalyzeRetryMax(retryMax);
        // 重试间隔置 0：单测不睡觉（退避逻辑只是 Thread.sleep 的线性放大，不值得为它等 30 秒）
        properties.setAnalyzeRetryBackoffMs(0L);
        return properties;
    }

    private static AgentTask newTask(AgentProperties properties)
    {
        return new AgentTask(properties, new AgentUpstreamClient(properties));
    }
}
