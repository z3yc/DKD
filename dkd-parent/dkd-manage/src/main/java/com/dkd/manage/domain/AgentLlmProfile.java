package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体模型档案对象 agent_llm_profile（排期 3-5 原型 S14，DDL 见 docs/ddl/agent_tool_llm_profile.sql）。
 *
 * <p><b>为什么只有 {@code apiKeyEnv}（环境变量名）而没有任何密钥字段：</b>
 * 密钥值只允许存在于运行环境的 env / 部署凭据里（AGENTS §7.1「密钥永不入库」）。
 * 本表存的是"去哪个环境变量取密钥"这个**指针**，页面可以展示它（运维需要知道配哪个变量），
 * 但 Java 侧从不解析、不缓存、不回显密钥值——真正的解析发生在 Python 探测/调用时。
 * 因此本实体没有、也永远不该有 {@code apiKey} 这样的字段：数据库里没有承载它的列，
 * 接口层也没有承载它的 DTO，从结构上而不是从校验上堵住"密钥被写进库/被接口带出来"。
 *
 * <p>{@code isDefault}：最多只有一个档案为 1。MySQL 没有部分唯一索引，所以由 Service
 * 在写操作里保证（先清其它行、再写本行，同一事务）；见 {@code AgentLlmProfileServiceImpl}。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentLlmProfile extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 档案编码（自定义，^[a-z][a-z0-9_]{2,31}$；新增后可读不可改） */
    private String code;

    /** 展示名称 2~50 字 */
    private String name;

    /** 供应商类型：openai_compatible/deepseek */
    private String provider;

    /** OpenAI 兼容基址（http/https，≤255） */
    private String baseUrl;

    /** 模型名（如 deepseek-chat） */
    private String model;

    /** **环境变量名**（如 DKD_DEEPSEEK_API_KEY），绝不存密钥值 */
    private String apiKeyEnv;

    /** 调用超时（秒）5~300 */
    private Integer timeoutS;

    /** 失败重试 0~5 */
    private Integer maxRetries;

    /** 启用状态 0/1 */
    private Integer enabled;

    /** 是否默认档案 0/1（应用层保证最多一个） */
    private Integer isDefault;

    /** 说明（用途/成本注意） */
    private String description;

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
        // 注意：apiKeyEnv 只记"是否配置"，不记值本身——虽只是变量名，也没有把配置细节
        // 打进日志的必要（AGENTS §6.1 日志最小化）
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("code", getCode())
                .append("name", getName())
                .append("provider", getProvider())
                .append("baseUrl", getBaseUrl())
                .append("model", getModel())
                .append("apiKeyEnvConfigured", getApiKeyEnv() != null && !getApiKeyEnv().isEmpty())
                .append("timeoutS", getTimeoutS())
                .append("maxRetries", getMaxRetries())
                .append("enabled", getEnabled())
                .append("isDefault", getIsDefault())
                .toString();
    }
}
