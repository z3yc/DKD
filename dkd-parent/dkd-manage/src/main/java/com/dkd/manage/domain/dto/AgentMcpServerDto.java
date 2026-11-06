package com.dkd.manage.domain.dto;

import java.util.Map;

/**
 * MCP server 新增/编辑入参（排期 3-7，S12）。
 *
 * <p><b>这个类里没有、也永远不该有"密钥值"字段：</b>表只存
 * {@code env_keys_json = {HTTP 头名: 环境变量名}}，接口层同样只接收环境变量名映射。
 * 即使前端误传 {@code {"X-Api-Key":"sk-xxx"}}，值也会被 ENV_NAME 正则拒绝（400 字段错误），
 * 不会进入实体、更不会落库（AGENTS §7.1 在类型层 + 校验层双重落实）。
 *
 * <p>编辑时 {@code code} 只读：传了与库内不同的值 → 字段错误。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMcpServerDto
{
    /** 主键（编辑时由 Controller 用路径参数覆盖） */
    private Long id;

    /** server 编码 ^[a-z][a-z0-9_]{2,31}$（新增必填；编辑只读） */
    private String code;

    /** 展示名称 2~50 字 */
    private String name;

    /** 传输方式：本期仅 http（stdio 明确拒绝） */
    private String transport;

    /** http/https endpoint（≤500） */
    private String endpoint;

    /** 环境变量名映射（{HTTP 头名: ENV_NAME}；只存变量名，不含密钥值） */
    private Map<String, String> envKeys;

    /** 启用状态 0/1；新增不传取 0（安全默认），编辑不传沿用当前值 */
    private Integer enabled;

    /** 调用超时（毫秒）1000~5000；新增不传取 5000 */
    private Integer timeoutMs;

    /** 返回行数上限 1~200；新增不传取 200 */
    private Integer rowLimit;

    /** 只读限制：只能传 1（写工具一律不接入） */
    private Integer readOnlyOnly;

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
}
