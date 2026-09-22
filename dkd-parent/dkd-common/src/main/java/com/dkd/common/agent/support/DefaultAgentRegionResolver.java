package com.dkd.common.agent.support;

import com.dkd.common.core.domain.model.LoginUser;

/**
 * 默认区域解析器：**解析不出区域，恒返回 null**，网关因此不注入 {@code X-Agent-Region} 头。
 *
 * <p>为什么解析不出（2026-09-21 核对源码结论，不是偷懒）：
 * 业务侧区域挂在 {@code tb_emp.region_id}，而管理端登录主体是 {@code sys_user}，
 * 两者之间没有任何关联列（{@code tb_emp} 无 user_id，{@code sys_dept} 也无 region_id），
 * 因此当前库表结构无法把管理端登录用户映射到区域。
 *
 * <p>影响与结论：Python 侧 {@code parse_user_context} 的 region_id 为 None，仅用于审计留痕与
 * 个性化查询的权限对齐；补货接单人分配（排期 1-8）是按**设备** region_id 匹配 tb_emp 选人，
 * 不依赖“登录用户区域”，故该缺省不阻塞 Phase 0/1。待 1-8 落地或 tb_emp 增加 user_id 后，
 * 实现本接口并让 Spring 中出现唯一实现即可（多实现会让注入点 ObjectProvider 显式失败，强制先决策）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public class DefaultAgentRegionResolver implements AgentRegionResolver
{
    @Override
    public Long resolveRegionId(LoginUser loginUser)
    {
        return null;
    }
}
