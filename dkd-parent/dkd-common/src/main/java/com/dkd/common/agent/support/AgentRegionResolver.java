package com.dkd.common.agent.support;

import com.dkd.common.core.domain.model.LoginUser;

/**
 * 登录用户 → 区域 ID 的解析扩展点（方案 §3.3 要求网关注入“用户名、角色、区域”）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public interface AgentRegionResolver
{
    /**
     * 解析登录用户所属区域。
     *
     * @param loginUser 当前登录用户，调用方保证非 null
     * @return 区域 ID；解析不出时返回 null（网关注入头时跳过该字段）
     */
    Long resolveRegionId(LoginUser loginUser);
}
