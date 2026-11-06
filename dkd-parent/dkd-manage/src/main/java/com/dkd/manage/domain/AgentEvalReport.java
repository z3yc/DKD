package com.dkd.manage.domain;

import java.math.BigDecimal;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体配置版本评测报告对象 agent_eval_report（排期 5-1，DDL 见 docs/ddl/agent_eval_flag.sql）。
 *
 * <p><b>只增不改：</b>评测是"这一版配置当时测得怎么样"的证据；后台若能把报告改漂亮，
 * 它就不再能作为保存护栏的依据。因此没有 update/delete，清理属保留策略（后续排期）。
 *
 * <p><b>accuracy 的语义：</b>只有 {@code status=scored} 才有值（0~1）；{@code blocked}
 * （无 verified 样本，不能算准确率）与 {@code failed}（跑挂）必须为 NULL——
 * 把"没测出来"写成 0 会让护栏永远拦人，把"跑挂"写成 100 会放行坏配置。
 *
 * <p>{@code baselineAccuracy/baselineN} 来自评测集 meta.baseline；未设置则 NULL（不伪造基线）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentEvalReport extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 智能体配置编码（agent_config.code） */
    private String configCode;

    /** 被评测的配置版本 */
    private Long configVersion;

    /** 评测场景（null = 全场景汇总） */
    private Integer scene;

    /** 评测模式：live=真实 LLM / offline=结构校验（不调 LLM） */
    private String mode;

    /** 状态：scored=已算准确率 / blocked=无样本 / failed=运行失败 */
    private String status;

    /** 准确率（仅 scored；0~1；其他状态为 null） */
    private BigDecimal accuracy;

    /** 参与用例数 */
    private Integer total;

    /** 通过数 */
    private Integer passed;

    /** 失败数 */
    private Integer failed;

    /** 未评分/阻塞数 */
    private Integer blocked;

    /** 评测集基线准确率（null = 未设基线） */
    private BigDecimal baselineAccuracy;

    /** 评测集基线样本量（null = 未设置） */
    private Integer baselineN;

    /** 完整报告 JSON（只落库、不写日志；不含密钥） */
    private String reportJson;

    /** 执行人/CI */
    private String runBy;

    // 注：createTime 由 BaseEntity 提供（带 @JsonFormat），不要重复声明。

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

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

    @Override
    public String toString()
    {
        // 不打印 reportJson 正文（可能含用例原文/用户问题；AGENTS §6.1 日志最小化）
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("configCode", getConfigCode())
                .append("configVersion", getConfigVersion())
                .append("scene", getScene())
                .append("mode", getMode())
                .append("status", getStatus())
                .append("accuracy", getAccuracy())
                .append("total", getTotal())
                .append("passed", getPassed())
                .append("failed", getFailed())
                .append("blocked", getBlocked())
                .append("baselineAccuracy", getBaselineAccuracy())
                .append("baselineN", getBaselineN())
                .append("reportJsonLength", getReportJson() == null ? 0 : getReportJson().length())
                .append("runBy", getRunBy())
                .append("createTime", getCreateTime())
                .toString();
    }
}
