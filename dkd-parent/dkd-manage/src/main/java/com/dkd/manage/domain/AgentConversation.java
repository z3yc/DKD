package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

/**
 * 智能体会话元数据对象 agent_conversation（Batch A · 排期 2-5，DDL 见 docs/ddl/agent_tables.sql）。
 *
 * <p><b>为什么本实体只作为"查询条件"使用、列表结果由 {@code AgentConversationVo} 承载：</b>
 * 会话列表要带上 {@code agent_message} 的聚合（实测消息条数/工具调用条数/token 合计/最后消息时间），
 * 这些不是 {@code agent_conversation} 的列；用一个 VO 承接"表列 + 聚合列"，
 * 比在实体上挂一堆非表字段更不容易误导后来者（实体与 DDL 保持一一对应）。
 *
 * <p>写入方只有智能体服务（Python 侧），管理端只读：会话是审计与成本计量的依据，
 * 后台提供编辑入口会让"谁在什么时候聊了什么"失去可信度（AGENTS §7.3 同一口径）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentConversation extends AgentTimeRangeQuery
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 业务会话ID（= LangGraph thread_id，UUID） */
    private String conversationId;

    /** 发起用户ID（来自网关注入的 X-Agent-User；定时任务可能为空） */
    private Long userId;

    /** 发起用户名称（冗余列，便于列表展示） */
    private String userName;

    /** 用户所属区域（网关注入，用于权限对齐） */
    private Long regionId;

    /** 场景：1-通用问答 2-补货 3-诊断 4-运营分析（≥101 为自定义） */
    private Integer scene;

    /** 会话标题（首条用户消息摘要） */
    private String title;

    /** 状态：1-进行中 2-已结束 3-已归档 */
    private Integer status;

    /** 消息条数（表内冗余计数；与聚合出的实测条数可能漂移，列表两个都给） */
    private Integer messageCount;

    /** 删除标记：0-存在 1-删除 */
    private String delFlag;

    // 注：createTime / updateTime 由 BaseEntity 提供（带 @JsonFormat），不要重复声明。

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getConversationId()
    {
        return conversationId;
    }

    public void setConversationId(String conversationId)
    {
        this.conversationId = conversationId;
    }

    public Long getUserId()
    {
        return userId;
    }

    public void setUserId(Long userId)
    {
        this.userId = userId;
    }

    public String getUserName()
    {
        return userName;
    }

    public void setUserName(String userName)
    {
        this.userName = userName;
    }

    public Long getRegionId()
    {
        return regionId;
    }

    public void setRegionId(Long regionId)
    {
        this.regionId = regionId;
    }

    public Integer getScene()
    {
        return scene;
    }

    public void setScene(Integer scene)
    {
        this.scene = scene;
    }

    public String getTitle()
    {
        return title;
    }

    public void setTitle(String title)
    {
        this.title = title;
    }

    public Integer getStatus()
    {
        return status;
    }

    public void setStatus(Integer status)
    {
        this.status = status;
    }

    public Integer getMessageCount()
    {
        return messageCount;
    }

    public void setMessageCount(Integer messageCount)
    {
        this.messageCount = messageCount;
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
                .append("conversationId", getConversationId())
                .append("userId", getUserId())
                .append("userName", getUserName())
                .append("regionId", getRegionId())
                .append("scene", getScene())
                .append("title", getTitle())
                .append("status", getStatus())
                .append("messageCount", getMessageCount())
                .append("delFlag", getDelFlag())
                .append("createTime", getCreateTime())
                .append("updateTime", getUpdateTime())
                .toString();
    }
}
