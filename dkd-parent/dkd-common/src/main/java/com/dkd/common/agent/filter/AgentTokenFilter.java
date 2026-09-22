package com.dkd.common.agent.filter;

import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.web.filter.OncePerRequestFilter;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.support.AgentRegionResolver;
import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.core.domain.entity.SysRole;
import com.dkd.common.core.domain.entity.SysUser;
import com.dkd.common.core.domain.model.LoginUser;
import com.dkd.common.utils.SecurityUtils;
import com.dkd.common.utils.StringUtils;

/**
 * 网关注入用户身份（任务 0-6，方案 §3.3 的 AgentTokenFilter）。
 *
 * <p>为什么用 filter 而不是在 Controller 里解析：身份解析与转发解耦，Controller 只负责“把 attribute 里的
 * 身份重新拼成白名单头”，无需再接触 SecurityContext；同时 requestId 需要覆盖该请求的所有下游日志。
 *
 * <p>为什么把身份放 request attribute 而不改写请求头：见 {@link AgentUserContext} 类注释（防伪造）。
 *
 * <p>为什么注册顺序必须在 Spring Security 过滤链之后：本类依赖 SecurityContext 已被
 * {@code JwtAuthenticationTokenFilter} 填充。{@link com.dkd.common.agent.config.AgentConfig}
 * 用 order=0 注册（Security 链为 order=-100，数值越小越先执行，故本过滤器在其**内部**执行）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public class AgentTokenFilter extends OncePerRequestFilter
{
    private static final Logger log = LoggerFactory.getLogger(AgentTokenFilter.class);

    /**
     * 区域解析扩展点，可为 null（无实现时按“没注入区域头”处理）
     */
    private final AgentRegionResolver regionResolver;

    public AgentTokenFilter(AgentRegionResolver regionResolver)
    {
        this.regionResolver = regionResolver;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request)
    {
        // CORS 预检请求不带 Authorization，放行交给 Spring Security 的 CorsFilter；
        // 否则预检会被判成“未登录”，前端拿不到 CORS 头（AGENTS §2.2 前端铁律）
        return HttpMethod.OPTIONS.matches(request.getMethod());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException
    {
        String requestId = resolveRequestId(request);
        request.setAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE, requestId);
        try
        {
            LoginUser loginUser = SecurityUtils.getLoginUser();
            if (loginUser != null)
            {
                request.setAttribute(AgentUserContext.ATTRIBUTE, buildContext(loginUser, requestId));
            }
        }
        catch (RuntimeException e)
        {
            // 未登录会走到这里（SecurityUtils 取不到身份抛 ServiceException）：
            // 这里刻意不返回 401 —— /agent/** 已由 Spring Security 要求认证，重复造 401 会与
            // 全局异常处理/前端 401 跳登录逻辑打架，交给安全链统一处理更一致
            log.debug("未解析到登录用户，交由安全过滤链处理 path={} requestId={}", request.getRequestURI(), requestId);
        }
        chain.doFilter(request, response);
    }

    /**
     * 组装身份上下文。
     *
     * @param loginUser 当前登录用户
     * @param requestId 全链路追踪 ID
     * @return 不可变身份上下文
     */
    private AgentUserContext buildContext(LoginUser loginUser, String requestId)
    {
        Long regionId = null;
        if (regionResolver != null)
        {
            try
            {
                regionId = regionResolver.resolveRegionId(loginUser);
            }
            catch (RuntimeException e)
            {
                // 区域只是“锦上添花”的上下文，解析失败不能让整个对话不可用
                log.warn("区域解析失败，按未注入 X-Agent-Region 处理 requestId={} err={}", requestId,
                        e.getMessage());
            }
        }
        return new AgentUserContext(loginUser.getUserId(), loginUser.getUsername(), collectRoles(loginUser), regionId);
    }

    /**
     * 取角色标识（roleKey）。
     *
     * <p>为什么不用 LoginUser.permissions：那是 `manage:task:add` 这类权限串，Python 侧要的是
     * “这个人是什么角色”用于权限对齐与审计留痕，两者语义不同，混用会让下游判断失准。
     *
     * @param loginUser 当前登录用户
     * @return 角色标识列表（去重、保持顺序）
     */
    private Set<String> collectRoles(LoginUser loginUser)
    {
        Set<String> roleKeys = new LinkedHashSet<String>();
        SysUser sysUser = loginUser.getUser();
        if (sysUser == null || sysUser.getRoles() == null)
        {
            return roleKeys;
        }
        List<SysRole> roles = sysUser.getRoles();
        for (SysRole role : roles)
        {
            if (role != null && StringUtils.isNotEmpty(role.getRoleKey()))
            {
                roleKeys.add(role.getRoleKey());
            }
        }
        return roleKeys;
    }

    /**
     * requestId 优先级：客户端已注入的请求头（经净化）> 生成新 ID。
     *
     * <p>为什么要复用而不是强制新生成：前端日志与本条链路要能对上，重生成会把前端已记录的 trace 打断。
     * 净化规则见 {@link AgentRequestId#sanitize(String)}（防日志注入与非法响应头）。
     *
     * @param request 当前请求
     * @return requestId
     */
    private String resolveRequestId(HttpServletRequest request)
    {
        return AgentRequestId.resolve(request);
    }
}
