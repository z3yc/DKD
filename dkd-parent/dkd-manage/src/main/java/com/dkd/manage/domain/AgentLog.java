package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

/**
 * 智能体执行日志对象 agent_log（Batch A · 排期 2-4，DDL 见 docs/ddl/agent_config.sql）。
 *
 * <p><b>为什么这张表只读、且实体没有任何 insert/update/delete 方法：</b>
 * 日志是排障证据（"某一时刻哪一行 WARN 说了什么"），写入方只有智能体服务
 * （Python 侧 logging Handler 异步批量写）。后台一旦能改/删日志，它就不再能回答
 * "当时到底报了什么错"——与 {@code agent_decision_log} 同一条纪律（AGENTS §6.3）。
 * 保留期清理属排期 4-4，由 Java/运维执行，也不在本类开入口。
 *
 * <p><b>为什么没有 delFlag：</b>{@code agent_log} 的 DDL 只有 {@code create_by} 一个审计列，
 * 没有 {@code del_flag}/{@code update_* } —— 它是"只增不改"的流水，逻辑删除不适用；
 * 查询因此不做 del_flag 过滤（若将来加了软删列，必须同步改 Mapper 与本注释）。
 *
 * <p>与 DDL 的字段一一对应；{@code beginTime}/{@code endTime}/{@code keyword} 是查询条件，
 * 不是表列（由 {@link AgentTimeRangeQuery} 与下方 keyword 提供）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentLog extends AgentTimeRangeQuery
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 级别：DEBUG/INFO/WARNING/ERROR/CRITICAL */
    private String level;

    /** logger 名称（如 dkd.agent.chat） */
    private String logger;

    /** 关联运行ID（agent_run.run_id，可空） */
    private String runId;

    /** 关联会话ID（可空） */
    private String conversationId;

    /** 关联工单ID（可空） */
    private String taskId;

    /** 场景：chat/restock/fault（可空） */
    private String scene;

    /** 图节点/阶段（可空） */
    private String node;

    /** 状态标记：ok/error/timeout/skipped（可空） */
    private String status;

    /** 日志正文（Python 侧已脱敏） */
    private String message;

    /** 错误摘要（异常类型+消息，已脱敏） */
    private String errorMsg;

    /** 全链路追踪ID */
    private String requestId;

    /** 耗时（毫秒，可空） */
    private Integer durationMs;

    /** 查询条件：关键词（模糊匹配 message / error_msg；不是表列） */
    private String keyword;

    // 注：createTime / createBy 由 BaseEntity 提供（同样带 @JsonFormat），不要重复声明——
    // 重复声明会产生两个同名字段/getter，MyBatis 映射与 JSON 序列化都会出现“哪个生效”的歧义。

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getLevel()
    {
        return level;
    }

    public void setLevel(String level)
    {
        this.level = level;
    }

    public String getLogger()
    {
        return logger;
    }

    public void setLogger(String logger)
    {
        this.logger = logger;
    }

    public String getRunId()
    {
        return runId;
    }

    public void setRunId(String runId)
    {
        this.runId = runId;
    }

    public String getConversationId()
    {
        return conversationId;
    }

    public void setConversationId(String conversationId)
    {
        this.conversationId = conversationId;
    }

    public String getTaskId()
    {
        return taskId;
    }

    public void setTaskId(String taskId)
    {
        this.taskId = taskId;
    }

    public String getScene()
    {
        return scene;
    }

    public void setScene(String scene)
    {
        this.scene = scene;
    }

    public String getNode()
    {
        return node;
    }

    public void setNode(String node)
    {
        this.node = node;
    }

    public String getStatus()
    {
        return status;
    }

    public void setStatus(String status)
    {
        this.status = status;
    }

    public String getMessage()
    {
        return message;
    }

    public void setMessage(String message)
    {
        this.message = message;
    }

    public String getErrorMsg()
    {
        return errorMsg;
    }

    public void setErrorMsg(String errorMsg)
    {
        this.errorMsg = errorMsg;
    }

    public String getRequestId()
    {
        return requestId;
    }

    public void setRequestId(String requestId)
    {
        this.requestId = requestId;
    }

    public Integer getDurationMs()
    {
        return durationMs;
    }

    public void setDurationMs(Integer durationMs)
    {
        this.durationMs = durationMs;
    }

    public String getKeyword()
    {
        return keyword;
    }

    public void setKeyword(String keyword)
    {
        this.keyword = keyword;
    }

    @Override
    public String toString()
    {
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("createTime", getCreateTime())
                .append("level", getLevel())
                .append("logger", getLogger())
                .append("runId", getRunId())
                .append("conversationId", getConversationId())
                .append("taskId", getTaskId())
                .append("scene", getScene())
                .append("node", getNode())
                .append("status", getStatus())
                .append("message", getMessage())
                .append("errorMsg", getErrorMsg())
                .append("requestId", getRequestId())
                .append("durationMs", getDurationMs())
                .append("createBy", getCreateBy())
                .toString();
    }
}
