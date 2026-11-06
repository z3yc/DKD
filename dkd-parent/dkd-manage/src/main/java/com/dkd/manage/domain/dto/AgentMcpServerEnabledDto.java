package com.dkd.manage.domain.dto;

/**
 * MCP server 启停入参（排期 3-7）。
 *
 * <p>只有 enabled：启停不应当顺带修改 endpoint/envKeys（那属于配置变更，需重新探测与安全复核）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMcpServerEnabledDto
{
    /** 启用状态 0/1 */
    private Integer enabled;

    public Integer getEnabled()
    {
        return enabled;
    }

    public void setEnabled(Integer enabled)
    {
        this.enabled = enabled;
    }
}
