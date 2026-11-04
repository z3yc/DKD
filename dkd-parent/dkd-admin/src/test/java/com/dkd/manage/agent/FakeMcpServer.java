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

import org.springframework.util.StreamUtils;

/**
 * 测试用"假 Python MCP 管理服务"（排期 3-7 单测夹具）。
 *
 * <p>为什么用 JDK 自带 {@code com.sun.net.httpserver}：只需要"回环随机端口 + 按剧本返回 +
 * 记录上游收到什么"，零外部依赖、测试可离线稳定复跑（AGENTS §8 测试不得触网）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class FakeMcpServer implements AutoCloseable
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

    /** 记录的上游请求 */
    static class Recorded
    {
        private final String method;

        private final String path;

        private final String query;

        private final Map<String, String> headers = new LinkedHashMap<String, String>();

        private final String body;

        private Recorded(HttpExchange exchange, String body)
        {
            this.method = exchange.getRequestMethod();
            this.path = exchange.getRequestURI().getPath();
            this.query = exchange.getRequestURI().getQuery();
            this.body = body;
            for (Map.Entry<String, List<String>> entry : exchange.getRequestHeaders().entrySet())
            {
                if (!entry.getValue().isEmpty())
                {
                    headers.put(entry.getKey().toLowerCase(), entry.getValue().get(0));
                }
            }
        }

        String getMethod()
        {
            return method;
        }

        String getPath()
        {
            return path;
        }

        String getQuery()
        {
            return query;
        }

        String getBody()
        {
            return body;
        }

        String header(String name)
        {
            return headers.get(name.toLowerCase());
        }
    }

    private final HttpServer server;

    private final List<Recorded> requests = new CopyOnWriteArrayList<Recorded>();

    FakeMcpServer(Handler handler) throws IOException
    {
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        // 用前缀 context 覆盖 probe/{code}、sync/{code}、tools?serverCode= 三类路径
        this.server.createContext("/agents/mcp", exchange -> {
            String body = new String(StreamUtils.copyToByteArray(exchange.getRequestBody()),
                    StandardCharsets.UTF_8);
            requests.add(new Recorded(exchange, body));
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
     * 写固定响应体。
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
