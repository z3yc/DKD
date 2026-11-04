package com.dkd.manage.domain;

import java.util.Date;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;
import com.fasterxml.jackson.annotation.JsonFormat;

/**
 * MCP server 接入配置对象 agent_mcp_server（排期 3-7，原型 S12，DDL 见 docs/ddl/agent_kb_mcp.sql）。
 *
 * <p><b>边界（与 AGENTS §7 一致）：</b>
 * <ul>
 *   <li>配置的增删改由 Java 负责（RuoYi 权限 + 审计）；探测/同步/调用由 Python 负责（服务间密钥）；</li>
 *   <li>{@code envKeysJson} 只存 **HTTP 头名 → 环境变量名** 的映射（如
 *       {@code {"X-Api-Key":"DKD_MCP_X_KEY"}}），**永不存密钥值**——密钥值只存在于运行环境 env 中；</li>
 *   <li>{@code readOnlyOnly} 固定 1：只接只读工具（写工具一律由 Python 接入层拒绝，AGENTS §7.9）；</li>
 *   <li>{@code status / lastProbeTime / lastProbeMsg} 由 Python 探测后回写（本实体只读这几列，
 *       Java 的 UPDATE SQL 刻意不包含它们，避免两处写同一字段）。</li>
 * </ul>
 *
 * <p>为什么不与 {@code agent_mcp_tool/probe} 合并成一个聚合实体：工具与探测记录是 Python 侧写入的
 * 结果数据，管理台只通过代理读；这里只承载"配置"这一件事（一个 server 一行）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMcpServer extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** server 编码（^[a-z][a-z0-9_]{2,31}$；新增后可读不可改） */
    private String code;

    /** 展示名称 2~50 字 */
    private String name;

    /** 传输方式：本期仅 http（JSON-RPC over HTTP）；stdio 明确拒绝 */
    private String transport;

    /** http 为 URL；stdio 为可执行命令（本期不落库） */
    private String endpoint;

    /** 环境变量名映射 JSON（{HTTP 头名: ENV_NAME}，只存名字，不含密钥值） */
    private String envKeysJson;

    /** 启用状态 0/1：默认 0（需人工安全复核后开启） */
    private Integer enabled;

    /** 调用超时（毫秒）1000~5000 */
    private Integer timeoutMs;

    /** 返回行数上限 1~200 */
    private Integer rowLimit;

    /** 只读限制：固定 1（写工具一律不接入） */
    private Integer readOnlyOnly;

    /** 探测状态：unknown/ok/error/disabled（Python 回写，Java 只读） */
    private String status;

    /** 最近探测时间（Python 回写，Java 只读） */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date lastProbeTime;

    /** 最近探测结论（脱敏，Python 回写，Java 只读） */
    private String lastProbeMsg;

    /** 删除标记：0-存在 1-删除 */
    private String delFlag;

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

    public String getEnvKeysJson()
    {
        return envKeysJson;
    }

    public void setEnvKeysJson(String envKeysJson)
    {
        this.envKeysJson = envKeysJson;
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
        // 不打印 envKeysJson 内容：虽是变量名映射，也没有把配置细节打进日志的必要（AGENTS §6.1）
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("code", getCode())
                .append("name", getName())
                .append("transport", getTransport())
                .append("endpoint", getEndpoint())
                .append("envKeysConfigured", getEnvKeysJson() != null && !getEnvKeysJson().isEmpty())
                .append("enabled", getEnabled())
                .append("timeoutMs", getTimeoutMs())
                .append("rowLimit", getRowLimit())
                .append("readOnlyOnly", getReadOnlyOnly())
                .append("status", getStatus())
                .append("lastProbeTime", getLastProbeTime())
                .toString();
    }
}
