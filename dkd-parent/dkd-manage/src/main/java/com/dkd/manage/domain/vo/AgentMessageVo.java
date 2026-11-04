package com.dkd.manage.domain.vo;

import java.util.Date;

import com.fasterxml.jackson.annotation.JsonFormat;

/**
 * 会话时间线里的单条消息出参（Batch A · 排期 2-5）。
 *
 * <p>三个刻意设计：
 *
 * <ol>
 *   <li><b>正文截断到 2000 字并显式给 {@code truncated} + {@code contentLength}：</b>
 *       一条助手消息可能几千字，详情页一次性全传会让抽屉卡顿；但"截断了"必须被看见，
 *       否则运营会拿着半截内容去判断结论对不对。{@code contentLength} 是截断**前**的原始字数，
 *       前端可以写"原始 5321 字，展示前 2000 字"。</li>
 *   <li><b>{@code toolCalls} 是解析后的对象；解析失败不抛异常，而是给原文 + 错误标记：</b>
 *       {@code tool_calls} 是 Python 侧写入的历史 JSON，可能因为早期版本/人工修复而损坏。
 *       界面能显示"该条工具调用原始文本损坏"就够了，不能让整个会话详情 500——
 *       那等于一条脏数据把整段排障证据锁死。</li>
 *   <li><b>{@code toolCallsRaw} 只在解析失败时回填：</b>成功时再复制一份原文
 *       会让响应体积翻倍（工具参数里可能有大段 JSON）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMessageVo
{
    /** 主键 */
    private Long id;

    /** 会话内序号（从 1 递增） */
    private Integer seq;

    /** 角色：1-用户 2-助手 3-工具结果 4-系统（中文标签由前端映射） */
    private Integer msgRole;

    /** 正文（超过 2000 字时已截断） */
    private String content;

    /** 正文原始字数（截断前；null 正文为 0） */
    private Integer contentLength;

    /** 正文是否被截断 */
    private Boolean truncated;

    /** 工具调用明细（JSON 解析后的数组/对象；解析失败或没有时为 null） */
    private Object toolCalls;

    /** 工具调用原始文本（**仅解析失败时**回填，便于排障与人工修复） */
    private String toolCallsRaw;

    /** 工具调用解析失败标记（异常类型；正常时为 null） */
    private String toolCallsParseError;

    /** 本消息解析出的工具调用条数（数组长度；无调用为 0） */
    private Integer toolCallCount;

    /** 模型标识 */
    private String model;

    /**
     * 生成本条消息时使用的智能体配置版本（排期 4-2；NULL = 历史/旧数据）。
     *
     * <p>只做标注、不回写历史内容：配置回滚后旧消息仍显示生成时的那一版。
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

    /** 消息时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createTime;

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
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

    public Integer getContentLength()
    {
        return contentLength;
    }

    public void setContentLength(Integer contentLength)
    {
        this.contentLength = contentLength;
    }

    public Boolean getTruncated()
    {
        return truncated;
    }

    public void setTruncated(Boolean truncated)
    {
        this.truncated = truncated;
    }

    public Object getToolCalls()
    {
        return toolCalls;
    }

    public void setToolCalls(Object toolCalls)
    {
        this.toolCalls = toolCalls;
    }

    public String getToolCallsRaw()
    {
        return toolCallsRaw;
    }

    public void setToolCallsRaw(String toolCallsRaw)
    {
        this.toolCallsRaw = toolCallsRaw;
    }

    public String getToolCallsParseError()
    {
        return toolCallsParseError;
    }

    public void setToolCallsParseError(String toolCallsParseError)
    {
        this.toolCallsParseError = toolCallsParseError;
    }

    public Integer getToolCallCount()
    {
        return toolCallCount;
    }

    public void setToolCallCount(Integer toolCallCount)
    {
        this.toolCallCount = toolCallCount;
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

    public Date getCreateTime()
    {
        return createTime;
    }

    public void setCreateTime(Date createTime)
    {
        this.createTime = createTime;
    }
}
