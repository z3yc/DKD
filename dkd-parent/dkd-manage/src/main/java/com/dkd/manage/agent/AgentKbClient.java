package com.dkd.manage.agent;

import java.net.HttpRetryException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.client.ResponseErrorHandler;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriUtils;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;

/**
 * 知识库管理的 Python 侧调用客户端（排期 3-6，服务间接口 {@code /agents/kb/**}）。
 *
 * <p><b>为什么 Java 只是代理、不直接读写 {@code agent_kb_*} 表：</b>
 * 知识库（文档/切片/向量/检索留痕）是**智能体自有数据**（AGENTS §7.3），
 * 切片与向量化逻辑唯一事实源在 Python；Java 只做"管理台入口 + 鉴权 + 参数预校验 + 错误映射"。
 * 在 Java 再写一套切片/向量逻辑必然与 Python 漂移，且两处都能写库会让数据难以解释。
 *
 * <p><b>安全约束：</b>
 * <ul>
 *   <li>只带服务间密钥 {@code X-Agent-Secret} 与 {@code X-Request-Id}，不透传任何用户头；</li>
 *   <li>失败时**丢弃上游响应体**，只按状态码归因（AGENTS §6.1：不把上游报文/堆栈带到前端）；</li>
 *   <li>日志只记 path/状态码/异常类型，不记请求体/响应体（内容可能含知识库正文）。</li>
 * </ul>
 *
 * <p><b>已知陷阱（AGENTS §6.1，与 AgentLlmProbeClient 同一处理）：</b>
 * JDK 对"带请求体的流式请求"收到 401/407 会抛 {@link HttpRetryException} 并丢弃响应体。
 * 这不是"不可达"：必须按状态码提示"服务间密钥不一致"，否则运维会朝"Python 挂了"排查。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Component
public class AgentKbClient
{
    private static final Logger log = LoggerFactory.getLogger(AgentKbClient.class);

    /** Python 知识库服务间路由前缀（task-16） */
    private static final String KB_PREFIX = "/agents/kb";

    /**
     * 知识库接口超时（毫秒），固定 30s。
     *
     * <p>为什么不复用 {@code agent.readTimeout}（默认 120s）：知识库入库是"切片 + 向量化"的重操作，
     * 但也远用不到两分钟；卡 120s 会让页面看起来死了。30s 覆盖切片+写库的正常耗时，
     * 超时就明确失败（运营重试比无限等待体验好）。
     */
    private static final int KB_TIMEOUT_MS = 30000;

    /** 连接超时兜底（properties 配了非正值时用） */
    private static final int DEFAULT_CONNECT_TIMEOUT_MS = 2000;

    private final AgentProperties properties;

    public AgentKbClient(AgentProperties properties)
    {
        this.properties = properties;
    }

    /**
     * 文档列表。
     *
     * @param query     查询参数（category/enabled/keyword/pageNum/pageSize；空值会被跳过）
     * @param requestId 全链路追踪 ID（可空）
     * @return Python 信封里的 data（{@code {total,rows}}）
     */
    public Object listDocuments(Map<String, String> query, String requestId)
    {
        return call(HttpMethod.GET, KB_PREFIX + "/documents", query, null, requestId);
    }

    /**
     * 文档入库（Python 侧切片/向量化/增量重建）。
     *
     * @param payload   入参（docCode/title/category/sourceType/sourceRef/content/operator）
     * @param requestId 全链路追踪 ID（可空）
     * @return Python 信封里的 data（{@code {ingest}}）
     */
    public Object ingestDocument(Map<String, Object> payload, String requestId)
    {
        return call(HttpMethod.POST, KB_PREFIX + "/documents", null, payload, requestId);
    }

    /**
     * 启用/停用文档。
     *
     * @param docCode   文档编码（调用方已按契约预校验）
     * @param enabled   0/1
     * @param requestId 全链路追踪 ID（可空）
     * @return Python 信封里的 data
     */
    public Object setEnabled(String docCode, boolean enabled, String requestId)
    {
        // docCode 已按 ^[a-z0-9][a-z0-9_\-]{2,63}$ 预校验；这里再做路径段编码是纵深防御，
        // 防止将来校验被放宽时把 "/" 之类字符带进上游路径
        String path = KB_PREFIX + "/documents/" + UriUtils.encodePathSegment(docCode, StandardCharsets.UTF_8)
                + "/enabled";
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("enabled", Integer.valueOf(enabled ? 1 : 0));
        return call(HttpMethod.PUT, path, null, payload, requestId);
    }

    /**
     * 知识库检索（管理台"检索测试"用；会写 agent_kb_retrieval 留痕）。
     *
     * @param payload   入参（query/topK/scene/requestId/conversationId）
     * @param requestId 全链路追踪 ID（可空；payload 里的 requestId 由 Controller 一并写入）
     * @return Python 信封里的 data（hits/retrievalId/fallbackReason）
     */
    public Object search(Map<String, Object> payload, String requestId)
    {
        return call(HttpMethod.POST, KB_PREFIX + "/search", null, payload, requestId);
    }

    /**
     * 知识库统计（文档数/切片数/检索次数）。
     *
     * @param requestId 全链路追踪 ID（可空）
     * @return Python 信封里的 data
     */
    public Object stats(String requestId)
    {
        return call(HttpMethod.GET, KB_PREFIX + "/stats", null, null, requestId);
    }

    // ------------------------------------------------------------------------------------
    // HTTP 基础设施
    // ------------------------------------------------------------------------------------

    /**
     * 统一调用 + 结果解包 + 错误归因。
     *
     * @param method    HTTP 方法
     * @param path      上游路径（以 / 开头）
     * @param query     查询参数（可 null）
     * @param body      请求体（可 null）
     * @param requestId 全链路追踪 ID（可 null）
     * @return 信封 data
     * @throws AgentKbUpstreamException 传输失败 / 非 2xx / 信封不成功
     */
    private Object call(HttpMethod method, String path, Map<String, String> query, Object body, String requestId)
    {
        RestTemplate restTemplate = buildRestTemplate();
        HttpHeaders headers = new HttpHeaders();
        if (body != null)
        {
            headers.setContentType(MediaType.APPLICATION_JSON);
        }
        if (StringUtils.hasText(properties.getSecret()))
        {
            headers.set(AgentHeaders.SECRET, properties.getSecret());
        }
        if (StringUtils.hasText(requestId))
        {
            headers.set(AgentHeaders.REQUEST_ID, requestId);
        }
        HttpEntity<String> request = new HttpEntity<String>(body == null ? null : JSON.toJSONString(body), headers);
        String url = normalizeBaseUrl(properties.getBaseUrl()) + path + buildQueryString(query);
        ResponseEntity<String> response;
        try
        {
            response = restTemplate.exchange(url, method, request, String.class);
        }
        catch (RuntimeException e)
        {
            throw translateTransportFailure(e, path);
        }
        int status = response.getStatusCodeValue();
        if (!response.getStatusCode().is2xxSuccessful())
        {
            log.warn("知识库接口返回非 2xx path={} status={} requestId={}", path, Integer.valueOf(status), requestId);
            if (status == 401 || status == 407)
            {
                // 鉴权拒绝要指名道姓：否则会被当成"服务挂了"，运维排查方向完全相反
                throw new AgentKbUpstreamException(Integer.valueOf(status), false,
                        "知识库服务鉴权失败（HTTP " + status + "），请检查服务间密钥配置");
            }
            throw new AgentKbUpstreamException(Integer.valueOf(status), false,
                    "知识库接口返回错误（HTTP " + status + "），请稍后重试");
        }
        return unwrapEnvelope(response.getBody(), status, path, requestId);
    }

    /**
     * 把传输层异常翻译成安全的上游异常（含 401/407 的 HttpRetryException 陷阱）。
     *
     * @param e    原始异常
     * @param path 上游路径（日志用）
     * @return 安全的 {@link AgentKbUpstreamException}
     */
    private AgentKbUpstreamException translateTransportFailure(RuntimeException e, String path)
    {
        HttpRetryException retry = findCause(e, HttpRetryException.class);
        if (retry != null && retry.responseCode() > 0)
        {
            int status = retry.responseCode();
            // 只记状态码与异常类型，不记响应体（JDK 这时也已经把它丢了）
            log.warn("知识库接口返回 {} 且 JDK 无法重放请求体（HttpRetryException） path={}", Integer.valueOf(status),
                    path);
            return new AgentKbUpstreamException(Integer.valueOf(status), false,
                    "知识库服务鉴权失败（HTTP " + status + "），请检查服务间密钥配置");
        }
        log.warn("知识库服务不可达 path={} err={}", path, e.getClass().getSimpleName());
        return new AgentKbUpstreamException(null, true, "知识库服务不可用（连接失败或超时），请稍后重试");
    }

    /**
     * 取出 RuoYi 信封里的 data。
     *
     * <p>为什么兼容"没有信封"的形状：Python 侧接口都按 {@code {code,msg,data}} 返回（task-16 契约），
     * 但本地旧版本/反向代理可能给出裸 JSON。这里对裸 JSON 原样当 data 返回（宽容读取），
     * 而对 {@code code != 200} 的信封明确失败——不能把上游错误信封当成功数据塞给前端。
     *
     * @param body      响应体
     * @param status    HTTP 状态（日志/异常用）
     * @param path      上游路径（日志用）
     * @param requestId 追踪 ID（日志用）
     * @return 信封 data
     */
    private Object unwrapEnvelope(String body, int status, String path, String requestId)
    {
        if (!StringUtils.hasText(body))
        {
            log.warn("知识库接口返回空响应 path={} status={} requestId={}", path, Integer.valueOf(status), requestId);
            throw new AgentKbUpstreamException(Integer.valueOf(status), false, "知识库接口返回空响应，请稍后重试");
        }
        JSONObject root;
        try
        {
            root = JSON.parseObject(body);
        }
        catch (RuntimeException e)
        {
            log.warn("知识库接口响应不是 JSON path={} status={} requestId={} err={}", path, Integer.valueOf(status),
                    requestId, e.getClass().getSimpleName());
            throw new AgentKbUpstreamException(Integer.valueOf(status), false, "知识库接口响应格式异常，请稍后重试");
        }
        if (root == null)
        {
            throw new AgentKbUpstreamException(Integer.valueOf(status), false, "知识库接口返回空响应，请稍后重试");
        }
        if (!root.containsKey("code"))
        {
            // 裸 JSON：原样作为 data（不猜测信封字段）
            return root;
        }
        Integer code = root.getInteger("code");
        if (code != null && code.intValue() != 200)
        {
            // 只用 code 归因，不回传上游 msg/正文：Python 的 500 信封可能含内部细节（AGENTS §6.1）
            log.warn("知识库接口信封失败 path={} code={} requestId={}", path, code, requestId);
            throw new AgentKbUpstreamException(Integer.valueOf(status), false,
                    "知识库接口返回错误（code=" + code + "），请稍后重试");
        }
        Object data = root.get("data");
        return data == null ? new LinkedHashMap<String, Object>() : data;
    }

    private String buildQueryString(Map<String, String> query)
    {
        if (query == null || query.isEmpty())
        {
            return "";
        }
        StringBuilder builder = new StringBuilder();
        for (Map.Entry<String, String> entry : query.entrySet())
        {
            if (!StringUtils.hasText(entry.getKey()) || !StringUtils.hasText(entry.getValue()))
            {
                continue;
            }
            builder.append(builder.length() == 0 ? '?' : '&');
            builder.append(UriUtils.encodeQueryParam(entry.getKey(), StandardCharsets.UTF_8));
            builder.append('=');
            builder.append(UriUtils.encodeQueryParam(entry.getValue(), StandardCharsets.UTF_8));
        }
        return builder.toString();
    }

    /**
     * 建一个短超时 RestTemplate（每次新建：配置变化无需重启，且不与其它通道共享连接池/超时）。
     *
     * @return RestTemplate
     */
    private RestTemplate buildRestTemplate()
    {
        int connectTimeout = properties.getConnectTimeout() > 0 ? properties.getConnectTimeout()
                : DEFAULT_CONNECT_TIMEOUT_MS;
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeout);
        factory.setReadTimeout(KB_TIMEOUT_MS);
        RestTemplate template = new RestTemplate(factory);
        // 不把 4xx/5xx 当异常抛：这里需要按状态码给出不同提示（401 鉴权 vs 5xx 服务故障）
        template.setErrorHandler(new PassThroughResponseErrorHandler());
        return template;
    }

    private String normalizeBaseUrl(String baseUrl)
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
     * 不把 4xx/5xx 当异常：状态码交给调用方做错误映射。
     */
    private static class PassThroughResponseErrorHandler implements ResponseErrorHandler
    {
        @Override
        public boolean hasError(ClientHttpResponse response)
        {
            return false;
        }

        @Override
        public void handleError(ClientHttpResponse response)
        {
            // 上游错误由调用方按状态码处理，此处无动作
        }
    }
}
