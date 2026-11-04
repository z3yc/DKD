package com.dkd.manage.mapper;

import java.util.Date;
import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.domain.AgentMcpServer;

/**
 * MCP server 配置 Mapper（排期 3-7，表 {@code agent_mcp_server}）。
 *
 * <p><b>为什么没有 delete、且 UPDATE 不含 status / last_probe_* 与 code：</b>
 * <ul>
 *   <li>{@code status / last_probe_time / last_probe_msg} 是 **Python 探测后回写**的运行时结论，
 *       由 Python 账号 UPDATE（见 DDL 授权段）；Java 也写会让"最近探测结果"出现两个写者，
 *       页面上的 status 就无法解释；</li>
 *   <li>{@code code} 是配置身份（Python 按 code 调 probe/sync，agent_mcp_tool.code 也含它），
 *       编辑只读；SQL 层也不给改的能力；</li>
 *   <li>不提供 DELETE：停用（enabled=0）即可，物理删除会让探测记录/工具同步结果悬空
 *       （AGENTS §7.4 软删除精神；本表 del_flag 由运维流程维护，不开放给页面）。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentMcpServerMapper
{
    /**
     * 配置列表（del_flag='0'）。
     *
     * @param query 过滤条件（可空；当前仅支持 enabled 精确过滤）
     * @return 配置列表
     */
    List<AgentMcpServer> selectAgentMcpServerList(AgentMcpServer query);

    /**
     * 按主键取配置（del_flag='0'）。
     *
     * @param id 主键
     * @return 配置；不存在返回 null
     */
    AgentMcpServer selectAgentMcpServerById(Long id);

    /**
     * 按编码取配置（del_flag='0'）。
     *
     * @param code server 编码
     * @return 配置；不存在返回 null
     */
    AgentMcpServer selectAgentMcpServerByCode(String code);

    /**
     * 统计同编码配置数（del_flag='0'）。
     *
     * @param code      编码
     * @param excludeId 排除的主键（编辑时为当前行；新增传 null）
     * @return 数量
     */
    int countAgentMcpServerByCode(@Param("code") String code, @Param("excludeId") Long excludeId);

    /**
     * 新增配置（useGeneratedKeys 回填 id）。
     *
     * @param server 配置（enabled 由 Service 决定：新增默认 0）
     * @return 影响行数
     */
    int insertAgentMcpServer(AgentMcpServer server);

    /**
     * 更新可编辑配置（不含 code/status/last_probe_*）。
     *
     * @param server 配置（必须带 id 与全部可编辑字段）
     * @return 影响行数（0 = 不存在/已删除）
     */
    int updateAgentMcpServer(AgentMcpServer server);

    /**
     * 只更新启用状态。
     *
     * @param id         主键
     * @param enabled    0/1
     * @param updateBy   操作者
     * @param updateTime 操作时间
     * @return 影响行数
     */
    int updateAgentMcpServerEnabled(@Param("id") Long id, @Param("enabled") Integer enabled,
            @Param("updateBy") String updateBy, @Param("updateTime") Date updateTime);
}
