package com.dkd.app.interceptor;

import com.dkd.app.common.context.EmpHolder;
import com.dkd.app.common.util.JWTUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/**
 * 登录拦截器（fail-closed）。
 *
 * <p>为什么必须 fail-closed：原实现在 Authorization 为空时直接 {@code return true}，
 * 再叠加 MvcConfig 把 /user-service/user/** 整体排除，等于工单、员工、设备接口全部匿名可读
 * （审计 2026-10-21 F1）。现在的规则很简单：没有令牌、令牌非法、令牌里没有 userId，一律 401，
 * 只有登录与发验证码两个入口放行（见 MvcConfig 的 excludePathPatterns）。
 *
 * <p>为什么在 afterCompletion 里清理 EmpHolder：ThreadLocal 不清会在 Tomcat 线程复用下
 * 把上一个请求的身份留给下一个请求，造成跨用户串号（审计 F15）。
 *
 * @author ruoyi
 * @date 2026-10-21
 */
@Slf4j
@Component
public class LogInterceptor implements HandlerInterceptor {

    private static final String AUTH_HEADER = "Authorization";

    private static final String BEARER_PREFIX = "Bearer ";

    private final JWTUtil jwtUtil;

    public LogInterceptor(JWTUtil jwtUtil) {
        this.jwtUtil = jwtUtil;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        String token = request.getHeader(AUTH_HEADER);
        if (token == null || token.trim().isEmpty()) {
            log.warn("未携带令牌，拒绝访问 path={} remoteAddr={}", request.getRequestURI(), request.getRemoteAddr());
            return reject(response);
        }
        String raw = token.startsWith(BEARER_PREFIX) ? token.substring(BEARER_PREFIX.length()).trim() : token.trim();
        try {
            EmpHolder.set(jwtUtil.decode(raw));
        } catch (Exception e) {
            // 只记异常类型与路径，绝不记令牌本身（无论正确与否）
            log.warn("令牌校验失败，拒绝访问 path={} err={}", request.getRequestURI(), e.getClass().getSimpleName());
            return reject(response);
        }
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        // 必须无条件清理：任何提前 return 或异常路径都会走到这里
        EmpHolder.remove();
    }

    /**
     * 统一 401 响应（RuoYi 风格信封）。
     *
     * @param response 当前响应
     * @return 恒为 false，调用方直接 return
     */
    private boolean reject(HttpServletResponse response) throws Exception {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json;charset=utf-8");
        response.getWriter().write("{\"code\":401,\"msg\":\"认证失败，请重新登录\"}");
        return false;
    }
}
