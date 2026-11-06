package com.dkd.manage.domain.dto;

/**
 * 智能体配置「启用/停用」入参（排期 2-2，契约 §4.5）。
 *
 * <p>为什么单独一个 DTO 而不是复用 {@link AgentConfigDto}：启停只允许改 enabled/disabledReason，
 * 复用编辑 DTO 会把提示词、工具、模型等字段一并暴露成可改；而「启停」按钮不应该能改配置内容。
 *
 * <p>{@code version} 由前端带上当前版本做乐观锁（冲突返回 409）；漏传时 Service 会读取库内
 * 当前版本兜底——那只能防住「读取之后、更新之前」的并发，故前端应始终传。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigEnabledDto
{
    /** 目标状态：1-启用 0-停用（必填） */
    private Integer enabled;

    /** 停用原因（enabled=0 时必填 5~100 字；enabled=1 时忽略并清空） */
    private String disabledReason;

    /** 当前版本（乐观锁；建议必传） */
    private Long version;

    /** 本次变更说明（可选 ≤200 字；不传时 Service 生成「启用/停用」） */
    private String changeNote;

    public Integer getEnabled()
    {
        return enabled;
    }

    public void setEnabled(Integer enabled)
    {
        this.enabled = enabled;
    }

    public String getDisabledReason()
    {
        return disabledReason;
    }

    public void setDisabledReason(String disabledReason)
    {
        this.disabledReason = disabledReason;
    }

    public Long getVersion()
    {
        return version;
    }

    public void setVersion(Long version)
    {
        this.version = version;
    }

    public String getChangeNote()
    {
        return changeNote;
    }

    public void setChangeNote(String changeNote)
    {
        this.changeNote = changeNote;
    }
}
