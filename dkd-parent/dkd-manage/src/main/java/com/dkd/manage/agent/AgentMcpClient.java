package com.dkd.manage.agent;

import java.net.HttpRetryException;
import java.nio.charset.StandardCharsets;

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
 * MCP 管理的 Python 侧调用客户端（排期 3-7，服务间接口 {@code /agents/mcp/**}）。
 *
 * <p><b>为什么只代理 probe/sync/tools、不代理 tools/call：</b>
 * Python 侧确有 {@code POST /agents/mcp/call/{code}/{tool}}（只读测试），但管理台若开一个
 * "任意工具调用"入口，等于给后台账号一个可绕过人工确认的外呼通道；首期 S12 只需要
 * "探测 → 同步 → 看工具清单"，试调能力留到有明确用例时再评审开放（写操作永远只走人工确认）。
 *
 * <p><b>安全约束：</b>只带服务间密钥 {@code X-Agent-Secret} 与 {@code X-Request-Id}，
 * 不透传用户头；失败丢弃上游响应体、只按状态码归因；日志不记请求/响应体。
 *
 * <p><b>401/407 陷阱（AGENTS §6.1，与 AgentKbClient/AgentLlmProbeClient 同一处理）：</b>
 * JDK 对"带请求体的流式请求"收到 401/407 会抛 {@link HttpRetryException} 并丢弃响应体——
 * 这不是"不可达"，必须按状态码提示"服务间密钥"。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Component
public class AgentMcpClient
{
    private static final Logger log = LoggerFactory.getLogger(AgentMcpClient.class);

    /** Python MCP 服务间路由前缀（task-19） */
    private static final String MCP_PREFIX = "/agents/mcp";

    /**
     * 超时（毫秒），固定 30s。
     *
     * <p>为什么不复用 {@code agent.readTimeout}（120s）：探测/同步要等 Python 对外部 MCP server
     * 发 JSON-RPC（其自身超时 ≤5s），30s 足够覆盖握手+工具列表；卡两分钟只会让页面看起来死了。
     */
    private static final int MCP_TIMEOUT_MS = 30000;

    /** 连接超时兜底 */
    private static final int DEFAULT_CONNECT_TIMEOUT_MS = 2000;

    private final AgentProperties properties;

    public AgentMcpClient(AgentProperties properties)
    {
        this.properties = properties;
    }

    /**
     * 探测 server（Python 会同步只读工具并回写探测结果）。
     *
     * @param serverCode server 编码
     * @param requestId  全链路追踪 ID（可空）
     * @return Python 信封里的 data（{@code {ok,latencyMs,tools,rejectedCount,errorType,errorMsg}}）
     */
    public Object probe(String serverCode, String requestId)
    {
        return call(HttpMethod.POST, MCP_PREFIX + "/probe/" + encodePath(serverCode), requestId);
    }

    /**
     * 同步只读工具（不探测，直接用已有工具列表刷新）。
     *
     * @param serverCode server 编码
     * @param requestId  全链路追踪 ID（可空）
     * @return Python 信封里的 data（{@code {synced,rejected,disabled}}）
     */
    public Object sync(String serverCode, String requestId)
    {
        return call(HttpMethod.POST, MCP_PREFIX + "/sync/" + encodePath(serverCode), requestId);
    }

    /**
     * 已同步工具列表。
     *
     * <p>为什么选"代理 Python"而不是"Java 读 agent_mcp_tool 表"：同步结果由 Python 写入
     * （upsert/停用旧工具），Java 再读一遍表就出现两套口径（谁算"已同步"、disabled 怎么算）；
     * 代理能保证页面看到的就是运行时实际生效的那份清单。
     *
     * @param serverCode server 编码
     * @param requestId  全链路追踪 ID（可空）
     * @return Python 信封里的 data（工具数组）
     */
    public Object tools(String serverCode, String requestId)
    {
        String path = MCP_PREFIX + "/tools?serverCode=" + UriUtils.encodeQueryParam(serverCode,
                StandardCharsets.UTF_8);
        return call(HttpMethod.GET, path, requestId);
    }

    /**
     * 统一调用 + 结果解包 + 错误归因。
     *
     * @param method    HTTP 方法
     * @param path      相对路径（含查询串；docCode 等已做路径段编码）
     * @param requestId 全链路追踪 ID（可空）
     * @return Python 信封 data
     */
    private Object call(HttpMethod method, String path, String requestId)
    {
        RestTemplate restTemplate = buildRestTemplate();
        HttpHeaders headers = new HttpHeaders();
        if (HttpMethod.POST.equals(method))
        {
            // probe/sync 无业务请求体，但仍要发合法 JSON（避免某些 server 对空 body 报错）
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
        HttpEntity<String> request = new HttpEntity<String>(HttpMethod.POST.equals(method) ? "{}" : null, headers);
        String url = normalizeBaseUrl(properties.getBaseUrl()) + path;
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
            log.warn("MCP 接口返回非 2xx path={} status={} requestId={}", path, Integer.valueOf(status), requestId);
            if (status == 401 || status == 407)
            {
                throw new AgentMcpUpstreamException(Integer.valueOf(status), false,
                        "MCP 服务鉴权失败（HTTP " + status + "），请检查服务间密钥配置");
            }
            if (status == 404)
            {
                // Python 侧的 load_server_by_code 只加载"启用且未删除"的配置：未启用时 probe/sync 会 404。
                // 把这一点翻译成可操作提示，而不是笼统的"接口错误"（实测契约见 send_message 的跨端对齐项）
                throw new AgentMcpUpstreamException(Integer.valueOf(status), false,
                        "MCP server 在智能体服务侧不存在或未启用（HTTP 404），请先启用后再探测/同步");
            }
            if (status == 400)
            {
                throw new AgentMcpUpstreamException(Integer.valueOf(status), false,
                        "MCP 接口请求被拒绝（HTTP 400），请检查该配置是否满足智能体侧限制后重试");
            }
            throw new AgentMcpUpstreamException(Integer.valueOf(status), false,
                    "MCP 接口返回错误（HTTP " + status + "），请稍后重试");
        }
        return unwrapEnvelope(response.getBody(), status, path, requestId);
    }

    /**
     * 传输层异常 → 安全的上游异常（含 401/407 HttpRetryException 归因）。
     *
     * @param e    原始异常
     * @param path 上游路径（日志用）
     * @return 安全异常
     */
    private AgentMcpUpstreamException translateTransportFailure(RuntimeException e, String path)
    {
        HttpRetryException retry = findCause(e, HttpRetryException.class);
        if (retry != null && retry.responseCode() > 0)
        {
            int status = retry.responseCode();
            log.warn("MCP 接口返回 {} 且 JDK 无法重放请求体（HttpRetryException） path={}", Integer.valueOf(status),
                    path);
            return new AgentMcpUpstreamException(Integer.valueOf(status), false,
                    "MCP 服务鉴权失败（HTTP " + status + "），请检查服务间密钥配置");
        }
        log.warn("MCP 服务不可达 path={} err={}", path, e.getClass().getSimpleName());
        return new AgentMcpUpstreamException(null, true, "MCP 服务不可用（连接失败或超时），请稍后重试");
    }

    /**
     * 取出 RuoYi 信封里的 data；裸 JSON 宽容当 data；信封失败不回传上游 msg。
     *
     * @param body      响应体
     * @param status    HTTP 状态
     * @param path      上游路径（日志用）
     * @param requestId 追踪 ID（日志用）
     * @return 信封 data（data 为 null 时返回空对象，避免前端拿到 null 后判空遗漏）
     */
    private Object unwrapEnvelope(String body, int status, String path, String requestId)
    {
        if (!StringUtils.hasText(body))
        {
            log.warn("MCP 接口返回空响应 path={} status={} requestId={}", path, Integer.valueOf(status), requestId);
            throw new AgentMcpUpstreamException(Integer.valueOf(status), false, "MCP 接口返回空响应，请稍后重试");
        }
        JSONObject root;
        try
        {
            root = JSON.parseObject(body);
        }
        catch (RuntimeException e)
        {
            log.warn("MCP 接口响应不是 JSON path={} status={} requestId={} err={}", path, Integer.valueOf(status),
                    requestId, e.getClass().getSimpleName());
            throw new AgentMcpUpstreamException(Integer.valueOf(status), false, "MCP 接口响应格式异常，请稍后重试");
        }
        if (root == null)
        {
            throw new AgentMcpUpstreamException(Integer.valueOf(status), false, "MCP 接口返回空响应，请稍后重试");
        }
        if (!root.containsKey("code"))
        {
            return root;
        }
        Integer code = root.getInteger("code");
        if (code != null && code.intValue() != 200)
        {
            log.warn("MCP 接口信封失败 path={} code={} requestId={}", path, code, requestId);
            throw new AgentMcpUpstreamException(Integer.valueOf(status), false,
                    "MCP 接口返回错误（code=" + code + "），请稍后重试");
        }
        Object data = root.get("data");
        return data == null ? new com.alibaba.fastjson2.JSONObject() : data;
    }

    private RestTemplate buildRestTemplate()
    {
        int connectTimeout = properties.getConnectTimeout() > 0 ? properties.getConnectTimeout()
                : DEFAULT_CONNECT_TIMEOUT_MS;
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeout);
        factory.setReadTimeout(MCP_TIMEOUT_MS);
        RestTemplate template = new RestTemplate(factory);
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

    /**
     * serverCode 已按 ^[a-z][a-z0-9_]{2,31}$ 预校验；这里再做路径段编码是纵深防御。
     *
     * @param serverCode 编码
     * @return 编码后的路径段
     */
    private String encodePath(String serverCode)
    {
        return UriUtils.encodePathSegment(serverCode, StandardCharsets.UTF_8);
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
     * 不把 4xx/5xx 当异常：按状态码给不同提示（401 鉴权 vs 5xx 服务故障）。
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
