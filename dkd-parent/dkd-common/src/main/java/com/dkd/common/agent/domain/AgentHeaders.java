package com.dkd.common.agent.domain;

/**
 * Java 网关与 Python 智能体服务之间的 HTTP 头常量（方案 §3.4）。
 *
 * <p>为什么集中定义：这些头名是**跨语言契约**，Python 侧在 {@code app/security.py} 中定义了同名常量，
 * 若在 Java 侧散落成字符串字面量，改名时必然两边漏改，且泄漏点（例如忘了剥离客户端伪造头）难以审计。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public class AgentHeaders
{
    /**
     * 登录用户 ID（数字）。网关解析 JWT 后注入，Python 只信任本头，不解析 JWT
     */
    public static final String USER = "X-Agent-User";

    /**
     * 登录用户账号（sys_user.user_name），供审计留痕展示
     */
    public static final String USERNAME = "X-Agent-Username";

    /**
     * 角色标识列表（逗号分隔），供智能体侧做权限对齐
     */
    public static final String ROLES = "X-Agent-Roles";

    /**
     * 用户所属区域 ID。当前实现下默认不注入（见 DefaultAgentRegionResolver）
     */
    public static final String REGION = "X-Agent-Region";

    /**
     * 服务间密钥。Python → Java 回调用它鉴权；Java → Python 也带上，作为回环信任之外的纵深防御
     */
    public static final String SECRET = "X-Agent-Secret";

    /**
     * 全链路追踪 ID：网关注入/复用，Python 侧写入日志并回带
     */
    public static final String REQUEST_ID = "X-Request-Id";

    /**
     * SSE 断点续传游标（Python 侧按此只补发未收帧）
     */
    public static final String LAST_EVENT_ID = "Last-Event-ID";

    private AgentHeaders()
    {
    }
}
