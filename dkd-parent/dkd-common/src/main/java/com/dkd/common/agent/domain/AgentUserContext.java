package com.dkd.common.agent.domain;

import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * 网关解析出的调用者身份（不可变）。
 *
 * <p>为什么把它放进 request attribute 而不是直接改写成请求头：请求头是**客户端可控输入**，
 * 若身份与头同处一个命名空间，后续任何一处“顺手透传所有请求头”的实现都会把伪造身份带上游。
 * 因此约定：{@link com.dkd.common.agent.filter.AgentTokenFilter} 只写 attribute，
 * {@link com.dkd.common.agent.controller.AgentGatewayController} 只读 attribute 并在转发时重建头。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public final class AgentUserContext
{
    /**
     * 身份存放的 request attribute 键
     */
    public static final String ATTRIBUTE = AgentUserContext.class.getName();

    /**
     * 全链路 requestId 存放的 request attribute 键
     */
    public static final String REQUEST_ID_ATTRIBUTE = AgentUserContext.class.getName() + ".requestId";

    private final Long userId;

    private final String userName;

    private final Set<String> roles;

    private final Long regionId;

    /**
     * @param userId 用户 ID
     * @param userName 用户账号
     * @param roles 角色标识集合（可为空集合，不可为 null）
     * @param regionId 区域 ID，解析不出时为 null
     */
    public AgentUserContext(Long userId, String userName, Set<String> roles, Long regionId)
    {
        this.userId = userId;
        this.userName = userName;
        this.roles = roles == null ? Collections.<String> emptySet()
                : Collections.unmodifiableSet(new LinkedHashSet<String>(roles));
        this.regionId = regionId;
    }

    public Long getUserId()
    {
        return userId;
    }

    public String getUserName()
    {
        return userName;
    }

    public Set<String> getRoles()
    {
        return roles;
    }

    public Long getRegionId()
    {
        return regionId;
    }

    /**
     * 角色列表转成 X-Agent-Roles 头值（逗号分隔，与 Python 侧解析口径一致）。
     *
     * @return 逗号分隔的角色标识；无角色时返回空串
     */
    public String getRolesHeader()
    {
        if (roles.isEmpty())
        {
            return "";
        }
        return String.join(",", roles);
    }
}
