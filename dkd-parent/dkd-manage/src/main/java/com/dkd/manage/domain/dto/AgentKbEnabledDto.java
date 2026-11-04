package com.dkd.manage.domain.dto;

/**
 * 知识库文档启停入参（排期 3-6）。
 *
 * <p>只有 enabled：启停不应顺带修改文档内容（改内容要走重新入库，触发切片/向量重建）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentKbEnabledDto
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
