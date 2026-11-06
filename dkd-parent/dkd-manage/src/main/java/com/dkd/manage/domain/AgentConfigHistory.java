package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体配置版本快照对象 agent_config_history（排期 2-2，契约 §3.2/§4.5）。
 *
 * <p><b>只增不改</b>：本表是「当前配置是哪一次保存」的唯一证据，因此 Mapper 只提供
 * INSERT 与查询，没有任何 update/delete 方法；回滚也**不是**原地改历史行，
 * 而是把某条历史快照复制成 agent_config 的一个**新版本**（否则版本序列会出现断点）。
 *
 * <p>{@code snapshot} 是整行 JSON 的**原始文本**（键名为 snake_case，与 agent_config 列名一致，
 * {@code params_json} 内嵌为对象）。出参保持字符串而不是 Map：
 * 前端「快照查看」只需要 JSON.parse 后展示，Java 侧解析成对象反而要引入一套无用的嵌套 DTO。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigHistory extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 智能体编码（与 agent_config.code 相同；UK 的另一半是 version） */
    private String code;

    /** 被快照的版本号（= 当时 agent_config.version，含回滚产生的新版本） */
    private Long version;

    /** 整行快照 JSON（snake_case 键名；params_json 内嵌为对象） */
    private String snapshot;

    /** 该版本的变更说明（回滚来源也要写清） */
    private String changeNote;

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getCode()
    {
        return code;
    }

    public void setCode(String code)
    {
        this.code = code;
    }

    public Long getVersion()
    {
        return version;
    }

    public void setVersion(Long version)
    {
        this.version = version;
    }

    public String getSnapshot()
    {
        return snapshot;
    }

    public void setSnapshot(String snapshot)
    {
        this.snapshot = snapshot;
    }

    public String getChangeNote()
    {
        return changeNote;
    }

    public void setChangeNote(String changeNote)
    {
        this.changeNote = changeNote;
    }

    @Override
    public String toString()
    {
        // 不打印 snapshot（整行 JSON，含 8KB 提示词）
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("code", getCode())
                .append("version", getVersion())
                .append("changeNote", getChangeNote())
                .append("createBy", getCreateBy())
                .append("createTime", getCreateTime())
                .toString();
    }
}
