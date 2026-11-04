package com.dkd.manage.domain.dto;

import java.math.BigDecimal;
import java.util.Map;

/**
 * 智能体配置的保存/编辑入参（排期 2-2，契约 §4.3）。
 *
 * <p>为什么参数用 {@code Map<String,Object>} 而不是强类型子 DTO：
 * 不同 runtime 的 params_json schema 不同（restock 6 项 / fault 2 项 / chat 空），
 * 强类型化只会造出一个「三载体共用、大半字段为 null」的胖类；区间校验集中在 Service，
 * 以 {@code AgentConfigDefaults.PARAM_SPECS} 为唯一口径（与 Python agent_defaults.py 对齐）。
 *
 * <p>{@code version} 只在编辑时必带（乐观锁）；新增时忽略。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigDto
{
    /** 主键；编辑时由 Controller 用路径参数覆盖，前端不必依赖 body 里的 id */
    private Long id;

    /** 智能体编码（新增必填；编辑只读） */
    private String code;

    /** 名称（必填 2~30 字；del_flag='0' 内唯一） */
    private String name;

    /** 描述（必填 ≤200 字） */
    private String description;

    /** 运行载体（新增只允许 chat；编辑只读） */
    private String runtime;

    /** 场景码（新增由后端自动分配且 ≥101；编辑只读） */
    private Integer scene;

    /** 是否内置（新增强制 0；编辑只读） */
    private Integer builtin;

    /** 系统提示词（chat/restock 必填 100~8000 字；fault 忽略并置 null） */
    private String systemPrompt;

    /** 工具子集，逗号分隔（chat 必填且全部在白名单内；restock/fault 必须为空） */
    private String tools;

    /** 模型名（空/null = 走 env 默认；非空须在白名单内） */
    private String model;

    /** 采样温度 null 或 0~2 */
    private BigDecimal temperature;

    /** 输出上限 null 或 256~8192 */
    private Integer maxTokens;

    /** 单次调用超时（秒）5~300；新增不传取 60，编辑不传沿用当前值 */
    private Integer timeoutS;

    /** 重试次数 0~5；新增不传取 2，编辑不传沿用当前值 */
    private Integer maxRetries;

    /** 业务参数（按 runtime 校验区间；chat 必须为 {}/null） */
    private Map<String, Object> paramsJson;

    /** 启用状态 0/1（新增不传取 1，编辑不传沿用当前值） */
    private Integer enabled;

    /** 停用原因（enabled=0 时必填 5~100 字） */
    private String disabledReason;

    /** 当前版本（编辑必带；乐观锁依据） */
    private Long version;

    /** 本次变更说明（写进 history，可选 ≤200 字） */
    private String changeNote;

    /**
     * 是否强制跳过评测护栏（排期 5-1；仅当 agent.evalGateEnabled=true 且被拒时生效）。
     *
     * <p>为什么留这个口子：评测本身可能因基础设施问题跑不动（CI 挂了/配额用尽），
     * 完全堵死会让线上配置改错字都做不到；强制跳过必须**填写原因并留痕**，
     * 让"为什么在没通过评测的情况下改了配置"可追溯。
     */
    private Boolean force;

    /** 强制跳过评测的原因（force=true 且被拒时必填，≥10 字；会写进 change_note） */
    private String forceReason;

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

    public Boolean getForce()
    {
        return force;
    }

    public void setForce(Boolean force)
    {
        this.force = force;
    }

    public String getForceReason()
    {
        return forceReason;
    }

    public void setForceReason(String forceReason)
    {
        this.forceReason = forceReason;
    }
}
