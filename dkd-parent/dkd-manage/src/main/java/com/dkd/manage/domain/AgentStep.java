package com.dkd.manage.domain;

import java.util.List;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体运行轮次（agent_step）实体。
 *
 * <p>`toolCalls` 不是表字段：由 Service 按 `stepNo` 把同一次运行的工具调用挂上来，
 * 供页面在一个时间线节点里展开（避免前端为每一轮再发一次请求）。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
public class AgentStep extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /**
     * 主键
     */
    private Long id;

    /**
     * 所属运行ID
     */
    private String runId;

    /**
     * 轮次序号（从 1 开始）
     */
    private Integer stepNo;

    /**
     * 角色编码：diagnostician / dispatcher / reviewer / supervisor
     */
    private String roleCode;

    /**
     * 动作标识：diagnose / dispatch_propose / dispatch_confirm / review / finish
     */
    private String action;

    /**
     * 本轮一句话结论（页面直接展示）
     */
    private String summary;

    /**
     * 证据/提案 JSON（含 evidenceId → tool_call 映射）
     */
    private String evidenceJson;

    /**
     * 本轮输入 token
     */
    private Integer inputTokens;

    /**
     * 本轮输出 token
     */
    private Integer outputTokens;

    /**
     * 本轮耗时（毫秒）
     */
    private Integer durationMs;

    /**
     * 状态：ok / error / timeout / skipped
     */
    private String status;

    /**
     * 失败原因原文
     */
    private String errorMsg;

    /**
     * 本轮工具调用（非表字段，Service 组装）
     */
    private List<AgentToolCall> toolCalls;

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getRunId()
    {
        return runId;
    }

    public void setRunId(String runId)
    {
        this.runId = runId;
    }

    public Integer getStepNo()
    {
        return stepNo;
    }

    public void setStepNo(Integer stepNo)
    {
        this.stepNo = stepNo;
    }

    public String getRoleCode()
    {
        return roleCode;
    }

    public void setRoleCode(String roleCode)
    {
        this.roleCode = roleCode;
    }

    public String getAction()
    {
        return action;
    }

    public void setAction(String action)
    {
        this.action = action;
    }

    public String getSummary()
    {
        return summary;
    }

    public void setSummary(String summary)
    {
        this.summary = summary;
    }

    public String getEvidenceJson()
    {
        return evidenceJson;
    }

    public void setEvidenceJson(String evidenceJson)
    {
        this.evidenceJson = evidenceJson;
    }

    public Integer getInputTokens()
    {
        return inputTokens;
    }

    public void setInputTokens(Integer inputTokens)
    {
        this.inputTokens = inputTokens;
    }

    public Integer getOutputTokens()
    {
        return outputTokens;
    }

    public void setOutputTokens(Integer outputTokens)
    {
        this.outputTokens = outputTokens;
    }

    public Integer getDurationMs()
    {
        return durationMs;
    }

    public void setDurationMs(Integer durationMs)
    {
        this.durationMs = durationMs;
    }

    public String getStatus()
    {
        return status;
    }

    public void setStatus(String status)
    {
        this.status = status;
    }

    public String getErrorMsg()
    {
        return errorMsg;
    }

    public void setErrorMsg(String errorMsg)
    {
        this.errorMsg = errorMsg;
    }

    public List<AgentToolCall> getToolCalls()
    {
        return toolCalls;
    }

    public void setToolCalls(List<AgentToolCall> toolCalls)
    {
        this.toolCalls = toolCalls;
    }
}
