package com.dkd.manage.mapper;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.domain.AgentConfig;
import com.dkd.manage.domain.AgentConfigHistory;

/**
 * 智能体配置 Mapper（排期 2-2，表结构见 docs/ddl/agent_config.sql）。
 *
 * <p>三条刻意约定：
 * <ol>
 *   <li><b>更新必须带版本条件</b>：{@link #updateAgentConfig(AgentConfig)} 的 WHERE 是
 *       {@code id AND version AND del_flag='0'}，影响行数 0 = 并发冲突或记录不存在，
 *       Service 据此抛 409——这条一旦被改成只按 id 更新，乐观锁就静默失效；</li>
 *   <li><b>没有 delete 方法</b>：本期不提供删除接口（停用即可），历史表又引用 code；
 *       真需要删除时按 AGENTS §7.4 走软删并另行评审；</li>
 *   <li><b>查询默认过滤 del_flag='0'</b>（AGENTS §7.4）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public interface AgentConfigMapper
{
    /**
     * 配置列表（分页由 PageHelper 负责）。
     *
     * @param query 过滤条件（code/name 模糊，runtime/enabled/builtin 精确；可为空对象）
     * @return 配置列表（params_json 为原始 JSON 文本，由 Service 转对象）
     */
    List<AgentConfig> selectAgentConfigList(AgentConfig query);

    /**
     * 按主键取单条（过滤已删除）。
     *
     * @param id 主键
     * @return 配置；不存在/已删除返回 null
     */
    AgentConfig selectAgentConfigById(Long id);

    /**
     * 名称是否已被占用（del_flag='0' 内唯一，排除自身）。
     *
     * @param name      名称
     * @param excludeId 要排除的 id（新增传 null）
     * @return 命中条数
     */
    int countAgentConfigByName(@Param("name") String name, @Param("excludeId") Long excludeId);

    /**
     * 编码是否已存在（DB 有 UK，这里提前查是为了给出字段级错误而不是 500）。
     *
     * @param code 编码
     * @return 命中条数
     */
    int countAgentConfigByCode(@Param("code") String code);

    /**
     * 当前最大场景码（用于自定义智能体自动分配 scene = max(CUSTOM_SCENE_BASE, max+1)）。
     *
     * @return 最大 scene；表为空返回 null
     */
    Integer selectMaxScene();

    /**
     * 新增配置（version 由 Service 固定写入 1）。
     *
     * @param config 配置实体
     * @return 影响行数
     */
    int insertAgentConfig(AgentConfig config);

    /**
     * 乐观锁更新（只更新可编辑列；code/runtime/scene/builtin 不可改）。
     *
     * <p>SQL 固定 {@code version = version + 1} 且 WHERE 带 {@code version = #{version}}；
     * 影响行数 0 = 版本不匹配（或记录不存在），调用方必须按 409 处理。
     *
     * @param config 配置实体（version = 提交的版本，仅用于 WHERE）
     * @return 影响行数（0 或 1）
     */
    int updateAgentConfig(AgentConfig config);

    /**
     * 新增版本快照（只增不改）。
     *
     * @param history 快照实体
     * @return 影响行数
     */
    int insertAgentConfigHistory(AgentConfigHistory history);

    /**
     * 某配置的全部历史版本（按 version 倒序，含 snapshot 全文）。
     *
     * @param code 智能体编码
     * @return 历史列表
     */
    List<AgentConfigHistory> selectAgentConfigHistoryByCode(String code);

    /**
     * 取指定编码+版本的历史快照（回滚来源）。
     *
     * @param code    智能体编码
     * @param version 历史版本号
     * @return 快照；不存在返回 null
     */
    AgentConfigHistory selectAgentConfigHistoryByCodeAndVersion(@Param("code") String code,
            @Param("version") Long version);
}
