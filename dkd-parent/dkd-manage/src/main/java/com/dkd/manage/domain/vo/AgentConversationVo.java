package com.dkd.manage.domain.vo;

import java.util.Date;

import com.fasterxml.jackson.annotation.JsonFormat;

/**
 * 会话列表/会话头出参（Batch A · 排期 2-5）。
 *
 * <p>字段 = {@code agent_conversation} 的表列 + {@code agent_message} 的聚合列。
 * <b>为什么同时给 {@code messageCount} 与 {@code actualMessageCount}：</b>
 * {@code messageCount} 是 Python 侧维护的冗余计数（用于列表快速展示、避免 N+1），
 * {@code actualMessageCount} 是本次按 {@code agent_message} 实测的条数。
 * 两者不一致说明写入侧漏了更新——这正是运营排障时最需要看到的东西，
 * 只给一个数就永远发现不了"列表说 12 条、点进去只有 9 条"的漂移。
 *
 * <p>{@code toolCallsCount} 的口径是"**tool_calls 非空的消息条数**"（不是工具调用总次数）：
 * 一条助手消息可能调用多个工具，但列表页只关心"这条会话有没有实际查过数据"，
 * 逐条解析 JSON 计数会把列表变成 N 次解析（且损坏的 JSON 还会让列表失败）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentConversationVo
{
    /** 主键 */
    private Long id;

    /** 业务会话ID（= LangGraph thread_id） */
    private String conversationId;

    /** 发起用户ID */
    private Long userId;

    /** 发起用户名称 */
    private String userName;

    /** 用户所属区域 */
    private Long regionId;

    /** 场景：1-通用问答 2-补货 3-诊断 4-运营分析（≥101 自定义） */
    private Integer scene;

    /** 会话标题 */
    private String title;

    /** 状态：1-进行中 2-已结束 3-已归档 */
    private Integer status;

    /** 表内冗余的消息条数（agent_conversation.message_count） */
    private Integer messageCount;

    /** agent_message 实测消息条数（无消息时为 0） */
    private Integer actualMessageCount;

    /** tool_calls 非空的消息条数（无消息时为 0） */
    private Integer toolCallsCount;

    /** 输入 token 合计（无消息时为 0） */
    private Long tokensIn;

    /** 输出 token 合计（无消息时为 0） */
    private Long tokensOut;

    /** 最后一条消息时间（无消息时为 null） */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date lastMessageTime;

    /** 会话创建时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createTime;

    /** 会话更新时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date updateTime;

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

    public Integer getActualMessageCount()
    {
        return actualMessageCount;
    }

    public void setActualMessageCount(Integer actualMessageCount)
    {
        this.actualMessageCount = actualMessageCount;
    }

    public Integer getToolCallsCount()
    {
        return toolCallsCount;
    }

    public void setToolCallsCount(Integer toolCallsCount)
    {
        this.toolCallsCount = toolCallsCount;
    }

    public Long getTokensIn()
    {
        return tokensIn;
    }

    public void setTokensIn(Long tokensIn)
    {
        this.tokensIn = tokensIn;
    }

    public Long getTokensOut()
    {
        return tokensOut;
    }

    public void setTokensOut(Long tokensOut)
    {
        this.tokensOut = tokensOut;
    }

    public Date getLastMessageTime()
    {
        return lastMessageTime;
    }

    public void setLastMessageTime(Date lastMessageTime)
    {
        this.lastMessageTime = lastMessageTime;
    }

    public Date getCreateTime()
    {
        return createTime;
    }

    public void setCreateTime(Date createTime)
    {
        this.createTime = createTime;
    }

    public Date getUpdateTime()
    {
        return updateTime;
    }

    public void setUpdateTime(Date updateTime)
    {
        this.updateTime = updateTime;
    }
}
