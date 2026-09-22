package com.dkd.common.agent.config;

import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import com.dkd.common.agent.filter.AgentCallbackAuthFilter;
import com.dkd.common.agent.filter.AgentTokenFilter;
import com.dkd.common.agent.support.AgentRegionResolver;
import com.dkd.common.agent.support.DefaultAgentRegionResolver;

/**
 * 智能体网关装配（任务 0-6 / 0-7）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@Configuration
public class AgentConfig
{
    /**
     * 回调过滤器注册顺序。
     *
     * <p>为什么是负数：Spring Security 过滤链的 order 为 -100（SecurityProperties.DEFAULT_FILTER_ORDER），
     * Servlet 过滤器中数值越小越先执行，故取 -200 保证回调鉴权先于安全链。
     */
    private static final int CALLBACK_FILTER_ORDER = -200;

    /**
     * 注册令牌注入过滤器（/agent/**）。
     *
     * <p>为什么用 FilterRegistrationBean 显式注册、而过滤器类上不加 @Component：Spring Boot 会把
     * 容器里所有 Filter 类型的 Bean 自动注册到 {@code /*}，与“只处理 /agent/*”的意图冲突，
     * 变成对所有请求（含静态资源、登录）都跑一遍，属隐性性能与兼容风险。
     *
     * <p>为什么 order=0：Spring Security 过滤链 order=-100（数值越小越先执行），
     * 本过滤器因此在其**内部**执行，能读到已被 JwtAuthenticationTokenFilter 填充的 SecurityContext。
     *
     * @param regionResolverProvider 区域解析扩展点（当前默认实现返回 null，见 DefaultAgentRegionResolver）
     * @return 过滤器注册
     */
    @Bean
    public FilterRegistrationBean<AgentTokenFilter> agentTokenFilterRegistration(
            ObjectProvider<AgentRegionResolver> regionResolverProvider)
    {
        AgentTokenFilter filter = new AgentTokenFilter(regionResolverProvider.getIfAvailable());
        FilterRegistrationBean<AgentTokenFilter> registration = new FilterRegistrationBean<AgentTokenFilter>(filter);
        registration.addUrlPatterns("/agent/*");
        registration.setName("agentTokenFilter");
        registration.setOrder(0);
        return registration;
    }

    /**
     * 注册回调鉴权过滤器（/agent/callback/*）。
     *
     * <p>为什么单独注册而不是在 {@link AgentTokenFilter} 里做：用户侧转发走 JWT，回调走服务间密钥，
     * 两条链路的身份体系完全不同（AGENTS §7.5），混在一个过滤器里迟早会把校验套到错误的路径上。
     *
     * <p>为什么 order=-200（**先于** Spring Security 的 -100）：回调命名空间的准入必须由服务间密钥
     * 单独守门。若排在安全链之后，未登记的回调子路径会先被 Security 以 401 拦掉，
     * 本过滤器的“非白名单 → 404”分支永远不可达（白名单形同装饰），且回调鉴权就变成依赖
     * {@code @Anonymous} 注解是否被后人误删。放在最前面后：无密钥/伪造密钥 → 401，
     * 密钥正确但路径未登记 → 404，与任务 0-7 的验收口径一致（两种情况都不进业务）。
     *
     * @param properties 智能体配置（提供密钥与白名单）
     * @return 过滤器注册
     */
    @Bean
    public FilterRegistrationBean<AgentCallbackAuthFilter> agentCallbackAuthFilterRegistration(
            AgentProperties properties)
    {
        FilterRegistrationBean<AgentCallbackAuthFilter> registration = new FilterRegistrationBean<AgentCallbackAuthFilter>(
                new AgentCallbackAuthFilter(properties));
        registration.addUrlPatterns(AgentCallbackAuthFilter.CALLBACK_URL_PATTERN);
        registration.setName("agentCallbackAuthFilter");
        registration.setOrder(CALLBACK_FILTER_ORDER);
        return registration;
    }

    /**
     * 默认区域解析器。
     *
     * <p>注意：这里刻意不使用 @ConditionalOnMissingBean —— 它只在自动配置类中语义可靠，
     * 普通 @Configuration 之间无序，可能出现“双 Bean”导致 {@code ObjectProvider.getIfAvailable()}
     * 显式失败。若将来出现第二个实现，注入点会立即报错，强制先决策（标注 @Primary 或替换本 Bean），
     * 这比“悄悄用了错的解析器”安全。
     *
     * @return 默认实现（恒返回 null）
     */
    @Bean
    public AgentRegionResolver agentRegionResolver()
    {
        return new DefaultAgentRegionResolver();
    }
}
