package com.dkd.agent;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.agent.support.AgentUpstreamException;
import com.sun.net.httpserver.HttpExchange;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 上游客户端单测（任务 0-6）。
 *
 * <p>核心证据：SSE 中继是**按块写出并 flush**的（首块必须在上游发完最后一帧之前就落到客户端输出流），
 * 这条断言专门防止“先把上游读完再一次性返回”的回归——那正是方案 §3.3 明令禁止的实现方式。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class AgentUpstreamClientTest
{
    private static final List<String> FRAMES = Arrays.asList("event: meta\ndata: {\"i\":1}\n\n",
            "event: delta\ndata: {\"i\":2}\n\n", "event: done\ndata: {\"i\":3}\n\n");

    @Test
    void 中继SSE按块写出且不整体缓冲() throws Exception
    {
        AtomicBoolean lastFrameSent = new AtomicBoolean(false);
        try (FakeAgentServer server = new FakeAgentServer("/agent/chat", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, 0);
            OutputStream body = exchange.getResponseBody();
            for (int i = 0; i < FRAMES.size(); i++)
            {
                body.write(FRAMES.get(i).getBytes(StandardCharsets.UTF_8));
                body.flush();
                if (i == FRAMES.size() - 1)
                {
                    lastFrameSent.set(true);
                }
                // 帧间隔 80ms：给客户端充分的窗口在“上游发完”之前完成首次写出
                sleepQuietly(80);
            }
            body.close();
        }))
        {
            RecordingOutputStream out = new RecordingOutputStream(lastFrameSent);

            newClient(server.baseUrl()).relayStream("POST", "/agent/chat", null, headers("7"), "{\"message\":\"hi\"}"
                    .getBytes(StandardCharsets.UTF_8), out);

            assertThat(out.content()).as("必须逐帧完整透传").isEqualTo(String.join("", FRAMES));
            assertThat(out.flushCount()).as("每块都必须 flush").isEqualTo(out.writeCount());
            assertThat(out.flushCount()).as("至少写出一次").isGreaterThan(0);
            assertThat(out.firstFlushBeforeLastFrame())
                    .as("首块写出必须早于上游发出最后一帧（否则就是整体缓冲）").isTrue();
        }
    }

    @Test
    void 中继时白名单头与请求体原样到达上游() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/chat", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().close();
        }))
        {
            Map<String, String> headers = new LinkedHashMap<String, String>();
            headers.put(AgentHeaders.USER, "7");
            headers.put(AgentHeaders.USERNAME, "ops");
            headers.put(AgentHeaders.ROLES, "admin");
            headers.put(AgentHeaders.REQUEST_ID, "rid-1");
            headers.put(AgentHeaders.LAST_EVENT_ID, "3");
            headers.put("Content-Type", "application/json");
            headers.put(AgentHeaders.SECRET, "svc-secret");

            newClient(server.baseUrl()).relayStream("POST", "/agent/chat", null, headers, "{\"message\":\"ping\"}"
                    .getBytes(StandardCharsets.UTF_8), new ByteArrayOutputStream());

            FakeAgentServer.RecordedRequest recorded = server.getRequests().get(0);
            assertThat(recorded.getMethod()).isEqualTo("POST");
            assertThat(recorded.getPathWithQuery()).isEqualTo("/agent/chat");
            assertThat(recorded.header(AgentHeaders.USER)).isEqualTo("7");
            assertThat(recorded.header(AgentHeaders.USERNAME)).isEqualTo("ops");
            assertThat(recorded.header(AgentHeaders.ROLES)).isEqualTo("admin");
            assertThat(recorded.header(AgentHeaders.REQUEST_ID)).isEqualTo("rid-1");
            assertThat(recorded.header(AgentHeaders.LAST_EVENT_ID)).as("断点续传游标必须透传").isEqualTo("3");
            assertThat(recorded.bodyAsString()).as("请求体必须原样字节转发").isEqualTo("{\"message\":\"ping\"}");
        }
    }

    @Test
    void 中继遇到上游非200抛异常且不写成SSE帧() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/chat",
                exchange -> FakeAgentServer.respond(exchange, 500, "application/json", "{\"detail\":\"boom\"}")))
        {
            RecordingOutputStream out = new RecordingOutputStream(new AtomicBoolean(false));

            assertThatThrownBy(() -> newClient(server.baseUrl()).relayStream("POST", "/agent/chat", null,
                    headers("7"), new byte[0], out))
                            .isInstanceOfSatisfying(AgentUpstreamException.class,
                                    e -> assertThat(e.getStatus()).isEqualTo(500));

            assertThat(out.content()).as("上游错误信封不得当作 SSE 帧透传").isEmpty();
        }
    }

    @Test
    void 上游不可达时抛传输层异常() throws Exception
    {
        int closedPort = FakeAgentServer.closedPort();

        assertThatThrownBy(() -> newClient("http://127.0.0.1:" + closedPort).relayStream("POST", "/agent/chat", null,
                headers("7"), new byte[0], new ByteArrayOutputStream()))
                        .isInstanceOfSatisfying(AgentUpstreamException.class,
                                e -> assertThat(e.getStatus())
                                        .isEqualTo(AgentUpstreamException.STATUS_TRANSPORT_FAILURE));
    }

    @Test
    void 转发返回上游状态码与信封体() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/restock/plans",
                exchange -> FakeAgentServer.respond(exchange, 422, "application/json", "{\"code\":422,\"msg\":\"参数校验失败\"}")))
        {
            AgentUpstreamResult result = newClient(server.baseUrl()).forward("POST", "/agent/restock/plans", "days=7",
                    headers("7"), "{}".getBytes(StandardCharsets.UTF_8));

            assertThat(result.getStatus()).as("4xx 携带业务语义，必须原样带回由调用方决定透传").isEqualTo(422);
            assertThat(result.isServerError()).isFalse();
            assertThat(result.getContentType()).isEqualTo("application/json");
            assertThat(new String(result.getBody(), StandardCharsets.UTF_8)).contains("参数校验失败");
            assertThat(server.getRequests().get(0).getPathWithQuery()).as("查询串必须带上").isEqualTo(
                    "/agent/restock/plans?days=7");
        }
    }

    @Test
    void 转发标记上游自身故障() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/restock/plans",
                exchange -> FakeAgentServer.respond(exchange, 503, "application/json", "{\"code\":503}")))
        {
            AgentUpstreamResult result = newClient(server.baseUrl()).forward("POST", "/agent/restock/plans", null,
                    headers("7"), new byte[0]);

            assertThat(result.getStatus()).isEqualTo(503);
            assertThat(result.isServerError()).isTrue();
        }
    }

    @Test
    void 转发回带上游的requestId() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/agent/restock/plans", exchange -> {
            exchange.getResponseHeaders().set("X-Request-Id", "rid-upstream");
            FakeAgentServer.respond(exchange, 200, "application/json", "{\"code\":200}");
        }))
        {
            AgentUpstreamResult result = newClient(server.baseUrl()).forward("GET", "/agent/restock/plans", null,
                    headers("7"), new byte[0]);

            assertThat(result.getRequestId()).isEqualTo("rid-upstream");
            assertThat(result.isSuccessful()).isTrue();
        }
    }

    @Test
    void 探活_上游正常时true_端口关闭时false() throws Exception
    {
        try (FakeAgentServer server = new FakeAgentServer("/health",
                exchange -> FakeAgentServer.respond(exchange, 200, "application/json", "{\"status\":\"ok\"}")))
        {
            assertThat(newClient(server.baseUrl()).pingHealth()).isTrue();
        }
        assertThat(newClient("http://127.0.0.1:" + FakeAgentServer.closedPort()).pingHealth())
                .as("探活失败必须是 false 而不是抛异常（/agent/status 要能返回 down）").isFalse();
    }

    private AgentUpstreamClient newClient(String baseUrl)
    {
        AgentProperties properties = new AgentProperties();
        properties.setBaseUrl(baseUrl);
        properties.setConnectTimeout(500);
        properties.setReadTimeout(5000);
        return new AgentUpstreamClient(properties);
    }

    private Map<String, String> headers(String userId)
    {
        Map<String, String> headers = new LinkedHashMap<String, String>();
        headers.put(AgentHeaders.USER, userId);
        return headers;
    }

    private static void sleepQuietly(long millis)
    {
        try
        {
            Thread.sleep(millis);
        }
        catch (InterruptedException e)
        {
            Thread.currentThread().interrupt();
        }
    }

    /**
     * 记录写入/flush 次数与“首块 flush 时上游是否已发完”的输出流，用于证明分帧写出。
     */
    private static class RecordingOutputStream extends OutputStream
    {
        private final ByteArrayOutputStream received = new ByteArrayOutputStream();

        private final AtomicBoolean lastFrameSent;

        private int writeCount;

        private int flushCount;

        private Boolean firstFlushBeforeLastFrame;

        RecordingOutputStream(AtomicBoolean lastFrameSent)
        {
            this.lastFrameSent = lastFrameSent;
        }

        @Override
        public void write(int b)
        {
            received.write(b);
            writeCount++;
        }

        @Override
        public void write(byte[] b, int off, int len)
        {
            received.write(b, off, len);
            writeCount++;
        }

        @Override
        public void flush() throws IOException
        {
            flushCount++;
            if (firstFlushBeforeLastFrame == null)
            {
                firstFlushBeforeLastFrame = Boolean.valueOf(!lastFrameSent.get());
            }
        }

        String content()
        {
            return new String(received.toByteArray(), StandardCharsets.UTF_8);
        }

        int writeCount()
        {
            return writeCount;
        }

        int flushCount()
        {
            return flushCount;
        }

        boolean firstFlushBeforeLastFrame()
        {
            return Boolean.TRUE.equals(firstFlushBeforeLastFrame);
        }
    }
}
