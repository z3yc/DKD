package com.dkd.manage.service;

import java.util.List;

import com.dkd.manage.domain.AgentLlmProbe;
import com.dkd.manage.domain.AgentLlmProfile;
import com.dkd.manage.domain.dto.AgentLlmProfileDto;
import com.dkd.manage.domain.dto.AgentLlmProfileEnabledDto;

/**
 * 模型档案 Service（排期 3-5，原型 S14）。
 *
 * <p>契约要点：
 * <ul>
 *   <li>只接收/返回**环境变量名** {@code apiKeyEnv}，从不接收或返回密钥值（AGENTS §7.1）；</li>
 *   <li>{@code isDefault} 最多一个，由本层在同一事务里保证（MySQL 无部分唯一索引）；</li>
 *   <li>连通测试会写 {@code agent_llm_probe}，Python 不可达时**先落失败记录再抛业务错误**。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentLlmProfileService
{
    /**
     * 档案列表（默认档案置顶，可按 code 模糊 / enabled 精确过滤）。
     *
     * @param query 过滤条件（可 null）
     * @return 档案列表（不含任何密钥值——表里本来就没有）
     */
    List<AgentLlmProfile> selectAgentLlmProfileList(AgentLlmProfile query);

    /**
     * 新增档案（isDefault=1 时同事务把其它档案置 0）。
     *
     * @param dto      入参
     * @param operator 操作者
     * @return 落库后的档案
     */
    AgentLlmProfile insertAgentLlmProfile(AgentLlmProfileDto dto, String operator);

    /**
     * 编辑档案（编码只读；isDefault=1 时同事务把其它档案置 0）。
     *
     * @param dto      入参（必须带 id）
     * @param operator 操作者
     * @return 更新后的档案
     */
    AgentLlmProfile updateAgentLlmProfile(AgentLlmProfileDto dto, String operator);

    /**
     * 启用/停用档案。
     *
     * @param id       主键
     * @param dto      入参（enabled 0/1）
     * @param operator 操作者
     * @return 更新后的档案
     */
    AgentLlmProfile updateEnabled(Long id, AgentLlmProfileEnabledDto dto, String operator);

    /**
     * 连通性测试：调 Python {@code POST /agents/llm/ping}，结果写入 {@code agent_llm_probe} 后返回。
     *
     * <p>Python 可达但探测失败（缺环境变量/鉴权/超时）→ 返回失败记录（ok=0）而不抛业务异常，
     * 前端需要展示 httpStatus/errorType；Python **不可达** → 先落失败记录再抛业务错误。
     *
     * @param id        档案主键
     * @param requestId 全链路追踪 ID（透传上游并落库）
     * @param operator  操作者
     * @return 落库后的探测记录
     */
    AgentLlmProbe probe(Long id, String requestId, String operator);

    /**
     * 某档案的探测记录（倒序；分页由 Controller 的 startPage 负责）。
     *
     * @param profileId 档案主键（不存在 → 业务错误）
     * @return 探测记录列表
     */
    List<AgentLlmProbe> selectProbeList(Long profileId);

    /**
     * 按主键取档案（不存在 → 业务错误）。
     *
     * <p>为什么单独暴露：探测记录接口必须在 {@code startPage()} **之前**校验档案存在，
     * 否则 PageHelper 会把“校验查询”也拼上 LIMIT（其 SQL 自带 limit 1 → 语法错误）。
     *
     * @param id 主键
     * @return 档案
     */
    AgentLlmProfile selectAgentLlmProfileById(Long id);

    /**
     * 按档案 code 取探测记录（倒序；分页由 Controller 的 startPage 负责）。
     *
     * @param profileCode 档案编码（非空）
     * @return 探测记录列表
     */
    List<AgentLlmProbe> selectProbeListByCode(String profileCode);
}
