package com.dkd.manage.domain;

import java.math.BigDecimal;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体配置对象 agent_config（排期 2-2，契约 §3.1/§4）。
 *
 * <p><b>⚠️ 命名冲突提醒：本类与 {@code com.dkd.common.agent.config.AgentConfig} 同名不同包，
 * 两者含义完全不同且禁止互相 import</b>——后者是智能体网关的 Spring 装配类（注册过滤器），
 * 本类是 agent_config 表映射实体。同时 import 两者会直接编译不过；需要引用网关装配时用全限定名，
 * 或者只 import 本类（绝大多数业务代码只需要本类）。
 *
 * <p>几个字段的语义（改动前先读 DDL 与契约，不要凭直觉）：
 * <ul>
 *   <li>{@code paramsJson} 在 DB 是 JSON 列，本实体刻意用 {@link String} 承载原始文本：
 *       MyBatis 没有注册 JSON TypeHandler，直接映射成 Map 会在运行期静默得到 null；
 *       Map ↔ JSON 的转换集中在 Service（{@code AgentConfigServiceImpl}），
 *       保证「出参是对象、入参是对象」而对 Mapper 保持字符串；</li>
 *   <li>{@code temperature}/{@code maxTokens}/{@code model} 的 null 表示「未设置，走模型/env 默认」，
 *       不是「没查出来」——保存时把空值清成 null 是**有业务含义**的动作；</li>
 *   <li>{@code builtin=1} 的行（restock/fault/chat）不可删除、不可改 code/runtime，
 *       校验在 Service 层（DB 不拦，因为内置行也要允许改提示词/停用）。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfig extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 智能体编码（UK；内置固定 restock/fault/chat；自定义匹配 ^[a-z][a-z0-9_]{2,31}$） */
    private String code;

    /** 名称（列表/抽屉展示，del_flag='0' 内唯一） */
    private String name;

    /** 描述（用途/边界说明） */
    private String description;

    /** 运行载体：chat/restock/fault（决定可配置能力面） */
    private String runtime;

    /** 场景码：1-4 内置；自定义 chat 从 101 起自动分配 */
    private Integer scene;

    /** 是否内置：1-内置（不可删除、不可改 code/runtime/builtin） */
    private Integer builtin;

    /** 角色设定/系统提示词（fault 载体不调 LLM，可为 null） */
    private String systemPrompt;

    /** 工具子集（逗号分隔；须为 runtime 工具白名单子集） */
    private String tools;

    /** 模型名（空/null = 走 env 默认 DKD_AGENT_LLM_MODEL） */
    private String model;

    /** 采样温度 0~2；null = 模型默认 */
    private BigDecimal temperature;

    /** 输出上限 256~8192；null = 模型默认 */
    private Integer maxTokens;

    /** 单次 LLM 调用超时（秒）5~300 */
    private Integer timeoutS;

    /** LLM 失败重试次数 0~5 */
    private Integer maxRetries;

    /** 业务参数原始 JSON 文本（DB 为 JSON 列；Service 负责 Map↔JSON 转换） */
    private String paramsJson;

    /** 启用状态：1-启用 0-停用 */
    private Integer enabled;

    /** 停用原因（enabled=0 时必填 5~100 字） */
    private String disabledReason;

    /** 配置版本（每次保存 +1；乐观锁与回滚依据） */
    private Long version;

    /** 本次变更说明（写进 agent_config_history.change_note） */
    private String changeNote;

    /** 删除标记：0-存在 1-删除（本期不提供删除接口，保留列以符合软删规范） */
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

    public String getParamsJson()
    {
        return paramsJson;
    }

    public void setParamsJson(String paramsJson)
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
        // 刻意不打印 systemPrompt / description：提示词可达 8KB，整行日志会把审计日志刷爆
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("code", getCode())
                .append("name", getName())
                .append("runtime", getRuntime())
                .append("scene", getScene())
                .append("builtin", getBuiltin())
                .append("model", getModel())
                .append("enabled", getEnabled())
                .append("version", getVersion())
                .append("createBy", getCreateBy())
                .append("createTime", getCreateTime())
                .append("updateBy", getUpdateBy())
                .append("updateTime", getUpdateTime())
                .toString();
    }
}
