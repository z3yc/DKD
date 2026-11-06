package com.dkd.manage.mapper;

import java.util.List;

import com.dkd.manage.domain.AgentTool;

/**
 * 智能体工具注册表 Mapper（排期 3-4「工具中心」，表 {@code agent_tool}）。
 *
 * <p><b>为什么只有 list / getById / 一个"只改三列"的 update：</b>
 * 工具行由 seed（随代码发布）与将来的 MCP 同步（排期 3-7）写入，后台只负责启停与护栏参数；
 * 不提供 insert/delete，也不提供"改 code/readOnly/builtin"的 SQL —— 能改 read_only 就等于
 * 能用一次页面操作绕过 AGENTS §7.9 的"写操作必须人工确认"红线。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentToolMapper
{
    /**
     * 工具列表（del_flag='0'，可按 scope/source/enabled 过滤，按 scope, code 排序）。
     *
     * @param query 过滤条件
     * @return 工具列表
     */
    List<AgentTool> selectAgentToolList(AgentTool query);

    /**
     * 按主键取工具（del_flag='0'）。
     *
     * @param id 主键
     * @return 工具；不存在返回 null
     */
    AgentTool selectAgentToolById(Long id);

    /**
     * 只更新可变的三个护栏字段 + 审计列。
     *
     * <p>SQL 里**故意不出现** code/source/read_only/builtin：即使有人绕过 Service 直接调本方法，
     * 也改不了工具的身份事实（纵深防御）。
     *
     * @param tool 待更新工具（必须带 id/enabled/timeoutMs/rowLimit/updateBy/updateTime）
     * @return 影响行数（0 = 记录不存在或已删除）
     */
    int updateAgentTool(AgentTool tool);
}
