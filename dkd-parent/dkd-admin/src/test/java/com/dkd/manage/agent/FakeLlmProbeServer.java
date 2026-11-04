package com.dkd.manage.agent;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

/**
 * 测试用"假 Python llm_probe 服务"（排期 3-5 单测夹具）。
 *
 * <p>为什么用 JDK 自带的 {@code com.sun.net.httpserver} 而不引 MockWebServer：
 * 只需要"绑定回环随机端口 + 按剧本返回固定响应 + 记录上游收到了什么"，
 * 零外部依赖、测试可离线稳定复跑（AGENTS §8 测试不得触网）。与
 * {@code com.dkd.agent.FakeAgentServer} 是同一套路，但那个类在别的测试包且不可见，
 * 这里为探测客户端单独实现一个最小版本。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class FakeLlmProbeServer implements AutoCloseable
{
    /** 上游处理逻辑（在 HttpServer 线程池里执行） */
    interface Handler
    {
        /**
         * @param exchange 本次请求/响应
         * @throws IOException 读写失败
         */
        void handle(HttpExchange exchange) throws IOException;
    }

    /** 记录上游实际收到的请求（用于断言请求体/头） */
    static class Recorded
    {
        private final String path;

        private final Map<String, String> headers = new LinkedHashMap<String, String>();

        private final String body;

        private Recorded(String path, Map<String, List<String>> rawHeaders, String body)
        {
            this.path = path;
            this.body = body;
            for (Map.Entry<String, List<String>> entry : rawHeaders.entrySet())
            {
                if (!entry.getValue().isEmpty())
                {
                    headers.put(entry.getKey().toLowerCase(), entry.getValue().get(0));
                }
            }
        }

        String getPath()
        {
            return path;
        }

        String getBody()
        {
            return body;
        }

        /**
         * 大小写不敏感的取头（JDK 会把头名归一化成 "X-agent-secret" 这类形式）。
         *
         * @param name 头名
         * @return 首个值；不存在返回 null
         */
        String header(String name)
        {
            return headers.get(name.toLowerCase());
        }
    }

    private final HttpServer server;

    private final List<Recorded> requests = new CopyOnWriteArrayList<Recorded>();

    FakeLlmProbeServer(Handler handler) throws IOException
    {
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        // 探测客户端固定请求 /agents/llm/ping，与 Python 侧路由一致
        this.server.createContext("/agents/llm/ping", exchange -> {
            String body = new String(org.springframework.util.StreamUtils.copyToByteArray(exchange.getRequestBody()),
                    StandardCharsets.UTF_8);
            requests.add(new Recorded(exchange.getRequestURI().getPath(), exchange.getRequestHeaders(), body));
            handler.handle(exchange);
        });
        this.server.setExecutor(Executors.newCachedThreadPool());
        this.server.start();
    }

    String baseUrl()
    {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    List<Recorded> getRequests()
    {
        return new ArrayList<Recorded>(requests);
    }

    /**
     * 写固定响应体的辅助方法。
     *
     * @param exchange 交换对象
     * @param status   状态码
     * @param body     响应体
     * @throws IOException 写出失败
     */
    static void respond(HttpExchange exchange, int status, String body) throws IOException
    {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json;charset=UTF-8");
        exchange.sendResponseHeaders(status, payload.length);
        OutputStream out = exchange.getResponseBody();
        out.write(payload);
        out.close();
    }

    /**
     * 找一个当前必定没人监听的端口（用于"上游不可达"用例）。
     *
     * @return 端口号
     * @throws IOException 探测失败
     */
    static int closedPort() throws IOException
    {
        try (java.net.ServerSocket socket = new java.net.ServerSocket(0))
        {
            return socket.getLocalPort();
        }
    }

    @Override
    public void close()
    {
        server.stop(0);
    }
}
