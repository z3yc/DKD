package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 模型连通性探测记录对象 agent_llm_probe（排期 3-5，DDL 见 docs/ddl/agent_tool_llm_profile.sql）。
 *
 * <p><b>为什么只存"结论"而不存响应正文/密钥：</b>探测记录是给运营看"上次点测试连通是什么结果"的，
 * 它不是调试日志——LLM 响应正文可能含用户数据，错误消息里也可能拼进 Authorization 片段。
 * 因此只落 {@code ok/httpStatus/latencyMs/modelServed/errorType/errorMsg(≤500 脱敏截断)}，
 * 原始报文一律不进库、不进日志（AGENTS §6.1/§7.1）。
 *
 * <p>只增不改：本实体没有 update/delete 方法，Mapper 也只有 insert + select。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentLlmProbe extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 模型档案编码（agent_llm_profile.code；不存 id，档案删除后记录仍可读） */
    private String profileCode;

    /** 探测结论：1-成功 0-失败 */
    private Integer ok;

    /** HTTP 状态码（拿不到则 null） */
    private Integer httpStatus;

    /** 端到端延迟（毫秒） */
    private Integer latencyMs;

    /** 服务端实际返回的模型名（如 deepseek-flash；可能为 null） */
    private String modelServed;

    /** 失败异常类型（不落堆栈） */
    private String errorType;

    /** 失败摘要（脱敏截断 ≤500） */
    private String errorMsg;

    /** 全链路追踪ID */
    private String requestId;

    // 注：createBy/createTime 由 BaseEntity 提供（create_time 在库里是 DATETIME(3)，
    // BaseEntity 的 @JsonFormat 只到秒——探测记录列表按 create_time 排序即可，
    // 秒级展示足够；不重复声明 createTime 以避免 MyBatis/Jackson 的同名歧义）。

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getProfileCode()
    {
        return profileCode;
    }

    public void setProfileCode(String profileCode)
    {
        this.profileCode = profileCode;
    }

    public Integer getOk()
    {
        return ok;
    }

    public void setOk(Integer ok)
    {
        this.ok = ok;
    }

    public Integer getHttpStatus()
    {
        return httpStatus;
    }

    public void setHttpStatus(Integer httpStatus)
    {
        this.httpStatus = httpStatus;
    }

    public Integer getLatencyMs()
    {
        return latencyMs;
    }

    public void setLatencyMs(Integer latencyMs)
    {
        this.latencyMs = latencyMs;
    }

    public String getModelServed()
    {
        return modelServed;
    }

    public void setModelServed(String modelServed)
    {
        this.modelServed = modelServed;
    }

    public String getErrorType()
    {
        return errorType;
    }

    public void setErrorType(String errorType)
    {
        this.errorType = errorType;
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

    @Override
    public String toString()
    {
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("profileCode", getProfileCode())
                .append("ok", getOk())
                .append("httpStatus", getHttpStatus())
                .append("latencyMs", getLatencyMs())
                .append("modelServed", getModelServed())
                .append("errorType", getErrorType())
                .append("requestId", getRequestId())
                .append("createTime", getCreateTime())
                .toString();
    }
}
