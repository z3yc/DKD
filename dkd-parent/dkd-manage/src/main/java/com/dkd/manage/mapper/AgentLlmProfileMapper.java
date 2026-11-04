package com.dkd.manage.mapper;

import java.util.Date;
import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.domain.AgentLlmProfile;

/**
 * 模型档案 Mapper（排期 3-5，表 {@code agent_llm_profile}）。
 *
 * <p>注意：本表只有 {@code api_key_env}（环境变量名），**没有任何密钥列**；
 * 因此这里不存在"读出密钥"的 SQL，接口层也就不可能泄露密钥（AGENTS §7.1）。
 *
 * <p>{@code is_default} 的互斥由应用层保证：MySQL 没有部分唯一索引
 * （{@code UNIQUE ... WHERE is_default=1} 不支持），所以用"先清其它行、再写本行"的两条 SQL
 * 放在同一个事务里。这里提供 {@link #clearDefaultFlag} 就是为了让这两步能在 Service 的事务内完成。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentLlmProfileMapper
{
    /**
     * 档案列表（del_flag='0'，可按 code 模糊 / enabled 精确过滤）。
     *
     * @param query 过滤条件
     * @return 档案列表（默认档案在前）
     */
    List<AgentLlmProfile> selectAgentLlmProfileList(AgentLlmProfile query);

    /**
     * 按主键取档案（del_flag='0'）。
     *
     * @param id 主键
     * @return 档案；不存在返回 null
     */
    AgentLlmProfile selectAgentLlmProfileById(Long id);

    /**
     * 按编码取档案（del_flag='0'）。
     *
     * @param code 档案编码
     * @return 档案；不存在返回 null
     */
    AgentLlmProfile selectAgentLlmProfileByCode(String code);

    /**
     * 统计同编码档案数（del_flag='0'）。
     *
     * @param code      编码
     * @param excludeId 排除的主键（编辑时为当前行 id；新增传 null）
     * @return 数量
     */
    int countAgentLlmProfileByCode(@Param("code") String code, @Param("excludeId") Long excludeId);

    /**
     * 新增档案（useGeneratedKeys 回填 id）。
     *
     * @param profile 档案
     * @return 影响行数
     */
    int insertAgentLlmProfile(AgentLlmProfile profile);

    /**
     * 全列更新可编辑字段（编码不可改，不在 SET 中）。
     *
     * @param profile 档案（必须带 id 与全部可编辑字段）
     * @return 影响行数（0 = 记录不存在/已删除）
     */
    int updateAgentLlmProfile(AgentLlmProfile profile);

    /**
     * 把其它档案的 is_default 清 0（"最多一个默认档案"的互斥实现）。
     *
     * @param excludeId 保留不清的行 id（编辑时为当前档案 id；新增传 null = 全部清）
     * @param updateBy  操作者
     * @param updateTime 操作时间
     * @return 影响行数
     */
    int clearDefaultFlag(@Param("excludeId") Long excludeId, @Param("updateBy") String updateBy,
            @Param("updateTime") Date updateTime);

    /**
     * 只更新启用状态。
     *
     * @param id         主键
     * @param enabled    0/1
     * @param updateBy   操作者
     * @param updateTime 操作时间
     * @return 影响行数
     */
    int updateAgentLlmProfileEnabled(@Param("id") Long id, @Param("enabled") Integer enabled,
            @Param("updateBy") String updateBy, @Param("updateTime") Date updateTime);
}
