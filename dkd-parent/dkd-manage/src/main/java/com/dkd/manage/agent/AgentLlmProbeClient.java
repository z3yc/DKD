package com.dkd.manage.agent;

import java.net.HttpRetryException;
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

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.manage.domain.AgentLlmProfile;

/**
 * 模型连通性探测的调用客户端（排期 3-5）。
 *
 * <p>调用 Python {@code POST /agents/llm/ping}（服务间密钥），把结果归一化成
 * {@link ProbeOutcome} 交给 Service 落库。为什么要独立一个 Client 而不是把 HTTP 塞进 Service：
 * <ul>
 *   <li>Service 的单测要能 Mock 掉"上游怎么回"（成功/失败/超时），不需要起 HTTP 服务；</li>
 *   <li>HTTP 细节（超时、错误处理器、响应解析容错）集中一处，Service 只表达业务语义。</li>
 * </ul>
 *
 * <p><b>安全约束（AGENTS §7.1/§6.1）：</b>
 * 请求体只带档案里的**环境变量名**（{@code apiKeyEnv}），Java 侧从不解析密钥值；
 * 日志只记 profileCode/requestId/状态码/异常类型，**不记请求体与响应体**。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Component
public class AgentLlmProbeClient
{
    private static final Logger log = LoggerFactory.getLogger(AgentLlmProbeClient.class);

    /** Python 侧模型探测端点（排期 3-5 / Python task-13） */
    private static final String PING_PATH = "/agents/llm/ping";

    /** 超时兜底：properties 配了非正值时用它（30s） */
    private static final int DEFAULT_TIMEOUT_MS = 30000;

    /** 失败摘要落库上限（DDL error_msg VARCHAR(500)） */
    private static final int MAX_ERROR_MSG_LENGTH = 500;

    private final AgentProperties properties;

    public AgentLlmProbeClient(AgentProperties properties)
    {
        this.properties = properties;
    }

    /**
     * 执行一次连通性探测。
     *
     * <p>为什么不抛异常而是返回 {@link ProbeOutcome}：探测结果**必须落库**（失败也要留记录），
     * 用返回值表达"上游不可达"可以让 Service 先落库再决定抛不抛业务异常；
     * 若这里抛异常，调用方很容易在落库前就中断，失败记录就丢了。
     *
     * @param profile   模型档案（提供 baseUrl/model/apiKeyEnv/timeoutS/maxRetries）
     * @param requestId 全链路追踪 ID（透传给 Python，并在落库记录里保留）
     * @return 探测结果（含 transportFailure 标记）
     */
    public ProbeOutcome ping(AgentLlmProfile profile, String requestId)
    {
        int timeoutMs = resolveTimeoutMs(profile);
        RestTemplate restTemplate = buildRestTemplate(timeoutMs);
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        if (StringUtils.hasText(properties.getSecret()))
        {
            headers.set(AgentHeaders.SECRET, properties.getSecret());
        }
        if (StringUtils.hasText(requestId))
        {
            headers.set(AgentHeaders.REQUEST_ID, requestId);
        }
        // 只传环境变量名，不传密钥值：密钥解析只发生在 Python 进程内（AGENTS §7.1）
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("baseUrl", profile.getBaseUrl());
        payload.put("model", profile.getModel());
        payload.put("apiKeyEnv", profile.getApiKeyEnv());
        payload.put("timeoutS", profile.getTimeoutS());
        payload.put("maxRetries", profile.getMaxRetries());

        String url = normalizeBaseUrl(properties.getBaseUrl()) + PING_PATH;
        long startNanos = System.nanoTime();
        ResponseEntity<String> response;
        try
        {
            response = restTemplate.exchange(url, HttpMethod.POST,
                    new HttpEntity<String>(JSON.toJSONString(payload), headers), String.class);
        }
        catch (RuntimeException e)
        {
            // 已知陷阱（AGENTS §6.1，实测于网关 1-9）：JDK 对「带请求体的流式请求」收到 401/407 时
            // 不会把响应交回来，而是抛 HttpRetryException（它想带凭据重放，但流式模式下无法重发请求体）。
            // 这不是"服务不可达"：状态码是明确的鉴权拒绝，必须按状态码归类——
            // 否则"服务间密钥不一致"会被报成"Python 挂了"，运维会朝完全错误的方向排查。
            // 代价：这类响应的响应体拿不到（JDK 已丢弃），所以 errorMsg 明说"无法读取响应体"。
            HttpRetryException retry = findCause(e, HttpRetryException.class);
            if (retry != null && retry.responseCode() > 0)
            {
                log.warn("模型连通测试上游返回 {} 且 JDK 无法重放请求体（HttpRetryException），按仅状态码处理 "
                        + "profileCode={} requestId={}", Integer.valueOf(retry.responseCode()), profile.getCode(),
                        requestId);
                ProbeOutcome outcome = new ProbeOutcome();
                outcome.setTransportFailure(false);
                outcome.setOk(false);
                outcome.setHttpStatus(Integer.valueOf(retry.responseCode()));
                outcome.setLatencyMs(elapsedMs(startNanos));
                outcome.setErrorType("HttpStatus" + retry.responseCode());
                outcome.setErrorMsg("上游返回 HTTP " + retry.responseCode() + "（JDK 已丢弃响应体，无法读取具体原因）");
                return outcome;
            }
            // 传输层失败（不可达/超时）：只记异常类型与 profileCode，不记异常原始报文
            log.warn("模型连通测试上游不可达 profileCode={} requestId={} err={}", profile.getCode(), requestId,
                    e.getClass().getSimpleName());
            ProbeOutcome outcome = new ProbeOutcome();
            outcome.setTransportFailure(true);
            outcome.setOk(false);
            outcome.setLatencyMs(elapsedMs(startNanos));
            outcome.setErrorType(e.getClass().getSimpleName());
            outcome.setErrorMsg(sanitizeErrorMsg(e.getMessage()));
            return outcome;
        }
        return parseResponse(response, elapsedMs(startNanos), profile.getCode(), requestId);
    }

    /**
     * 解析上游响应；任何解析问题都归成"探测失败"，绝不抛穿（前端要能拿到并展示结论）。
     *
     * @param response   HTTP 响应
     * @param latencyMs  端到端耗时
     * @param profileCode 档案编码（日志用）
     * @param requestId  追踪 ID（日志用）
     * @return 归一化结果
     */
    private ProbeOutcome parseResponse(ResponseEntity<String> response, int latencyMs, String profileCode,
            String requestId)
    {
        ProbeOutcome outcome = new ProbeOutcome();
        outcome.setLatencyMs(latencyMs);
        outcome.setHttpStatus(Integer.valueOf(response.getStatusCodeValue()));
        JSONObject root = parseJson(response.getBody());
        if (root == null)
        {
            // 非 JSON 响应（网关错误页/空响应）：只报"拿不到结构化结果"，不回显正文
            log.warn("模型连通测试响应不是 JSON profileCode={} requestId={} httpStatus={}", profileCode, requestId,
                    Integer.valueOf(response.getStatusCodeValue()));
            outcome.setOk(false);
            outcome.setErrorType("NonJsonResponse");
            outcome.setErrorMsg("上游返回非 JSON 响应（HTTP " + response.getStatusCodeValue() + "）");
            return outcome;
        }
        // 兼容两种形状：Python 直接返回 {ok,...}，或包在 RuoYi 信封 {code,msg,data:{...}} 里
        JSONObject data = root.get("data") instanceof JSONObject ? root.getJSONObject("data") : root;
        outcome.setOk(Boolean.TRUE.equals(data.getBoolean("ok")));
        Integer serverLatency = data.getInteger("latencyMs");
        if (serverLatency != null && serverLatency.intValue() > 0)
        {
            outcome.setLatencyMs(serverLatency.intValue());
        }
        outcome.setModelServed(data.getString("modelServed"));
        outcome.setErrorType(trimToNull(data.getString("errorType")));
        outcome.setErrorMsg(sanitizeErrorMsg(data.getString("errorMsg")));
        if (outcome.getErrorMsg() == null)
        {
            // FastAPI 的 HTTPException 用 {"detail": "..."}；把它当作失败摘要，
            // 否则 401/422 这类探测失败在页面上只剩一个状态码，运营无从判断原因
            outcome.setErrorMsg(sanitizeErrorMsg(data.getString("detail")));
        }
        Integer bodyHttpStatus = data.getInteger("httpStatus");
        if (bodyHttpStatus != null)
        {
            outcome.setHttpStatus(bodyHttpStatus);
        }
        if (!response.getStatusCode().is2xxSuccessful() && outcome.getErrorType() == null)
        {
            // 非 2xx 且上游没给 errorType：用状态码补一个可读类型，避免前端看到空白原因
            outcome.setErrorType("HttpStatus" + response.getStatusCodeValue());
        }
        if (!response.getStatusCode().is2xxSuccessful())
        {
            outcome.setOk(false);
        }
        return outcome;
    }

    /**
     * 解析 JSON；失败返回 null（由调用方归成 NonJsonResponse）。
     *
     * @param body 响应文本
     * @return JSON 对象或 null
     */
    private JSONObject parseJson(String body)
    {
        if (!StringUtils.hasText(body))
        {
            return null;
        }
        try
        {
            return JSON.parseObject(body);
        }
        catch (RuntimeException e)
        {
            log.warn("模型连通测试响应解析失败 err={} bodyLength={}", e.getClass().getSimpleName(),
                    Integer.valueOf(body.length()));
            return null;
        }
    }

    /**
     * 错误摘要脱敏：去控制字符/合并空白 + 截断到 500。
     *
     * <p>为什么必须截断并去换行：error_msg 会落库并被页面展示，换行会破坏表格与日志行，
     * 超长会把其它排障信息挤出视野；同时绝不把完整响应体塞进来（AGENTS §6.1）。
     *
     * @param raw 原始消息
     * @return 可落库的摘要；无内容时返回 null
     */
    private String sanitizeErrorMsg(String raw)
    {
        if (!StringUtils.hasText(raw))
        {
            return null;
        }
        StringBuilder builder = new StringBuilder(Math.min(raw.length(), MAX_ERROR_MSG_LENGTH));
        boolean lastWasSpace = false;
        for (int i = 0; i < raw.length() && builder.length() < MAX_ERROR_MSG_LENGTH; i++)
        {
            char current = raw.charAt(i);
            boolean isSpace = current <= 0x20;
            if (isSpace)
            {
                if (!lastWasSpace)
                {
                    builder.append(' ');
                }
                lastWasSpace = true;
            }
            else
            {
                builder.append(current);
                lastWasSpace = false;
            }
        }
        String value = builder.toString().trim();
        return value.isEmpty() ? null : value;
    }

    /**
     * 建一个短超时 RestTemplate。
     *
     * <p>为什么每次新建而不复用：探测是低频人工操作，新建一个工厂对象成本可忽略，
     * 换来"超时可随配置变化、也不会与其它通道共享连接池/超时"的确定性。
     *
     * @param timeoutMs 连接/读取超时
     * @return RestTemplate
     */
    private RestTemplate buildRestTemplate(int timeoutMs)
    {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(timeoutMs);
        factory.setReadTimeout(timeoutMs);
        RestTemplate template = new RestTemplate(factory);
        // 不把 4xx/5xx 当异常抛：探测需要拿到状态码与错误类型写进记录（与网关 AgentUpstreamClient 同一取舍）
        template.setErrorHandler(new PassThroughResponseErrorHandler());
        return template;
    }

    /**
     * 超时取配置值：档案自身的 timeoutS 是"调用模型的超时"（秒），
     * 这里要的是"HTTP 往返超时"（含重试），必须比它宽，所以统一用 properties.llmProbeTimeoutMs。
     *
     * @param profile 档案（本方法暂未使用档案值，保留参数便于将来按档案配置超时）
     * @return 超时毫秒
     */
    private int resolveTimeoutMs(AgentLlmProfile profile)
    {
        int configured = properties.getLlmProbeTimeoutMs();
        return configured > 0 ? configured : DEFAULT_TIMEOUT_MS;
    }

    private int elapsedMs(long startNanos)
    {
        long elapsed = (System.nanoTime() - startNanos) / 1000000L;
        return elapsed > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int) elapsed;
    }

    private String normalizeBaseUrl(String baseUrl)
    {
        if (!StringUtils.hasText(baseUrl))
        {
            return "http://127.0.0.1:8090";
        }
        return baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
    }

    private String trimToNull(String value)
    {
        if (value == null)
        {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    /**
     * 在异常链里找指定类型的 cause（Spring 会把底层异常层层包装）。
     *
     * @param throwable 根异常
     * @param type      目标类型
     * @param <T>       目标类型
     * @return 找到的 cause；没有返回 null
     */
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
     * 探测结果（Client 内部的归一化结构；由 Service 映射成 {@code agent_llm_probe} 行）。
     *
     * <p>为什么不用 `AgentLlmProbe` 实体直接承载：探测记录是"落库后的证据"，
     * 而这里还要表达 {@code transportFailure}（是否连 Python 都没连上）这种**只在调用期有意义**的状态；
     * 落库行不应该有这个字段，混在一起会让读出来的记录出现"transportFailure 未持久化"的歧义。
     */
    public static class ProbeOutcome
    {
        /** true = 传输层失败（Python 不可达/超时），此时 httpStatus 为 null */
        private boolean transportFailure;

        /** 探测结论：Python 返回的 ok（或本地判定的失败） */
        private boolean ok;

        /** HTTP 状态码（可能来自响应体里的 httpStatus） */
        private Integer httpStatus;

        /** 端到端延迟（毫秒） */
        private int latencyMs;

        /** 服务端实际返回的模型名 */
        private String modelServed;

        /** 失败异常类型 */
        private String errorType;

        /** 失败摘要（已脱敏截断） */
        private String errorMsg;

        public boolean isTransportFailure()
        {
            return transportFailure;
        }

        public void setTransportFailure(boolean transportFailure)
        {
            this.transportFailure = transportFailure;
        }

        public boolean isOk()
        {
            return ok;
        }

        public void setOk(boolean ok)
        {
            this.ok = ok;
        }

        public Integer getHttpStatus()
        {
            return httpStatus;
        }

        public void setHttpStatus(Integer httpStatus)
        {
            this.httpStatus = httpStatus;
        }

        public int getLatencyMs()
        {
            return latencyMs;
        }

        public void setLatencyMs(int latencyMs)
        {
            this.latencyMs = latencyMs;
        }

        public String getModelServed()
        {
            return modelServed;
        }

        public void setModelServed(String modelServed)
        {
            this.modelServed = modelServed;
        }

        public String getErrorType()
        {
            return errorType;
        }

        public void setErrorType(String errorType)
        {
            this.errorType = errorType;
        }

        public String getErrorMsg()
        {
            return errorMsg;
        }

        public void setErrorMsg(String errorMsg)
        {
            this.errorMsg = errorMsg;
        }
    }

    /**
     * 不把 4xx/5xx 当异常：状态码与错误类型要写进探测记录，交给调用方决策。
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
            // 上游错误由调用方按探测语义处理，此处无动作
        }
    }
}
