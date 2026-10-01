package com.dkd.common.agent.support;

import com.dkd.common.core.domain.model.LoginUser;

/**
 * 默认区域解析器：**解析不出区域，恒返回 null**，网关因此不注入 {@code X-Agent-Region} 头。
 *
 * <p>为什么解析不出（2026-09-21 核对源码 + **2026-10-01 核对该表数据**，不是偷懒）：
 * 业务侧区域挂在 {@code tb_emp.region_id}，而管理端登录主体是 {@code sys_user}。
 * 两者之间**没有任何可用的关联**：
 *
 * <ul>
 *   <li>{@code tb_emp} 没有 {@code user_id}（最初的结论）；</li>
 *   <li>它**有**一个 {@code user_name} 列，看上去可以接 {@code sys_user.user_name}——
 *       但实测（本机 dkd 库）该列存的是**中文姓名**（刘备 / 关羽 / 曹操 / 孙权 …），
 *       而非登录账号：17 行全部有值，与 {@code sys_user.user_name} 匹配 **0 行**；</li>
 *   <li>{@code sys_dept} 也没有 {@code region_id}。</li>
 * </ul>
 *
 * <p>所以“列在但数据不成立”是这里最容易踩的坑：只看 SHOW COLUMNS 会以为“实现一下就行”，
 * 写出来的解析器会在生产上常年返回 null（而且没人会去查为什么），
 * 反而比现在这个“恒返回 null + 写明原因”的实现更难发现。
 *
 * <p>影响与结论：Python 侧 {@code parse_user_context} 的 region_id 为 None，仅用于审计留痕与
 * 个性化查询的权限对齐；补货接单人分配（排期 1-8）是按**设备** region_id 匹配 tb_emp 选人，
 * 不依赖“登录用户区域”，故该缺省不阻塞 Phase 0/1（已由 1-8 的真库抽样与浏览器验收间接证实）。
 *
 * <p><b>启用条件（触发后才实现本接口）</b>：
 * ① 业务把运维人员与登录账号建立关联（把 {@code tb_emp.user_name} 改为登录账号并保证唯一，
 *    或给 {@code tb_emp} 加 {@code user_id}）；② 或给 {@code sys_dept} 加 {@code region_id}。
 * 实现时只需提供本接口的唯一实现，注入点（{@code ObjectProvider}）会立刻生效；
 * 多实现会让注入点显式失败，强制先做决策——这是故意的。
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
