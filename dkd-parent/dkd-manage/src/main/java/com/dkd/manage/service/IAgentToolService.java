package com.dkd.manage.service;

import java.util.List;

import com.dkd.manage.domain.AgentTool;
import com.dkd.manage.domain.dto.AgentToolEditDto;

/**
 * 智能体工具中心 Service（排期 3-4）。
 *
 * <p>只提供"查列表 + 改三个护栏字段"，不提供增删：工具行由 seed / MCP 同步写入，
 * 后台只负责启停与超时/行数护栏（详情见 {@code AgentTool} 的类注释）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentToolService
{
    /**
     * 工具列表（可按 scope/source/enabled 过滤，按 scope, code 排序）。
     *
     * @param query 过滤条件（可 null）
     * @return 工具列表
     */
    List<AgentTool> selectAgentToolList(AgentTool query);

    /**
     * 更新工具的 enabled/timeoutMs/rowLimit（其余字段只读）。
     *
     * @param id       主键
     * @param dto      入参（不可变字段传了不同值 → 字段错误）
     * @param operator 操作者
     * @return 更新后的工具
     * @throws com.dkd.common.exception.ServiceException 工具不存在/更新失败
     * @throws com.dkd.manage.agent.AgentFieldErrorException 字段校验失败
     */
    AgentTool updateAgentTool(Long id, AgentToolEditDto dto, String operator);
}
