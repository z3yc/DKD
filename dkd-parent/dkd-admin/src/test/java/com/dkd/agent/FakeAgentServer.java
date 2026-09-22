package com.dkd.agent;

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
import org.springframework.util.StreamUtils;

/**
 * 测试用“假 Python 智能体服务”（任务 0-6/0-7 单测夹具）。
 *
 * <p>为什么用 JDK 自带的 com.sun.net.httpserver 而不引 MockWebServer/OkHttp：
 * 只用得上“绑定回环随机端口 + 按我的节奏分帧发出 + 记录上游收到了什么”，
 * 引新依赖换不来额外价值；且该实现零外部依赖，测试可离线稳定复跑（AGENTS §8 测试不得触网）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class FakeAgentServer implements AutoCloseable
{
    /**
     * 上游处理逻辑（在 HttpServer 的线程池里执行）
     */
    interface Handler
    {
        /**
         * @param exchange 本次请求/响应
         * @throws IOException 读写失败
         */
        void handle(HttpExchange exchange) throws IOException;
    }

    /**
     * 上游实际收到的请求（用于断言白名单头/请求体是否原样到达）
     */
    static class RecordedRequest
    {
        private final String method;

        private final String pathWithQuery;

        private final Map<String, List<String>> headers;

        private final byte[] body;

        private RecordedRequest(String method, String pathWithQuery, Map<String, List<String>> headers, byte[] body)
        {
            this.method = method;
            this.pathWithQuery = pathWithQuery;
            this.headers = headers;
            this.body = body;
        }

        static RecordedRequest of(HttpExchange exchange) throws IOException
        {
            Map<String, List<String>> copied = new LinkedHashMap<String, List<String>>();
            for (Map.Entry<String, List<String>> entry : exchange.getRequestHeaders().entrySet())
            {
                copied.put(entry.getKey(), new ArrayList<String>(entry.getValue()));
            }
            byte[] body = StreamUtils.copyToByteArray(exchange.getRequestBody());
            return new RecordedRequest(exchange.getRequestMethod(), exchange.getRequestURI().toString(), copied, body);
        }

        String getMethod()
        {
            return method;
        }

        String getPathWithQuery()
        {
            return pathWithQuery;
        }

        /**
         * 大小写不敏感的取头（JDK Headers 会把 key 归一化成 "X-agent-user" 这类形式）
         *
         * @param name 头名
         * @return 首个值；不存在时返回 null
         */
        String header(String name)
        {
            for (Map.Entry<String, List<String>> entry : headers.entrySet())
            {
                if (entry.getKey().equalsIgnoreCase(name) && !entry.getValue().isEmpty())
                {
                    return entry.getValue().get(0);
                }
            }
            return null;
        }

        String bodyAsString()
        {
            return new String(body, StandardCharsets.UTF_8);
        }
    }

    private final HttpServer server;

    private final List<RecordedRequest> requests = new CopyOnWriteArrayList<RecordedRequest>();

    FakeAgentServer(String path, Handler handler) throws IOException
    {
        // 绑定回环随机端口：不触网、不撞端口（AGENTS §8）
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        this.server.createContext(path, exchange -> {
            requests.add(RecordedRequest.of(exchange));
            handler.handle(exchange);
        });
        this.server.setExecutor(Executors.newCachedThreadPool());
        this.server.start();
    }

    int port()
    {
        return server.getAddress().getPort();
    }

    String baseUrl()
    {
        return "http://127.0.0.1:" + port();
    }

    List<RecordedRequest> getRequests()
    {
        return requests;
    }

    /**
     * 直接写固定响应体的辅助方法（非流式接口用）
     *
     * @param exchange 交换对象
     * @param status 状态码
     * @param contentType 内容类型
     * @param body 响应体
     * @throws IOException 写出失败
     */
    static void respond(HttpExchange exchange, int status, String contentType, String body) throws IOException
    {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", contentType);
        exchange.sendResponseHeaders(status, payload.length);
        OutputStream out = exchange.getResponseBody();
        out.write(payload);
        out.close();
    }

    /**
     * 找一个当前必定没人监听的端口（用于“上游不可达”用例）
     *
     * @return 端口号
     * @throws IOException 探测失败
     */
    static int closedPort() throws IOException
    {
        try (java.net.ServerSocket socket = new java.net.ServerSocket(0))
        {
            int port = socket.getLocalPort();
            socket.close();
            return port;
        }
    }

    @Override
    public void close()
    {
        server.stop(0);
    }
}
