package com.dkd.manage.domain.dto;

import java.math.BigDecimal;

/**
 * 评测报告上报入参（排期 5-1，评测脚本/CI 调用）。
 *
 * <p>本 DTO 里没有、也不该有任何密钥字段：报告正文（reportJson）由评测脚本生成，
 * 规范要求不含密钥/完整 prompt（AGENTS §7.1/§6.1）；Java 侧只做结构校验与落库，不解析正文。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentEvalReportDto
{
    /** 配置编码（agent_config.code，必填） */
    private String configCode;

    /** 被评测的配置版本（必填，≥1） */
    private Long configVersion;

    /** 评测场景（null = 全场景汇总） */
    private Integer scene;

    /** 评测模式：live / offline（必填） */
    private String mode;

    /** 状态：scored / blocked / failed（必填） */
    private String status;

    /** 准确率（仅 scored 必填；0~1） */
    private BigDecimal accuracy;

    /** 参与用例数 */
    private Integer total;

    /** 通过数 */
    private Integer passed;

    /** 失败数 */
    private Integer failed;

    /** 未评分/阻塞数 */
    private Integer blocked;

    /** 基线准确率（null = 未设基线） */
    private BigDecimal baselineAccuracy;

    /** 基线样本量（null = 未设置） */
    private Integer baselineN;

    /** 完整报告 JSON（必填；≤1MB） */
    private String reportJson;

    /** 执行人/CI（可空；空则取当前登录人） */
    private String runBy;

    public String getConfigCode()
    {
        return configCode;
    }

    public void setConfigCode(String configCode)
    {
        this.configCode = configCode;
    }

    public Long getConfigVersion()
    {
        return configVersion;
    }

    public void setConfigVersion(Long configVersion)
    {
        this.configVersion = configVersion;
    }

    public Integer getScene()
    {
        return scene;
    }

    public void setScene(Integer scene)
    {
        this.scene = scene;
    }

    public String getMode()
    {
        return mode;
    }

    public void setMode(String mode)
    {
        this.mode = mode;
    }

    public String getStatus()
    {
        return status;
    }

    public void setStatus(String status)
    {
        this.status = status;
    }

    public BigDecimal getAccuracy()
    {
        return accuracy;
    }

    public void setAccuracy(BigDecimal accuracy)
    {
        this.accuracy = accuracy;
    }

    public Integer getTotal()
    {
        return total;
    }

    public void setTotal(Integer total)
    {
        this.total = total;
    }

    public Integer getPassed()
    {
        return passed;
    }

    public void setPassed(Integer passed)
    {
        this.passed = passed;
    }

    public Integer getFailed()
    {
        return failed;
    }

    public void setFailed(Integer failed)
    {
        this.failed = failed;
    }

    public Integer getBlocked()
    {
        return blocked;
    }

    public void setBlocked(Integer blocked)
    {
        this.blocked = blocked;
    }

    public BigDecimal getBaselineAccuracy()
    {
        return baselineAccuracy;
    }

    public void setBaselineAccuracy(BigDecimal baselineAccuracy)
    {
        this.baselineAccuracy = baselineAccuracy;
    }

    public Integer getBaselineN()
    {
        return baselineN;
    }

    public void setBaselineN(Integer baselineN)
    {
        this.baselineN = baselineN;
    }

    public String getReportJson()
    {
        return reportJson;
    }

    public void setReportJson(String reportJson)
    {
        this.reportJson = reportJson;
    }

    public String getRunBy()
    {
        return runBy;
    }

    public void setRunBy(String runBy)
    {
        this.runBy = runBy;
    }
}
