package com.dkd.manage.domain.dto;

/**
 * 智能体配置「版本回滚」入参（排期 2-2，契约 §4.5）。
 *
 * <p>回滚语义：把 {@code targetVersion} 对应的历史快照复制成 agent_config 的**新版本**
 * （newVersion = 当前 version + 1），而不是把 version 改回旧值——版本序列必须单调递增，
 * 否则「当前配置是哪一次保存」无法回答（契约 §3.2）。
 *
 * <p>{@code version} 必填：回滚也是写操作，同样要受乐观锁保护，防止把别人刚保存的配置覆盖掉。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigRollbackDto
{
    /** 要回滚到的历史版本号（必填；不存在时返回业务错误） */
    private Long targetVersion;

    /** 当前版本（必填；乐观锁依据，冲突返回 409） */
    private Long version;

    /** 变更说明（可选 ≤200 字；不传时 Service 生成「回滚至 vX」） */
    private String changeNote;

    public Long getTargetVersion()
    {
        return targetVersion;
    }

    public void setTargetVersion(Long targetVersion)
    {
        this.targetVersion = targetVersion;
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
