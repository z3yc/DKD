package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体工具注册表对象 agent_tool（排期 3-4「工具中心」，DDL 见 docs/ddl/agent_tool_llm_profile.sql）。
 *
 * <p><b>为什么这个实体只允许改三个字段（enabled / timeoutMs / rowLimit）：</b>
 * 工具行的 {@code code / source / readOnly / builtin} 是"这个工具是什么、能不能被模型直调"
 * 的身份事实——{@code read_only=1} 才允许被 LLM 直接调用，{@code builtin} 表示随代码发布。
 * 如果后台能把 {@code read_only} 从 0 改成 1，等于用一次页面操作绕过了 AGENTS §7.9
 * 「写操作只能人工确认后回调 Java」这条红线。因此不可变字段在 Service 校验**并且**在
 * UPDATE SQL 里根本不出现（纵深防御：即使有人绕过 Service 直接调 Mapper 也改不了）。
 *
 * <p>本实体同时作为列表出参（字段与表列一一对应；没有需要额外聚合的内容）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentTool extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 工具编码（必须能映射到代码/MCP 工具名；不可修改） */
    private String code;

    /** 展示名称 */
    private String name;

    /** 能力说明（给运营看，不是给模型看） */
    private String description;

    /** 适用载体：chat/restock/fault/all */
    private String scope;

    /** 来源：local-代码内置 mcp-外部 MCP（不可修改） */
    private String source;

    /** 是否只读：1-只读（可被模型调用）0-写工具（必须拒绝直调；不可修改） */
    private Integer readOnly;

    /** 启用状态：0-停用（不注入模型） */
    private Integer enabled;

    /** 工具级超时（毫秒）100~30000 */
    private Integer timeoutMs;

    /** 工具级返回行数上限 1~5000 */
    private Integer rowLimit;

    /** 是否随代码发布：1-内置（不可删除；不可修改） */
    private Integer builtin;

    /** 删除标记：0-存在 1-删除 */
    private String delFlag;

    // 注：createBy/createTime/updateBy/updateTime 由 BaseEntity 提供，不要重复声明。

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

    public String getName()
    {
        return name;
    }

    public void setName(String name)
    {
        this.name = name;
    }

    public String getDescription()
    {
        return description;
    }

    public void setDescription(String description)
    {
        this.description = description;
    }

    public String getScope()
    {
        return scope;
    }

    public void setScope(String scope)
    {
        this.scope = scope;
    }

    public String getSource()
    {
        return source;
    }

    public void setSource(String source)
    {
        this.source = source;
    }

    public Integer getReadOnly()
    {
        return readOnly;
    }

    public void setReadOnly(Integer readOnly)
    {
        this.readOnly = readOnly;
    }

    public Integer getEnabled()
    {
        return enabled;
    }

    public void setEnabled(Integer enabled)
    {
        this.enabled = enabled;
    }

    public Integer getTimeoutMs()
    {
        return timeoutMs;
    }

    public void setTimeoutMs(Integer timeoutMs)
    {
        this.timeoutMs = timeoutMs;
    }

    public Integer getRowLimit()
    {
        return rowLimit;
    }

    public void setRowLimit(Integer rowLimit)
    {
        this.rowLimit = rowLimit;
    }

    public Integer getBuiltin()
    {
        return builtin;
    }

    public void setBuiltin(Integer builtin)
    {
        this.builtin = builtin;
    }

    public String getDelFlag()
    {
        return delFlag;
    }

    public void setDelFlag(String delFlag)
    {
        this.delFlag = delFlag;
    }

    @Override
    public String toString()
    {
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("code", getCode())
                .append("name", getName())
                .append("scope", getScope())
                .append("source", getSource())
                .append("readOnly", getReadOnly())
                .append("enabled", getEnabled())
                .append("timeoutMs", getTimeoutMs())
                .append("rowLimit", getRowLimit())
                .append("builtin", getBuiltin())
                .append("updateBy", getUpdateBy())
                .append("updateTime", getUpdateTime())
                .toString();
    }
}
