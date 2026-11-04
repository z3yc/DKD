package com.dkd.manage.domain.dto;

/**
 * 模型档案启停入参（排期 3-5）。
 *
 * <p>只有 {@code enabled} 一个字段：启停不应顺带修改连接信息，
 * 单独一个 DTO 能让"误把整行表单提交到启停接口"这类错误在编译期就不成立。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentLlmProfileEnabledDto
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
