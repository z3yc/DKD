package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体消息明细对象 agent_message（Batch A · 排期 2-5，DDL 见 docs/ddl/agent_tables.sql）。
 *
 * <p><b>{@code toolCalls} 为什么在实体里是 String、在出参里却是对象：</b>
 * MySQL 的 JSON 列经 JDBC 返回的就是文本（本仓库既有 JSON 列 {@code tb_report.top_products}
 * 也是这么映射的）。实体保持"原样文本"是为了不在 Mapper 层引入新的 TypeHandler 链；
 * 解析成对象并做**容错**是 Service 的事（{@code AgentConversationServiceImpl}）——
 * 一条损坏的历史 JSON 不能让整个会话详情接口 500。
 *
 * <p>写入方只有智能体服务（Python 侧 {@code app/audit.py::record_message}），管理端只读。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMessage extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 会话ID（逻辑关联 agent_conversation.conversation_id） */
    private String conversationId;

    /** 发起用户ID（定时任务为 null） */
    private Long userId;

    /** 会话内序号，从 1 递增（同会话唯一） */
    private Integer seq;

    /** 角色：1-用户 2-助手 3-工具结果 4-系统 */
    private Integer msgRole;

    /** 消息正文（可能很长，Service 按 2000 字截断后再出参） */
    private String content;

    /** 工具调用明细（JSON 原文；解析容错见 Service） */
    private String toolCalls;

    /** 模型标识（如 deepseek-chat） */
    private String model;

    /**
     * 生成本条消息时使用的智能体配置版本（排期 4-2，可空）。
     *
     * <p>NULL = 历史/旧数据（DDL 加列前的行），出参保持 null；**不编造版本号**。
     */
    private Long configVersion;

    /** 输入 token 数 */
    private Integer tokensIn;

    /** 输出 token 数 */
    private Integer tokensOut;

    /** 本消息耗时（毫秒） */
    private Integer latencyMs;

    /** 全链路追踪ID（与日志页互通互跳的依据） */
    private String requestId;

    /** 删除标记：0-存在 1-删除 */
    private String delFlag;

    // 注：createTime 由 BaseEntity 提供（带 @JsonFormat），不要重复声明。

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

    public Integer getSeq()
    {
        return seq;
    }

    public void setSeq(Integer seq)
    {
        this.seq = seq;
    }

    public Integer getMsgRole()
    {
        return msgRole;
    }

    public void setMsgRole(Integer msgRole)
    {
        this.msgRole = msgRole;
    }

    public String getContent()
    {
        return content;
    }

    public void setContent(String content)
    {
        this.content = content;
    }

    public String getToolCalls()
    {
        return toolCalls;
    }

    public void setToolCalls(String toolCalls)
    {
        this.toolCalls = toolCalls;
    }

    public String getModel()
    {
        return model;
    }

    public void setModel(String model)
    {
        this.model = model;
    }

    public Long getConfigVersion()
    {
        return configVersion;
    }

    public void setConfigVersion(Long configVersion)
    {
        this.configVersion = configVersion;
    }

    public Integer getTokensIn()
    {
        return tokensIn;
    }

    public void setTokensIn(Integer tokensIn)
    {
        this.tokensIn = tokensIn;
    }

    public Integer getTokensOut()
    {
        return tokensOut;
    }

    public void setTokensOut(Integer tokensOut)
    {
        this.tokensOut = tokensOut;
    }

    public Integer getLatencyMs()
    {
        return latencyMs;
    }

    public void setLatencyMs(Integer latencyMs)
    {
        this.latencyMs = latencyMs;
    }

    public String getRequestId()
    {
        return requestId;
    }

    public void setRequestId(String requestId)
    {
        this.requestId = requestId;
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
                .append("seq", getSeq())
                .append("msgRole", getMsgRole())
                // 正文与 tool_calls 可能含用户数据，toString 只记长度，不落内容（AGENTS §6.1）
                .append("contentLength", getContent() == null ? 0 : getContent().length())
                .append("toolCallsLength", getToolCalls() == null ? 0 : getToolCalls().length())
                .append("model", getModel())
                .append("tokensIn", getTokensIn())
                .append("tokensOut", getTokensOut())
                .append("latencyMs", getLatencyMs())
                .append("requestId", getRequestId())
                .append("createTime", getCreateTime())
                .toString();
    }
}
