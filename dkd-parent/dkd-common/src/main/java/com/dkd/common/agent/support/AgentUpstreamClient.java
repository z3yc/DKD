package com.dkd.common.agent.support;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.ClientHttpRequest;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RequestCallback;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.ResponseErrorHandler;
import org.springframework.web.client.ResponseExtractor;
import org.springframework.web.client.RestTemplate;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;

/**
 * 网关 → Python 智能体服务的 HTTP 客户端（任务 0-6）。
 *
 * <p>为什么不用第三方客户端（OkHttp/HttpClient5）：spring-web 已在 dkd-common 依赖中，
 * 少一个依赖就少一个攻击面与版本兼容问题；SSE 只需“别缓冲”，Spring 自带的
 * {@link SimpleClientHttpRequestFactory} 能逐块读到 socket 数据，够用。
 *
 * <p>两条通道刻意分开，语义不同：
 * <ul>
 *   <li>{@link #relayStream} —— SSE 用，**纯字节中继**：读一块写一块并立即 flush，
 *       不解析、不聚合、不缓存整个响应体（AGENTS §2.2 与方案 §3.3 的硬要求）；</li>
 *   <li>{@link #forward} —— 其余 JSON 接口用，缓冲响应体（有上限保护）。
 *       缓冲是为了先拿到上游状态码，再决定“透传”还是“降级为 503 友好提示”。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@Component
public class AgentUpstreamClient
{
    private static final Logger log = LoggerFactory.getLogger(AgentUpstreamClient.class);

    /**
     * 中继块大小：偏小以保证首帧尽快到达前端（SSE 体感），代价是系统调用略多
     */
    private static final int RELAY_BUFFER_BYTES = 1024;

    /**
     * 非 SSE 转发响应体上限。超过即判为异常响应（正常 JSON 接口远小于此），
     * 避免上游异常时把 JVM 内存拖垮（AGENTS §1 约束 5：单主库/小规模部署，不做无上限缓冲）
     */
    private static final int MAX_PROXY_BODY_BYTES = 4 * 1024 * 1024;

    /**
     * /health 探活超时（毫秒）。固定 1s：状态接口要快速返回，不能让前端入口卡住
     */
    private static final int PROBE_TIMEOUT_MS = 1000;

    private final AgentProperties properties;

    private final RestTemplate restTemplate;

    private final RestTemplate probeRestTemplate;

    private final String normalizedBaseUrl;

    public AgentUpstreamClient(AgentProperties properties)
    {
        this.properties = properties;
        this.restTemplate = buildRestTemplate(properties.getConnectTimeout(), properties.getReadTimeout());
        this.probeRestTemplate = buildRestTemplate(PROBE_TIMEOUT_MS, PROBE_TIMEOUT_MS);
        this.normalizedBaseUrl = normalizeBaseUrl(properties.getBaseUrl());
    }

    /**
     * 探活 Python {@code /health}。
     *
     * <p>为什么把异常全部收敛成 false：该结果只用于前端决定是否隐藏对话入口，
     * 探活失败本身就是“不可用”的一种，不应把异常抛给状态接口（否则前端拿到 500 反而不知道该怎么办）。
     *
     * @return true 表示上游返回 2xx
     */
    public boolean pingHealth()
    {
        try
        {
            HttpStatus status = probeRestTemplate
                    .getForEntity(normalizedBaseUrl + "/health", String.class).getStatusCode();
            return status.is2xxSuccessful();
        }
        catch (RuntimeException e)
        {
            log.debug("智能体服务探活失败 baseUrl={} err={}", normalizedBaseUrl, e.getClass().getSimpleName());
            return false;
        }
    }

    /**
     * SSE 字节流中继：读一块、写一块、flush 一块。
     *
     * @param method HTTP 方法
     * @param path 上游路径（以 / 开头）
     * @param query 原始查询串，可为 null
     * @param headers 已由网关重建的白名单头
     * @param body 请求体，可为 null/空
     * @param out 响应输出流（Servlet 输出流）
     * @throws AgentUpstreamException 上游不可达/超时，或返回非 200
     * @throws AgentClientAbortException 客户端主动断开导致写入失败
     */
    public void relayStream(String method, String path, String query, Map<String, String> headers, byte[] body,
            OutputStream out)
    {
        final byte[] payload = body == null ? new byte[0] : body;
        ResponseExtractor<Void> extractor = new ResponseExtractor<Void>()
        {
            @Override
            public Void extractData(ClientHttpResponse response) throws IOException
            {
                int status = response.getRawStatusCode();
                if (status != HttpStatus.OK.value())
                {
                    // 非 200 时上游回的是 JSON 错误信封，不是 SSE 帧；不能当流透传，交调用方降级
                    throw new AgentUpstreamException(status, "上游返回状态 " + status);
                }
                copyStream(response.getBody(), out);
                return null;
            }
        };
        execute(method, path, query, headers, payload, extractor);
    }

    /**
     * 非 SSE 转发：缓冲响应体后带回状态码/类型/字节，由调用方决定透传或降级。
     *
     * @param method HTTP 方法
     * @param path 上游路径（以 / 开头）
     * @param query 原始查询串，可为 null
     * @param headers 已由网关重建的白名单头
     * @param body 请求体，可为 null/空
     * @return 上游结果（状态码可达 4xx/5xx，不抛异常，便于调用方按策略处理）
     * @throws AgentUpstreamException 传输层失败（不可达/超时）
     */
    public AgentUpstreamResult forward(String method, String path, String query, Map<String, String> headers,
            byte[] body)
    {
        final byte[] payload = body == null ? new byte[0] : body;
        ResponseExtractor<AgentUpstreamResult> extractor = new ResponseExtractor<AgentUpstreamResult>()
        {
            @Override
            public AgentUpstreamResult extractData(ClientHttpResponse response) throws IOException
            {
                // 必须先读状态码再读响应体！JDK 的 HttpURLConnection.getErrorStream() 只在响应码已被读出
                // （responseCode >= 400）时才返回错误流，否则 Spring 会退回 getInputStream() 并抛
                // "Server returned HTTP response code: 4xx/5xx"，导致 4xx/5xx 响应体永远拿不到、
                // 还会被误判成“上游不可达”。实测踩坑，勿调换顺序。
                int status = response.getRawStatusCode();
                byte[] responseBody = readBounded(response.getBody(), MAX_PROXY_BODY_BYTES);
                String contentType = response.getHeaders().getFirst(HttpHeaders.CONTENT_TYPE);
                String requestId = response.getHeaders().getFirst(AgentHeaders.REQUEST_ID);
                return new AgentUpstreamResult(status, contentType, responseBody, requestId);
            }
        };
        return execute(method, path, query, headers, payload, extractor);
    }

    /**
     * 逐块复制并 flush。
     *
     * @param in 上游输入流
     * @param out 客户端输出流
     */
    private void copyStream(InputStream in, OutputStream out)
    {
        byte[] buffer = new byte[RELAY_BUFFER_BYTES];
        int read;
        try
        {
            while ((read = in.read(buffer)) != -1)
            {
                // 读一块写一块，写失败在 writeChunk 内单独归因（客户端断开 ≠ 上游故障）
                writeChunk(out, buffer, read);
            }
        }
        catch (IOException e)
        {
            throw new AgentUpstreamException(AgentUpstreamException.STATUS_TRANSPORT_FAILURE,
                    "读取上游响应流失败", e);
        }
    }

    /**
     * 写出一块并 flush。
     *
     * <p>为什么必须 flush：Tomcat 响应默认 8KB 缓冲，不 flush 会把 SSE 攒成大块再发，
     * 前端“逐 token 渲染”退化成整段返回（方案 §3.3 明令禁止先读完整包再返回）。
     *
     * @param out 客户端输出流
     * @param buffer 数据缓冲
     * @param length 本块长度
     */
    private void writeChunk(OutputStream out, byte[] buffer, int length)
    {
        try
        {
            out.write(buffer, 0, length);
            out.flush();
        }
        catch (IOException e)
        {
            // 前端 AbortController 取消 / 关闭页面都会走到这里，属正常终止（见 AgentClientAbortException 注释）
            throw new AgentClientAbortException("向客户端写出 SSE 帧失败（客户端可能已断开）", e);
        }
    }

    /**
     * 组装并按上限读取响应体。
     *
     * @param in 上游输入流
     * @param maxBytes 上限
     * @return 响应体字节
     */
    private byte[] readBounded(InputStream in, int maxBytes) throws IOException
    {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[RELAY_BUFFER_BYTES];
        int read;
        while ((read = in.read(chunk)) != -1)
        {
            if (buffer.size() + read > maxBytes)
            {
                throw new AgentUpstreamException(HttpStatus.BAD_GATEWAY.value(),
                        "上游响应体超过 " + maxBytes + " 字节上限，拒绝缓冲");
            }
            buffer.write(chunk, 0, read);
        }
        return buffer.toByteArray();
    }

    /**
     * 统一执行 + 异常归因（把 Spring 的 RestClientException 收敛成本模块的两类异常）。
     */
    private <T> T execute(String method, String path, String query, Map<String, String> headers, byte[] body,
            ResponseExtractor<T> extractor)
    {
        HttpMethod httpMethod = HttpMethod.resolve(method);
        if (httpMethod == null)
        {
            throw new AgentUpstreamException(HttpStatus.METHOD_NOT_ALLOWED.value(), "不支持的请求方法 " + method);
        }
        final Map<String, String> finalHeaders = headers == null ? new LinkedHashMap<String, String>() : headers;
        final byte[] payload = body;
        RequestCallback callback = new RequestCallback()
        {
            @Override
            public void doWithRequest(ClientHttpRequest request) throws IOException
            {
                for (Map.Entry<String, String> entry : finalHeaders.entrySet())
                {
                    request.getHeaders().set(entry.getKey(), entry.getValue());
                }
                if (payload.length > 0)
                {
                    request.getBody().write(payload);
                }
            }
        };
        String url = buildUrl(path, query);
        try
        {
            return restTemplate.execute(URI.create(url), httpMethod, callback, extractor);
        }
        catch (AgentUpstreamException e)
        {
            throw e;
        }
        catch (AgentClientAbortException e)
        {
            throw e;
        }
        catch (RuntimeException e)
        {
            AgentClientAbortException abort = findCause(e, AgentClientAbortException.class);
            if (abort != null)
            {
                throw abort;
            }
            AgentUpstreamException upstream = findCause(e, AgentUpstreamException.class);
            if (upstream != null)
            {
                throw upstream;
            }
            if (e instanceof ResourceAccessException)
            {
                throw new AgentUpstreamException(AgentUpstreamException.STATUS_TRANSPORT_FAILURE,
                        "智能体服务不可达或读取超时", e);
            }
            // 只记异常类型，不记上游原始报文（可能含用户数据，AGENTS §6.1）
            throw new AgentUpstreamException("调用智能体服务失败：" + e.getClass().getSimpleName(), e);
        }
    }

    private String buildUrl(String path, String query)
    {
        StringBuilder url = new StringBuilder(normalizedBaseUrl);
        url.append(path.startsWith("/") ? path : "/" + path);
        if (StringUtils.hasText(query))
        {
            url.append('?').append(query);
        }
        return url.toString();
    }

    private static RestTemplate buildRestTemplate(int connectTimeout, int readTimeout)
    {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeout);
        factory.setReadTimeout(readTimeout);
        RestTemplate template = new RestTemplate(factory);
        // 为什么关掉默认错误处理器：默认实现会对 4xx/5xx 抛 HttpStatusCodeException 并丢弃状态码与响应体，
        // 而网关需要按“SSE/非 SSE + 上游是否自身故障”决定降级方式（这是本模块的核心分支，不能被提前打断）
        template.setErrorHandler(new PassThroughResponseErrorHandler());
        return template;
    }

    private static String normalizeBaseUrl(String baseUrl)
    {
        if (!StringUtils.hasText(baseUrl))
        {
            return "http://127.0.0.1:8090";
        }
        return baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
    }

    private static <T extends Throwable> T findCause(Throwable throwable, Class<T> type)
    {
        Throwable current = throwable;
        while (current != null)
        {
            if (type.isInstance(current))
            {
                return type.cast(current);
            }
            current = current.getCause();
        }
        return null;
    }

    /**
     * 不把 4xx/5xx 当异常：状态码交给调用方决策（网关要按降级策略处理，而不是抛给前端）。
     */
    private static class PassThroughResponseErrorHandler implements ResponseErrorHandler
    {
        @Override
        public boolean hasError(ClientHttpResponse response) throws IOException
        {
            return false;
        }

        @Override
        public void handleError(ClientHttpResponse response) throws IOException
        {
            // 上游错误由调用方按策略处理，此处无动作
        }
    }
}
