package com.dkd.manage.domain;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体工具调用（agent_tool_call）实体。
 *
 * <p>为什么参数只存摘要：工具入参可能含手机号等 PII（AGENTS §7.8），
 * 摘要仅用于"两次调用是否同一参数"的对账，不做可读展示。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
public class AgentToolCall extends BaseEntity
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
     * 所属轮次
     */
    private Integer stepNo;

    /**
     * 轮内序号（从 1 开始）
     */
    private Integer callSeq;

    /**
     * 工具编码
     */
    private String toolCode;

    /**
     * 参数摘要（sha256 前 16 位，不存原文）
     */
    private String paramsDigest;

    /**
     * 返回行数
     */
    private Integer rowCount;

    /**
     * 耗时（毫秒）
     */
    private Integer durationMs;

    /**
     * 结果：ok / error / timeout / truncated
     */
    private String resultStatus;

    /**
     * 失败原因原文
     */
    private String errorMsg;

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

    public Integer getCallSeq()
    {
        return callSeq;
    }

    public void setCallSeq(Integer callSeq)
    {
        this.callSeq = callSeq;
    }

    public String getToolCode()
    {
        return toolCode;
    }

    public void setToolCode(String toolCode)
    {
        this.toolCode = toolCode;
    }

    public String getParamsDigest()
    {
        return paramsDigest;
    }

    public void setParamsDigest(String paramsDigest)
    {
        this.paramsDigest = paramsDigest;
    }

    public Integer getRowCount()
    {
        return rowCount;
    }

    public void setRowCount(Integer rowCount)
    {
        this.rowCount = rowCount;
    }

    public Integer getDurationMs()
    {
        return durationMs;
    }

    public void setDurationMs(Integer durationMs)
    {
        this.durationMs = durationMs;
    }

    public String getResultStatus()
    {
        return resultStatus;
    }

    public void setResultStatus(String resultStatus)
    {
        this.resultStatus = resultStatus;
    }

    public String getErrorMsg()
    {
        return errorMsg;
    }

    public void setErrorMsg(String errorMsg)
    {
        this.errorMsg = errorMsg;
    }
}
