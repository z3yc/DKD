package com.dkd.manage.service;

import java.util.List;

import com.dkd.manage.domain.vo.AgentMcpServerVo;
import com.dkd.manage.domain.dto.AgentMcpServerDto;
import com.dkd.manage.domain.dto.AgentMcpServerEnabledDto;

/**
 * MCP server 配置 Service（排期 3-7，原型 S12）。
 *
 * <p>职责切分：**配置**的增改由本层负责（RuoYi 权限 + 审计）；**探测/同步/工具清单**由
 * Python 负责，本层只按 server code 代理调用（服务间密钥），不直接读写 agent_mcp_tool/probe 表。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentMcpServerService
{
    /**
     * 配置列表（含 Python 回写的 status/lastProbe*；envKeys 解析为变量名映射）。
     *
     * @return 配置列表
     */
    List<AgentMcpServerVo> selectServerList();

    /**
     * 配置详情。
     *
     * @param id 主键
     * @return 配置
     */
    AgentMcpServerVo selectServerById(Long id);

    /**
     * 新增配置（enabled 默认 0；transport 本期仅 http；envKeys 只收环境变量名）。
     *
     * @param dto      入参
     * @param operator 操作者
     * @return 落库后的配置
     */
    AgentMcpServerVo insertServer(AgentMcpServerDto dto, String operator);

    /**
     * 编辑配置（code 只读；enabled 也可在此更新，但推荐走独立启停接口）。
     *
     * @param dto      入参（必须带 id）
     * @param operator 操作者
     * @return 更新后的配置
     */
    AgentMcpServerVo updateServer(AgentMcpServerDto dto, String operator);

    /**
     * 启用/停用配置。
     *
     * @param id       主键
     * @param dto      入参（enabled 0/1）
     * @param operator 操作者
     * @return 更新后的配置
     */
    AgentMcpServerVo updateEnabled(Long id, AgentMcpServerEnabledDto dto, String operator);

    /**
     * 代理 Python 探测（会同步只读工具并回写 status/lastProbe*）。
     *
     * @param id        配置主键
     * @param requestId 全链路追踪 ID
     * @return Python 信封 data
     */
    Object probe(Long id, String requestId);

    /**
     * 代理 Python 同步（只刷新工具清单）。
     *
     * @param id        配置主键
     * @param requestId 全链路追踪 ID
     * @return Python 信封 data
     */
    Object sync(Long id, String requestId);

    /**
     * 代理 Python 已同步工具清单。
     *
     * @param id        配置主键
     * @param requestId 全链路追踪 ID
     * @return Python 信封 data
     */
    Object tools(Long id, String requestId);
}
