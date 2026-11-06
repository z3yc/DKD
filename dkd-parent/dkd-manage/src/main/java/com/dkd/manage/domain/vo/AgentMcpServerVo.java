package com.dkd.manage.domain.vo;

import java.util.Date;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonFormat;

/**
 * MCP server 配置出参（排期 3-7，S12）。
 *
 * <p>{@code envKeys} 是 **HTTP 头名 → 环境变量名** 的映射（如
 * {@code {"X-Api-Key":"DKD_MCP_X_KEY"}}）：页面需要知道"要配哪个环境变量"，
 * 但本 VO 里没有、也不该有任何密钥值字段——表里本来就没有承载它的列。
 *
 * <p>{@code lastProbeTime/lastProbeMsg/status} 是 Python 探测后回写的结果，Java 只读。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMcpServerVo
{
    /** 主键 */
    private Long id;

    /** server 编码 */
    private String code;

    /** 展示名称 */
    private String name;

    /** 传输方式（本期仅 http） */
    private String transport;

    /** http endpoint（URL） */
    private String endpoint;

    /** 环境变量名映射（{HTTP 头名: ENV_NAME}；解析失败时为空对象而不是 null） */
    private Map<String, String> envKeys;

    /** 启用状态 0/1 */
    private Integer enabled;

    /** 调用超时（毫秒） */
    private Integer timeoutMs;

    /** 返回行数上限 */
    private Integer rowLimit;

    /** 只读限制（固定 1） */
    private Integer readOnlyOnly;

    /** 探测状态：unknown/ok/error/disabled */
    private String status;

    /** 最近探测时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date lastProbeTime;

    /** 最近探测结论（脱敏） */
    private String lastProbeMsg;

    /** 创建者 */
    private String createBy;

    /** 创建时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createTime;

    /** 更新者 */
    private String updateBy;

    /** 更新时间 */
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

    public String getTransport()
    {
        return transport;
    }

    public void setTransport(String transport)
    {
        this.transport = transport;
    }

    public String getEndpoint()
    {
        return endpoint;
    }

    public void setEndpoint(String endpoint)
    {
        this.endpoint = endpoint;
    }

    public Map<String, String> getEnvKeys()
    {
        return envKeys;
    }

    public void setEnvKeys(Map<String, String> envKeys)
    {
        this.envKeys = envKeys;
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

    public Integer getReadOnlyOnly()
    {
        return readOnlyOnly;
    }

    public void setReadOnlyOnly(Integer readOnlyOnly)
    {
        this.readOnlyOnly = readOnlyOnly;
    }

    public String getStatus()
    {
        return status;
    }

    public void setStatus(String status)
    {
        this.status = status;
    }

    public Date getLastProbeTime()
    {
        return lastProbeTime;
    }

    public void setLastProbeTime(Date lastProbeTime)
    {
        this.lastProbeTime = lastProbeTime;
    }

    public String getLastProbeMsg()
    {
        return lastProbeMsg;
    }

    public void setLastProbeMsg(String lastProbeMsg)
    {
        this.lastProbeMsg = lastProbeMsg;
    }

    public String getCreateBy()
    {
        return createBy;
    }

    public void setCreateBy(String createBy)
    {
        this.createBy = createBy;
    }

    public Date getCreateTime()
    {
        return createTime;
    }

    public void setCreateTime(Date createTime)
    {
        this.createTime = createTime;
    }

    public String getUpdateBy()
    {
        return updateBy;
    }

    public void setUpdateBy(String updateBy)
    {
        this.updateBy = updateBy;
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
