package com.dkd.common.agent.filter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.web.filter.OncePerRequestFilter;
import com.alibaba.fastjson2.JSON;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.utils.ServletUtils;
import com.dkd.common.utils.StringUtils;

/**
 * 智能体回调鉴权过滤器（任务 0-7，方案 §3.4）。
 *
 * <p>三层防护，全部 fail-closed：
 * <ol>
 *   <li><b>服务间密钥</b>：常量时间比较 X-Agent-Secret，与用户 JWT 是两套体系（AGENTS §7.5）；</li>
 *   <li><b>路径白名单</b>：未列入 agent.callback-path-whitelist 的 /agent/callback/** 一律 404；</li>
 *   <li><b>速率限制</b>：由回调 Controller 上的 @RateLimiter 承担（Redis 计数，命中即拒）。</li>
 * </ol>
 *
 * <p>为什么用 Filter 而不是 HandlerInterceptor（与任务书的差异，理由记录在此避免后人误判）：
 * 本模块 dkd-common 的依赖只有 spring-web（不含 spring-webmvc），HandlerInterceptor/WebMvcConfigurer
 * 无法编译；Filter 在 DispatcherServlet **之前**执行，对“路径存在但不在白名单”“路径根本不存在”
 * 两种情况都能统一拒绝，比拦截器更早、更不易被绕过（拦截器对未映射路径不会执行）。
 *
 * <p>为什么先校验密钥再校验白名单：鉴权应先于路由判定，未鉴权的探测不应得到“该路径是否存在”的信号。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public class AgentCallbackAuthFilter extends OncePerRequestFilter
{
    private static final Logger log = LoggerFactory.getLogger(AgentCallbackAuthFilter.class);

    /**
     * 回调路径前缀：回调是 Python → Java 的单向通道，网关也不会把它暴露给前端
     */
    public static final String CALLBACK_PATH_PREFIX = "/agent/callback";

    /**
     * 过滤器注册用的 URL 模式（与 {@link #CALLBACK_PATH_PREFIX} 保持一致）
     */
    public static final String CALLBACK_URL_PATTERN = "/agent/callback/*";

    private final AgentProperties properties;

    public AgentCallbackAuthFilter(AgentProperties properties)
    {
        this.properties = properties;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request)
    {
        // 回调不来自浏览器，正常不会有 CORS 预检；这里显式放行以免将来前端调试时被误拦成 401
        return HttpMethod.OPTIONS.matches(request.getMethod());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException
    {
        String expected = properties.getSecret();
        if (StringUtils.isEmpty(expected))
        {
            // 为什么拒绝而不是放行：忘配密钥时默认放行等于回调接口裸奔（可被内网任意进程建单）；
            // Python 侧未配置密钥时同样返回 503，两端口径一致
            log.warn("回调密钥未配置（DKD_AGENT_SERVICE_SECRET），拒绝回调请求 path={}", request.getRequestURI());
            reject(response, HttpServletResponse.SC_SERVICE_UNAVAILABLE, "回调密钥未配置，拒绝服务");
            return;
        }

        String presented = request.getHeader(AgentHeaders.SECRET);
        if (!constantTimeEquals(expected, presented))
        {
            // 只记路径与来源，绝不记密钥（无论正确的还是提交上来的）
            log.warn("服务间密钥校验失败 path={} remoteAddr={}", request.getRequestURI(), request.getRemoteAddr());
            reject(response, HttpServletResponse.SC_UNAUTHORIZED, "服务间密钥校验失败");
            return;
        }

        String path = resolvePath(request);
        if (!isWhitelisted(path))
        {
            log.warn("回调路径不在白名单内 path={}", path);
            reject(response, HttpServletResponse.SC_NOT_FOUND, "回调路径不在白名单内");
            return;
        }
        chain.doFilter(request, response);
    }

    /**
     * 常量时间比较，避免按字符短路泄漏密钥长度/前缀信息。
     *
     * @param expected 配置中的密钥
     * @param presented 请求头中的密钥
     * @return true 表示一致
     */
    private boolean constantTimeEquals(String expected, String presented)
    {
        if (presented == null)
        {
            return false;
        }
        return MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                presented.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 白名单精确匹配（不支持通配符，避免“看似白名单实则全放行”的配置事故）。
     *
     * @param path 去掉 contextPath 的请求路径
     * @return true 表示在白名单内
     */
    private boolean isWhitelisted(String path)
    {
        List<String> whitelist = properties.getCallbackPathWhitelist();
        return whitelist != null && whitelist.contains(path);
    }

    /**
     * 去掉 contextPath 后的请求路径。当前部署 context-path 为 "/"，但硬编码会在改部署时埋雷。
     *
     * @param request 当前请求
     * @return 形如 /agent/callback/task 的路径
     */
    private String resolvePath(HttpServletRequest request)
    {
        String uri = request.getRequestURI();
        String contextPath = request.getContextPath();
        if (StringUtils.isNotEmpty(contextPath) && uri.startsWith(contextPath))
        {
            return uri.substring(contextPath.length());
        }
        return uri;
    }

    /**
     * 统一拒绝响应：RuoYi 风格 JSON 信封 + 对应 HTTP 状态码。
     *
     * @param response 响应
     * @param status HTTP 状态码
     * @param message 拒绝原因（不含任何凭据）
     */
    private void reject(HttpServletResponse response, int status, String message)
    {
        ServletUtils.renderString(response, JSON.toJSONString(AjaxResult.error(status, message)));
        // 顺序不能反：renderString 内部固定写 200，必须在其之后再覆盖为真实状态码
        // （此时响应尚未 commit，覆盖有效；调用方既能按 HTTP 码判断，也能按信封 code 判断）
        response.setStatus(status);
    }
}
