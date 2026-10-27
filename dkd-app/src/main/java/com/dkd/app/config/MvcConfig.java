package com.dkd.app.config;

import com.dkd.app.interceptor.LogInterceptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * springMVC 配置类。
 *
 * <p>为什么排除路径收窄到两个入口：早期把 /user-service/user/** 整体排除，
 * 连"按 id 查员工"（会返回手机号）都不需要令牌，匿名即可批量枚举员工 PII
 * （审计 2026-10-21 F3）。现在只有登录与获取验证码放行，其余全部要过 LogInterceptor。
 *
 * @author ruoyi
 * @date 2026-10-21
 */
@Configuration
public class MvcConfig implements WebMvcConfigurer {

    @Autowired
    private LogInterceptor logInterceptor;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(logInterceptor)
                .addPathPatterns("/**")
                // 仅登录与获取验证码可匿名；/error 是容器错误转发路径，拦它会把 401 变成 500
                .excludePathPatterns(
                        "/user-service/user/login",
                        "/user-service/user/code/**",
                        "/error",
                        "/favicon.ico");
    }

}
