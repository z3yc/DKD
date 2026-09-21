package com.dkd.common.agent.controller;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StreamUtils;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import com.alibaba.fastjson2.JSON;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.filter.AgentCallbackAuthFilter;
import com.dkd.common.agent.support.AgentClientAbortException;
import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.agent.support.AgentUpstreamException;
import com.dkd.common.core.domain.AjaxResult;

/**
 * 智能体网关（任务 0-6，方案 §3.3）：前端 /agent/** 一律经此转发到 Python 智能体服务。
 *
 * <p>职责边界：只做「鉴权上下文重建 + 转发 + 降级」，不含任何业务逻辑（AGENTS §2.1）。
 *
 * <p>为什么 SSE 单独一个入口而不全部走泛化代理：SSE 必须逐块写响应流（前端逐 token 渲染），
 * 而其余接口需要“先拿状态码再决定透传/降级”，两者响应模型不同，混在一个方法里必然写一堆分支判断。
 *
 * <p>为什么 SSE 用同步写 Servlet 输出流、而不是 StreamingResponseBody：本模块 dkd-common 的依赖
 * 只到 spring-web（不含 spring-webmvc），拿不到 StreamingResponseBody；且同步写反而省掉异步 servlet 的
 * asyncTimeout（Tomcat 默认 30s，会把长回复的流掐断）这类隐性配置。语义上两者一致，
 * 关键约束都是“读一块写一块并立即 flush，禁止先把响应体读完再返回”。
 *
 * <p>降级语义（方案 §3.4）：
 * <ul>
 *   <li>{@code agent.enabled=false}：非 SSE 接口返回 HTTP 503 + JSON 信封；SSE 接口返回 error+done 帧
 *       （前端对该路径只按 SSE 解析，返回 JSON 会让前端解析器直接报错，故不能混用）；</li>
 *   <li>Python 不可达/自身 5xx：同上，但提示语不同（区分“没开”和“挂了”，前者要运维开开关，后者要等等）；</li>
 *   <li>上游 4xx（参数错误/限额 429）：**原样透传**，因为它携带明确的业务语义，替换成 503 会让前端丢失原因。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@RestController
@RequestMapping("/agent")
public class AgentGatewayController
{
    private static final Logger log = LoggerFactory.getLogger(AgentGatewayController.class);

    /**
     * 上游对话路径（Python app/api/chat.py 的 router 前缀 + 路由）
     */
    private static final String CHAT_PATH = "/agent/chat";

    /**
     * 回调路径前缀：回调是 Python → Java 的单向通道，绝不经网关暴露给前端（防御性兜底）
     */
    private static final String CALLBACK_PATH_PREFIX = AgentCallbackAuthFilter.CALLBACK_PATH_PREFIX;

    private static final String DEGRADE_MSG_DISABLED = "智能体服务未启用";

    private static final String DEGRADE_MSG_UNAVAILABLE = "智能体服务暂不可用，请稍后重试";

    private final AgentProperties properties;

    private final AgentUpstreamClient upstreamClient;

    public AgentGatewayController(AgentProperties properties, AgentUpstreamClient upstreamClient)
    {
        this.properties = properties;
        this.upstreamClient = upstreamClient;
    }

    /**
     * 入口可用性探测：前端据此决定是否隐藏/禁用对话入口（降级时**不伪造**会话能力，方案 §3.3）。
     *
     * @return {@code data.enabled} 开关状态；{@code data.upstream} 为 up/down
     */
    @GetMapping("/status")
    public AjaxResult status()
    {
        boolean enabled = properties.getEnabled();
        Map<String, Object> data = new HashMap<String, Object>();
        data.put("enabled", enabled);
        // 开关关掉时不必再探活：既省一次跨进程调用，也避免“开关关了还显示 up”的误导
        data.put("upstream", enabled && upstreamClient.pingHealth() ? "up" : "down");
        return AjaxResult.success(data);
    }

    /**
     * 流式对话（SSE 透传）。
     *
     * <p>降级也通过 SSE 错误帧表达（该路径前端只按 SSE 解析，回 JSON 会让前端解析器直接报错），
     * 因此 HTTP 状态恒为 200；前端是否隐藏入口依据 {@code GET /agent/status}。
     *
     * @param request 当前请求
     * @param response 当前响应（HTTP 状态与头部必须在写第一块前确定，写后就不可改）
     * @throws IOException 写出失败（客户端断开等）
     */
    @PostMapping("/chat")
    public void chat(HttpServletRequest request, HttpServletResponse response) throws IOException
    {
        // 请求体必须在转发前读完：上游一旦开始回帧，本请求的输入流语义就不再可控
        byte[] payload = readBody(request);
        // requestId 必须先解析一次再向下传：若在响应头与转发头处各解析一次，前端拿到的 trace 与上游收到的
        // 会是两个不同的值，全链路追踪直接断掉（单测已覆盖该回归）
        String requestId = resolveRequestId(request);
        Map<String, String> upstreamHeaders = buildUpstreamHeaders(request, requestId);
        String userId = upstreamHeaders.get(AgentHeaders.USER);

        applySseHeaders(response, requestId);
        OutputStream outputStream = response.getOutputStream();
        if (!properties.getEnabled())
        {
            writeDegradeFrames(outputStream, DEGRADE_MSG_DISABLED, requestId);
            return;
        }
        try
        {
            upstreamClient.relayStream(HttpMethod.POST.name(), CHAT_PATH, request.getQueryString(), upstreamHeaders,
                    payload, outputStream);
        }
        catch (AgentClientAbortException e)
        {
            // 用户主动取消（AbortController）属正常操作，不写错误帧、不打 WARN（否则监控噪声掩盖真故障）
            log.info("客户端已断开，停止 SSE 中继 requestId={} userId={}", requestId, userId);
        }
        catch (AgentUpstreamException e)
        {
            log.warn("SSE 中继失败，返回降级帧 requestId={} userId={} upstreamStatus={} err={}", requestId, userId,
                    e.getStatus(), e.getMessage());
            writeDegradeFrames(outputStream, DEGRADE_MSG_UNAVAILABLE, requestId);
        }
    }

    /**
     * 泛化代理：除 SSE 与回调外的 /agent/** 一律按原方法转发。
     *
     * @param request 当前请求
     * @return 上游响应（或降级 JSON）
     */
    @RequestMapping("/**")
    public ResponseEntity<byte[]> proxy(HttpServletRequest request)
    {
        String path = resolvePath(request);
        if (isCallbackPath(path))
        {
            log.warn("拒绝经网关访问回调路径 path={}", path);
            return jsonResponse(HttpStatus.NOT_FOUND,
                    AjaxResult.error(HttpStatus.NOT_FOUND.value(), "回调路径不可经网关访问"));
        }
        if (!properties.getEnabled())
        {
            return jsonResponse(HttpStatus.SERVICE_UNAVAILABLE,
                    AjaxResult.error(HttpStatus.SERVICE_UNAVAILABLE.value(), "智能体服务未启用（agent.enabled=false）"));
        }

        String requestId = resolveRequestId(request);
        Map<String, String> upstreamHeaders = buildUpstreamHeaders(request, requestId);
        try
        {
            AgentUpstreamResult result = upstreamClient.forward(request.getMethod(), path, request.getQueryString(),
                    upstreamHeaders, readBody(request));
            if (result.isServerError())
            {
                // 上游自身 5xx 不回传原始报文：可能含堆栈/内部信息，且前端也处理不了
                log.warn("智能体上游自身故障 requestId={} path={} upstreamStatus={}", requestId, path,
                        result.getStatus());
                return jsonResponse(HttpStatus.SERVICE_UNAVAILABLE,
                        AjaxResult.error(HttpStatus.SERVICE_UNAVAILABLE.value(), DEGRADE_MSG_UNAVAILABLE));
            }
            return passthrough(result, requestId);
        }
        catch (AgentUpstreamException e)
        {
            log.warn("智能体服务不可达 requestId={} path={} err={}", requestId, path, e.getMessage());
            return jsonResponse(HttpStatus.SERVICE_UNAVAILABLE,
                    AjaxResult.error(HttpStatus.SERVICE_UNAVAILABLE.value(), DEGRADE_MSG_UNAVAILABLE));
        }
    }

    /**
     * 设置 SSE 响应头。
     *
     * <p>为什么在写第一块**之前**设置：状态与头部一旦有字节写出就 commit，之后再改无效；
     * 为什么显式声明 no-transform / X-Accel-Buffering：中间层（Nginx 等）默认会缓冲/压缩响应，
     * 把逐帧输出攒成整包，流式体验失效（方案 §3.3 的流式约束）。
     *
     * @param response 当前响应
     * @param requestId 全链路追踪 ID，回带前端便于对日志
     */
    private void applySseHeaders(HttpServletResponse response, String requestId)
    {
        response.setStatus(HttpStatus.OK.value());
        response.setContentType(MediaType.TEXT_EVENT_STREAM_VALUE + ";charset=UTF-8");
        response.setHeader(HttpHeaders.CACHE_CONTROL, "no-cache, no-transform");
        response.setHeader("X-Accel-Buffering", "no");
        response.setHeader(AgentHeaders.REQUEST_ID, requestId);
    }

    /**
     * 重建发往 Python 的白名单请求头。
     *
     * <p>**安全关键点**：这里只写“网关解析出来的值”，绝不遍历客户端头，因此客户端伪造的
     * X-Agent-User / X-Agent-Roles / X-Agent-Secret 无法传到 Python（AGENTS §7.5 鉴权不裸奔）。
     *
     * @param request 当前请求
     * @param requestId 已解析的全链路追踪 ID（不要在本方法里再生成一份）
     * @return 上游请求头
     */
    private Map<String, String> buildUpstreamHeaders(HttpServletRequest request, String requestId)
    {
        Map<String, String> headers = new LinkedHashMap<String, String>();
        AgentUserContext context = (AgentUserContext) request.getAttribute(AgentUserContext.ATTRIBUTE);
        if (context != null)
        {
            if (context.getUserId() != null)
            {
                headers.put(AgentHeaders.USER, String.valueOf(context.getUserId()));
            }
            if (StringUtils.hasText(context.getUserName()))
            {
                headers.put(AgentHeaders.USERNAME, context.getUserName());
            }
            if (StringUtils.hasText(context.getRolesHeader()))
            {
                headers.put(AgentHeaders.ROLES, context.getRolesHeader());
            }
            if (context.getRegionId() != null)
            {
                headers.put(AgentHeaders.REGION, String.valueOf(context.getRegionId()));
            }
        }
        headers.put(AgentHeaders.REQUEST_ID, requestId);
        if (StringUtils.hasText(properties.getSecret()))
        {
            // 纵深防御：回环网络信任之外再加一层服务间密钥（Python 侧当前仅在回调/运维接口强制校验，
            // 携带并不产生副作用，但未来收紧 /agent/** 校验时网关无需再改）
            headers.put(AgentHeaders.SECRET, properties.getSecret());
        }
        String accept = request.getHeader(HttpHeaders.ACCEPT);
        if (StringUtils.hasText(accept))
        {
            headers.put(HttpHeaders.ACCEPT, accept);
        }
        String contentType = request.getHeader(HttpHeaders.CONTENT_TYPE);
        if (StringUtils.hasText(contentType))
        {
            headers.put(HttpHeaders.CONTENT_TYPE, contentType);
        }
        // SSE 断点续传游标必须原样透传，否则重连会重跑整轮（Python 侧 0-10 已支持）
        String lastEventId = request.getHeader(AgentHeaders.LAST_EVENT_ID);
        if (lastEventId != null)
        {
            headers.put(AgentHeaders.LAST_EVENT_ID, lastEventId);
        }
        return headers;
    }

    /**
     * 判断是否回调路径。
     *
     * <p>为什么同时匹配“前缀本身”与“前缀 + /”：过滤器是按 {@code /agent/callback/*} 前缀映射注册的，
     * 不覆盖 {@code /agent/callback}（无尾斜杠）这一种写法；若此处只判 startsWith(前缀 + "/")，
     * 这个畸形路径会绕过拦截器直接被转发给 Python（虽然 Python 也没这个路由，但“回调不经网关”
     * 这条约束不该留一个字符的口子）。
     *
     * @param path 去掉 contextPath 的请求路径
     * @return true 表示是回调路径
     */
    private boolean isCallbackPath(String path)
    {
        return path.equals(CALLBACK_PATH_PREFIX) || path.startsWith(CALLBACK_PATH_PREFIX + "/");
    }

    /**
     * 透传上游响应（4xx 也透传：它带的是明确业务语义，如 429 token 限额）。
     *
     * @param result 上游结果
     * @param requestId 网关 requestId（上游未回带时兜底）
     * @return 响应
     */
    private ResponseEntity<byte[]> passthrough(AgentUpstreamResult result, String requestId)
    {
        HttpStatus status = HttpStatus.resolve(result.getStatus());
        if (status == null)
        {
            return jsonResponse(HttpStatus.BAD_GATEWAY,
                    AjaxResult.error(HttpStatus.BAD_GATEWAY.value(), "上游返回了无法识别的状态码"));
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set(HttpHeaders.CONTENT_TYPE, StringUtils.hasText(result.getContentType()) ? result.getContentType()
                : MediaType.APPLICATION_JSON_VALUE);
        headers.set(AgentHeaders.REQUEST_ID,
                StringUtils.hasText(result.getRequestId()) ? result.getRequestId() : requestId);
        return new ResponseEntity<byte[]>(result.getBody(), headers, status);
    }

    /**
     * 统一 JSON 响应（RuoYi 信封），显式 UTF-8 避免中文乱码。
     *
     * @param status HTTP 状态码
     * @param body 信封
     * @return 响应
     */
    private ResponseEntity<byte[]> jsonResponse(HttpStatus status, AjaxResult body)
    {
        HttpHeaders headers = new HttpHeaders();
        headers.set(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE + ";charset=UTF-8");
        return new ResponseEntity<byte[]>(JSON.toJSONBytes(body), headers, status);
    }

    /**
     * 写降级帧：先 error 再 done，让前端既知道失败原因，也能正常收尾（不悬挂连接）。
     *
     * @param outputStream SSE 输出流
     * @param message 面向用户的中文提示
     * @param requestId 全链路追踪 ID（仅日志用）
     */
    private void writeDegradeFrames(OutputStream outputStream, String message, String requestId)
    {
        Map<String, Object> error = new LinkedHashMap<String, Object>();
        error.put("msg", message);
        error.put("code", HttpStatus.SERVICE_UNAVAILABLE.value());
        error.put("degrade", true);
        StringBuilder frames = new StringBuilder();
        frames.append("event: error\ndata: ").append(JSON.toJSONString(error)).append("\n\n");
        frames.append("event: done\ndata: {\"finish_reason\":\"degrade\"}\n\n");
        try
        {
            outputStream.write(frames.toString().getBytes(StandardCharsets.UTF_8));
            outputStream.flush();
        }
        catch (IOException e)
        {
            // 客户端已经走了，写不出去是正常现象，不能因此抛异常（异步任务抛异常会污染容器日志）
            log.info("降级帧写出失败（客户端可能已断开）requestId={} err={}", requestId, e.getClass().getSimpleName());
        }
    }

    /**
     * 读取请求体。
     *
     * <p>不用 @RequestBody：byte[] 需要与 Jackson 反序列化竞争消息转换器，而这里要的是**原样字节**
     * （透传不能改变上游收到的内容），直接读流最不容易出意外。
     *
     * @param request 当前请求
     * @return 请求体字节，读取失败时返回空数组
     */
    private byte[] readBody(HttpServletRequest request)
    {
        try
        {
            return StreamUtils.copyToByteArray(request.getInputStream());
        }
        catch (IOException e)
        {
            log.warn("读取请求体失败 path={} err={}", request.getRequestURI(), e.getClass().getSimpleName());
            return new byte[0];
        }
    }

    /**
     * requestId：attribute（过滤器已解析并净化）> 请求头（同样净化，防止过滤器未生效时直通）> 新生成。
     *
     * @param request 当前请求
     * @return requestId
     */
    private String resolveRequestId(HttpServletRequest request)
    {
        Object fromAttribute = request.getAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE);
        if (fromAttribute instanceof String && StringUtils.hasText((String) fromAttribute))
        {
            return (String) fromAttribute;
        }
        return AgentRequestId.resolve(request);
    }

    /**
     * 去掉 contextPath 后的请求路径。
     *
     * @param request 当前请求
     * @return 路径
     */
    private String resolvePath(HttpServletRequest request)
    {
        String uri = request.getRequestURI();
        String contextPath = request.getContextPath();
        if (StringUtils.hasText(contextPath) && uri.startsWith(contextPath))
        {
            return uri.substring(contextPath.length());
        }
        return uri;
    }
}
