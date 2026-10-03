package com.dkd.common.ai;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import com.dkd.common.ai.config.AiConfig;
import com.dkd.common.ai.service.impl.AiServiceImpl;
import com.sun.net.httpserver.HttpServer;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * 兜底单轮 AI 链路的**错误路径不得把上游原始响应体回传给终端用户**（F-3 回归测试）。
 *
 * <p>背景（2026-10-02 真环境浏览器验收实测）：`设备管理 → AI诊断` 弹窗直接把上游 401 响应体
 * 渲染到了运营眼前，内容是
 * {@code {"error":{"message":"Authentication Fails, Your api key: ****48a8 is invalid ..."}}}——
 * 既让运营看不懂（英文技术栈报错、没有下一步动作），又把凭据指纹与上游 request_id 暴露到界面。
 * 旧实现在 {@code HttpClient} 里把 {@code result} 原样拼进返回值；本测试锁死这个行为不再回来。
 *
 * <p>为什么必须起真 HTTP 服务器而不是打桩 Hutool 静态方法：被改坏的地方正是
 * “HTTP 状态码 → 用户文案”的映射，只有让请求真的走一遍 Hutool 才能覆盖到（AGENTS §8：
 * 修复必须带防回归测试）。用 JDK 自带 {@code com.sun.net.httpserver}，绑回环随机端口，
 * 不触网、零新增依赖（同 {@code com.dkd.agent.FakeAgentServer} 的取舍）。
 *
 * @author ruoyi
 * @date 2026-10-02
 */
class AiServiceImplErrorLeakTest
{
    /** 断言里出现的“敏感片段”：真实上游响应体里就长这样，任何一条漏到返回值即失败 */
    private static final String FAKE_KEY_TAIL = "****48a8";

    private static final String UPSTREAM_401_BODY = "{\"error\":{\"message\":\"Authentication Fails, "
            + "Your api key: " + FAKE_KEY_TAIL + " is invalid (request_id: ac8dd579-6a07)\","
            + "\"type\":\"authentication_error\",\"code\":\"invalid_request_error\"}}";

    private HttpServer server;

    private AiServiceImpl service;

    private String upstreamBody;

    private int upstreamStatus;

    @AfterEach
    void tearDown()
    {
        if (server != null)
        {
            server.stop(0);
        }
    }

    /**
     * 上游 401：返回值必须是**可行动的中文文案**，且不含任何上游原文片段。
     */
    @Test
    void 上游401时不回传原始响应体且给出可行动文案() throws Exception
    {
        givenUpstream(401, UPSTREAM_401_BODY);

        String reply = service.ask("诊断一下");

        assertThat(reply).doesNotContain(FAKE_KEY_TAIL);
        assertThat(reply).doesNotContain("Authentication Fails");
        assertThat(reply).doesNotContain("authentication_error");
        assertThat(reply).doesNotContain("request_id");
        assertThat(reply).doesNotContain("\"error\"");
        // 必须指向下一步动作：运营看到这句才知道该找谁
        assertThat(reply).contains("鉴权失败").contains("401").contains("API Key");
    }

    /**
     * 上游 429 / 5xx：文案要区分“等一会儿”与“报障”，不能都糊成一句“请求失败”。
     */
    @Test
    void 上游限流与故障给不同文案() throws Exception
    {
        givenUpstream(429, "{\"error\":{\"message\":\"rate limit\"}}");
        assertThat(service.ask("hi")).contains("429").contains("稍后再试");

        givenUpstream(503, "<html>gateway down</html>");
        String reply = service.ask("hi");
        assertThat(reply).contains("503").contains("稍后再试");
        // 非 JSON 的上游响应（网关 HTML）同样不得原样回传
        assertThat(reply).doesNotContain("gateway down");
    }

    /**
     * HTTP 200 但信封里带 error：旧实现把 {@code error.message} 原样拼给用户，同样要堵住。
     */
    @Test
    void 信封内错误对象也不回传上游message() throws Exception
    {
        givenUpstream(200, "{\"error\":{\"message\":\"Model not exist, key " + FAKE_KEY_TAIL + "\"}}");

        String reply = service.ask("诊断一下");

        assertThat(reply).doesNotContain(FAKE_KEY_TAIL);
        assertThat(reply).doesNotContain("Model not exist");
        assertThat(reply).contains("AI 服务返回错误");
    }

    /**
     * 正常路径不能被这次改动带坏：choices[0].message.content 必须原样返回。
     */
    @Test
    void 正常响应仍原样返回模型内容() throws Exception
    {
        givenUpstream(200, "{\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"设备当前运行正常\"}}]}");

        assertThat(service.ask("诊断一下")).isEqualTo("设备当前运行正常");
    }

    /**
     * 上游 200 但没有 choices：给可读文案，不拼接原始体。
     */
    @Test
    void 缺少choices时给可读文案() throws Exception
    {
        givenUpstream(200, "{\"id\":\"chatcmpl-1\",\"object\":\"chat.completion\"}");

        String reply = service.ask("诊断一下");

        assertThat(reply).contains("AI 服务返回内容为空");
        assertThat(reply).doesNotContain("chatcmpl-1");
    }

    /**
     * 起一个只回固定状态码/响应体的上游，并把 AiServiceImpl 指过去。
     *
     * @param status HTTP 状态码
     * @param body 响应体原文
     * @throws IOException 绑定端口失败
     */
    private void givenUpstream(int status, String body) throws IOException
    {
        upstreamStatus = status;
        upstreamBody = body;
        if (server == null)
        {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/chat/completions", exchange -> {
                byte[] payload = upstreamBody.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(upstreamStatus, payload.length);
                OutputStream out = exchange.getResponseBody();
                out.write(payload);
                out.close();
            });
            server.setExecutor(Executors.newCachedThreadPool());
            server.start();
        }

        AiConfig config = new AiConfig();
        config.setApiUrl("http://127.0.0.1:" + server.getAddress().getPort() + "/chat/completions");
        config.setApiKey("sk-test-key-not-real");
        config.setTimeout(5000);

        service = new AiServiceImpl();
        // apiConfig 是 @Autowired 私有字段：单测不启 Spring 上下文（同 AgentRoutingTest 的做法）
        ReflectionTestUtils.setField(service, "aiConfig", config);
    }
}
