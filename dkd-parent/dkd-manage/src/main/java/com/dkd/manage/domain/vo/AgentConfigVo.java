package com.dkd.manage.domain.vo;

import java.math.BigDecimal;
import java.util.Date;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonFormat;

/**
 * 智能体配置出参（排期 2-2，契约 §4.3）。
 *
 * <p>字段与入参 {@code AgentConfigDto} 同构，另加审计列；两处刻意的差异：
 * <ul>
 *   <li>{@code paramsJson} 是**对象**（Service 从 DB 的 JSON 文本解析而来），
 *       前端动态表单可直接取键；</li>
 *   <li>时间列带 {@code @JsonFormat}，与 RuoYi 其它实体一致（否则前端拿到 ISO 时间串）。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigVo
{
    /** 主键 */
    private Long id;

    /** 智能体编码 */
    private String code;

    /** 名称 */
    private String name;

    /** 描述 */
    private String description;

    /** 运行载体 */
    private String runtime;

    /** 场景码（内置 1~4；自定义 ≥101） */
    private Integer scene;

    /** 是否内置 1/0 */
    private Integer builtin;

    /** 系统提示词（fault 为 null） */
    private String systemPrompt;

    /** 工具子集（逗号分隔；可为空串/null） */
    private String tools;

    /** 模型名（null/空 = 走 env 默认） */
    private String model;

    /** 采样温度（null = 模型默认） */
    private BigDecimal temperature;

    /** 输出上限（null = 模型默认） */
    private Integer maxTokens;

    /** 单次调用超时（秒） */
    private Integer timeoutS;

    /** 重试次数 */
    private Integer maxRetries;

    /** 业务参数对象（无参数时为 `{}`，不是 null） */
    private Map<String, Object> paramsJson;

    /** 启用状态 1/0 */
    private Integer enabled;

    /** 停用原因 */
    private String disabledReason;

    /** 当前版本 */
    private Long version;

    /** 最新一次变更说明 */
    private String changeNote;

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

    public String getDescription()
    {
        return description;
    }

    public void setDescription(String description)
    {
        this.description = description;
    }

    public String getRuntime()
    {
        return runtime;
    }

    public void setRuntime(String runtime)
    {
        this.runtime = runtime;
    }

    public Integer getScene()
    {
        return scene;
    }

    public void setScene(Integer scene)
    {
        this.scene = scene;
    }

    public Integer getBuiltin()
    {
        return builtin;
    }

    public void setBuiltin(Integer builtin)
    {
        this.builtin = builtin;
    }

    public String getSystemPrompt()
    {
        return systemPrompt;
    }

    public void setSystemPrompt(String systemPrompt)
    {
        this.systemPrompt = systemPrompt;
    }

    public String getTools()
    {
        return tools;
    }

    public void setTools(String tools)
    {
        this.tools = tools;
    }

    public String getModel()
    {
        return model;
    }

    public void setModel(String model)
    {
        this.model = model;
    }

    public BigDecimal getTemperature()
    {
        return temperature;
    }

    public void setTemperature(BigDecimal temperature)
    {
        this.temperature = temperature;
    }

    public Integer getMaxTokens()
    {
        return maxTokens;
    }

    public void setMaxTokens(Integer maxTokens)
    {
        this.maxTokens = maxTokens;
    }

    public Integer getTimeoutS()
    {
        return timeoutS;
    }

    public void setTimeoutS(Integer timeoutS)
    {
        this.timeoutS = timeoutS;
    }

    public Integer getMaxRetries()
    {
        return maxRetries;
    }

    public void setMaxRetries(Integer maxRetries)
    {
        this.maxRetries = maxRetries;
    }

    public Map<String, Object> getParamsJson()
    {
        return paramsJson;
    }

    public void setParamsJson(Map<String, Object> paramsJson)
    {
        this.paramsJson = paramsJson;
    }

    public Integer getEnabled()
    {
        return enabled;
    }

    public void setEnabled(Integer enabled)
    {
        this.enabled = enabled;
    }

    public String getDisabledReason()
    {
        return disabledReason;
    }

    public void setDisabledReason(String disabledReason)
    {
        this.disabledReason = disabledReason;
    }

    public Long getVersion()
    {
        return version;
    }

    public void setVersion(Long version)
    {
        this.version = version;
    }

    public String getChangeNote()
    {
        return changeNote;
    }

    public void setChangeNote(String changeNote)
    {
        this.changeNote = changeNote;
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
