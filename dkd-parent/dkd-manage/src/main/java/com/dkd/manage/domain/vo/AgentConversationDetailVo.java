package com.dkd.manage.domain.vo;

import java.util.ArrayList;
import java.util.List;

/**
 * 会话详情出参：会话头 + 消息时间线（Batch A · 排期 2-5）。
 *
 * <p><b>为什么带 {@code messageLimit} / {@code messagesTruncated}：</b>
 * 一个长会话可能有上千条消息，详情接口不能无界返回（单主库 + 抽屉渲染都扛不住）。
 * 截断本身可以接受，但**必须让页面知道"看到的不全"**——否则运营会把
 * "只看到前 500 条"当成"这个会话就这么多"，据此下结论。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentConversationDetailVo
{
    /** 会话头（含消息/token 聚合）；会话不存在时为 null（由 Controller 转业务错误） */
    private AgentConversationVo conversation;

    /** 消息时间线（按 seq 升序，最多 {@link #messageLimit} 条） */
    private List<AgentMessageVo> messages = new ArrayList<AgentMessageVo>();

    /** 本次返回的消息条数上限（服务端常量，供前端提示"仅前 N 条"） */
    private Integer messageLimit;

    /** 实际消息条数是否超过上限（true 表示时间线被截断） */
    private Boolean messagesTruncated;

    public AgentConversationVo getConversation()
    {
        return conversation;
    }

    public void setConversation(AgentConversationVo conversation)
    {
        this.conversation = conversation;
    }

    public List<AgentMessageVo> getMessages()
    {
        return messages;
    }

    public void setMessages(List<AgentMessageVo> messages)
    {
        this.messages = messages;
    }

    public Integer getMessageLimit()
    {
        return messageLimit;
    }

    public void setMessageLimit(Integer messageLimit)
    {
        this.messageLimit = messageLimit;
    }

    public Boolean getMessagesTruncated()
    {
        return messagesTruncated;
    }

    public void setMessagesTruncated(Boolean messagesTruncated)
    {
        this.messagesTruncated = messagesTruncated;
    }
}
