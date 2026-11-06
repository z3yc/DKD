package com.dkd.manage.domain.dto;

/**
 * 知识库检索测试入参（排期 3-6，S11「检索测试」）。
 *
 * <p>{@code requestId}/{@code conversationId} 由前端可空传入；requestId 若为空，
 * Controller 会用全链路追踪头自动补一个，保证 Python 侧的检索留痕能被 Java 日志对上。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentKbSearchDto
{
    /** 检索词（必填，≤500 字） */
    private String query;

    /** 返回条数（1~20；不传取 5） */
    private Integer topK;

    /** 场景码（可空；用于归因：诊断/问答等） */
    private Integer scene;

    /** 会话ID（可空；把"这次检索属于哪段会话"写进留痕） */
    private String conversationId;

    public String getQuery()
    {
        return query;
    }

    public void setQuery(String query)
    {
        this.query = query;
    }

    public Integer getTopK()
    {
        return topK;
    }

    public void setTopK(Integer topK)
    {
        this.topK = topK;
    }

    public Integer getScene()
    {
        return scene;
    }

    public void setScene(Integer scene)
    {
        this.scene = scene;
    }

    public String getConversationId()
    {
        return conversationId;
    }

    public void setConversationId(String conversationId)
    {
        this.conversationId = conversationId;
    }
}
