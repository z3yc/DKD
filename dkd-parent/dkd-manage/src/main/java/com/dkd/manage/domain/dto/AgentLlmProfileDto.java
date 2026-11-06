package com.dkd.manage.domain.dto;

/**
 * 模型档案新增/编辑入参（排期 3-5，原型 S14）。
 *
 * <p><b>这个类里没有、也永远不该有"密钥值"字段</b>（如 apiKey/apiKeyValue/secret）：
 * 表结构只存环境变量名（{@code apiKeyEnv}），接口层同样只接收变量名。
 * 从 DTO 形状上就杜绝"前端把密钥提交上来"——即便前端传了 {@code apiKey} 字段，
 * Jackson 反序列化时也会因为没有对应属性而丢弃，不会进入任何业务对象、更不会落库。
 * 这是"密钥永不入库"（AGENTS §7.1）在类型层的落实，而不是靠某处 if 判断。
 *
 * <p>编辑时 {@code code} 只读：传了与库内不同的值 → 字段错误。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentLlmProfileDto
{
    /** 主键（编辑时由 Controller 用路径参数覆盖） */
    private Long id;

    /** 档案编码 ^[a-z][a-z0-9_]{2,31}$（新增必填；编辑只读） */
    private String code;

    /** 展示名称 2~50 字 */
    private String name;

    /** 供应商类型（可空，新增默认 openai_compatible） */
    private String provider;

    /** OpenAI 兼容基址（http/https，≤255） */
    private String baseUrl;

    /** 模型名（非空，≤64） */
    private String model;

    /** **环境变量名** ^[A-Z][A-Z0-9_]{2,63}$（只存变量名，绝不提交/接收密钥值） */
    private String apiKeyEnv;

    /** 调用超时（秒）5~300；新增不传取 60，编辑不传沿用当前值 */
    private Integer timeoutS;

    /** 失败重试 0~5；新增不传取 2，编辑不传沿用当前值 */
    private Integer maxRetries;

    /** 启用状态 0/1；新增不传取 1，编辑不传沿用当前值 */
    private Integer enabled;

    /** 是否默认档案 0/1（=1 时同事务把其它档案置 0）；新增不传取 0，编辑不传沿用当前值 */
    private Integer isDefault;

    /** 说明（≤500 字） */
    private String description;

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

    public String getProvider()
    {
        return provider;
    }

    public void setProvider(String provider)
    {
        this.provider = provider;
    }

    public String getBaseUrl()
    {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl)
    {
        this.baseUrl = baseUrl;
    }

    public String getModel()
    {
        return model;
    }

    public void setModel(String model)
    {
        this.model = model;
    }

    public String getApiKeyEnv()
    {
        return apiKeyEnv;
    }

    public void setApiKeyEnv(String apiKeyEnv)
    {
        this.apiKeyEnv = apiKeyEnv;
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

    public Integer getEnabled()
    {
        return enabled;
    }

    public void setEnabled(Integer enabled)
    {
        this.enabled = enabled;
    }

    public Integer getIsDefault()
    {
        return isDefault;
    }

    public void setIsDefault(Integer isDefault)
    {
        this.isDefault = isDefault;
    }

    public String getDescription()
    {
        return description;
    }

    public void setDescription(String description)
    {
        this.description = description;
    }
}
