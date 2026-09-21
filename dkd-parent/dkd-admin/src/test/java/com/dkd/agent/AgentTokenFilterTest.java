package com.dkd.agent;

import java.util.Arrays;
import java.util.Collections;
import javax.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.filter.AgentTokenFilter;
import com.dkd.common.agent.support.AgentRegionResolver;
import com.dkd.common.core.domain.entity.SysRole;
import com.dkd.common.core.domain.entity.SysUser;
import com.dkd.common.core.domain.model.LoginUser;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

/**
 * 网关注入身份过滤器单测（任务 0-6）。
 *
 * <p>覆盖点：已登录注入身份、未登录不注入、**客户端伪造身份头无效**（安全关键拒绝路径）、
 * CORS 预检放行。全部为纯单测，不启 Spring 上下文、不连数据库/Redis（AGENTS §8）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
class AgentTokenFilterTest
{
    private final FilterChain chain = mock(FilterChain.class);

    @AfterEach
    void clearContext()
    {
        SecurityContextHolder.clearContext();
    }

    @Test
    void 已登录时注入真实身份并生成requestId() throws Exception
    {
        loginAs(7L, "ops", "admin");

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        MockHttpServletResponse response = new MockHttpServletResponse();

        new AgentTokenFilter(null).doFilter(request, response, chain);

        AgentUserContext context = (AgentUserContext) request.getAttribute(AgentUserContext.ATTRIBUTE);
        assertThat(context).as("已登录必须注入身份").isNotNull();
        assertThat(context.getUserId()).isEqualTo(7L);
        assertThat(context.getUserName()).isEqualTo("ops");
        assertThat(context.getRolesHeader()).isEqualTo("admin");
        assertThat(context.getRegionId()).as("当前 schema 无法解析区域，应为 null").isNull();
        assertThat(request.getAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE)).as("requestId 必须生成").isNotNull();
        verify(chain).doFilter(any(), any());
    }

    @Test
    void 复用客户端已有的requestId() throws Exception
    {
        loginAs(9L, "ops2", "common");

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        request.addHeader("X-Request-Id", "rid-from-frontend");

        new AgentTokenFilter(null).doFilter(request, new MockHttpServletResponse(), chain);

        assertThat(request.getAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE)).isEqualTo("rid-from-frontend");
    }

    @Test
    void 客户端伪造身份头不影响注入的真实身份() throws Exception
    {
        loginAs(7L, "ops", "admin");

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        // 攻击者视角：伪造一个管理员/别的用户
        request.addHeader("X-Agent-User", "999");
        request.addHeader("X-Agent-Username", "root");
        request.addHeader("X-Agent-Roles", "admin,super");

        new AgentTokenFilter(null).doFilter(request, new MockHttpServletResponse(), chain);

        AgentUserContext context = (AgentUserContext) request.getAttribute(AgentUserContext.ATTRIBUTE);
        assertThat(context.getUserId()).as("必须以 JWT 解析出的身份为准").isEqualTo(7L);
        assertThat(context.getUserName()).isEqualTo("ops");
        assertThat(context.getRolesHeader()).isEqualTo("admin");
    }

    @Test
    void 未登录时不注入身份() throws Exception
    {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");

        new AgentTokenFilter(null).doFilter(request, new MockHttpServletResponse(), chain);

        assertThat(request.getAttribute(AgentUserContext.ATTRIBUTE)).as("未登录不得注入身份").isNull();
        verify(chain).doFilter(any(), any());
    }

    @Test
    void CORS预检请求直接放行且不注入身份() throws Exception
    {
        loginAs(7L, "ops", "admin");

        MockHttpServletRequest request = new MockHttpServletRequest("OPTIONS", "/agent/chat");

        new AgentTokenFilter(null).doFilter(request, new MockHttpServletResponse(), chain);

        assertThat(request.getAttribute(AgentUserContext.ATTRIBUTE)).isNull();
        verify(chain).doFilter(any(), any());
    }

    @Test
    void 区域解析器抛异常时不阻断请求() throws Exception
    {
        loginAs(7L, "ops", "admin");

        AgentRegionResolver brokenResolver = loginUser -> {
            throw new IllegalStateException("region table unreachable");
        };
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");

        new AgentTokenFilter(brokenResolver).doFilter(request, new MockHttpServletResponse(), chain);

        AgentUserContext context = (AgentUserContext) request.getAttribute(AgentUserContext.ATTRIBUTE);
        assertThat(context).as("区域解析失败不能把整个对话打断").isNotNull();
        assertThat(context.getRegionId()).isNull();
    }

    @Test
    void requestId含控制字符时被净化() throws Exception
    {
        loginAs(11L, "ops3", "common");

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        // 换行/制表符：可用于日志注入；且含 \n 的头写回响应会被 Tomcat 判为非法而抛异常
        // 后半段超长：验证 64 字符上限截断
        request.addHeader("X-Request-Id", "rid\nFAKE-LOG-LINE\t" + repeat('x', 200));

        new AgentTokenFilter(null).doFilter(request, new MockHttpServletResponse(), chain);

        Object requestId = request.getAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE);
        assertThat(requestId).as("必须仍能给出可用的 requestId").isNotNull();
        assertThat(requestId.toString()).doesNotContain("\n").doesNotContain("\t");
        assertThat(requestId.toString()).as("超长必须截断").hasSizeLessThanOrEqualTo(64);
        assertThat(requestId.toString()).as("可见字符保留").startsWith("ridFAKE-LOG-LINE");
    }

    @Test
    void requestId全为非法字符时改为生成新值() throws Exception
    {
        loginAs(12L, "ops4", "common");

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/agent/chat");
        request.addHeader("X-Request-Id", "\n\r\t ");

        new AgentTokenFilter(null).doFilter(request, new MockHttpServletResponse(), chain);

        String requestId = (String) request.getAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE);
        assertThat(requestId).as("净化后为空则必须生成新 ID，绝不能把空值传给下游").hasSize(32);
    }

    /**
     * 构造并放入 SecurityContext，模拟 JwtAuthenticationTokenFilter 已完成认证
     */
    private void loginAs(Long userId, String userName, String roleKey)
    {
        SysUser sysUser = new SysUser();
        sysUser.setUserId(userId);
        sysUser.setUserName(userName);
        SysRole role = new SysRole();
        role.setRoleKey(roleKey);
        sysUser.setRoles(Arrays.asList(role));

        LoginUser loginUser = new LoginUser(userId, 100L, sysUser, Collections.<String> emptySet());
        UsernamePasswordAuthenticationToken authentication = new UsernamePasswordAuthenticationToken(loginUser, null,
                loginUser.getAuthorities());
        SecurityContextHolder.getContext().setAuthentication(authentication);
    }

    /**
     * 生成长度为 count 的重复字符串（不用 String.repeat，避免依赖 JDK11 API —— 本项目编译目标 Java 8）
     */
    private static String repeat(char ch, int count)
    {
        char[] chars = new char[count];
        Arrays.fill(chars, ch);
        return new String(chars);
    }
}
