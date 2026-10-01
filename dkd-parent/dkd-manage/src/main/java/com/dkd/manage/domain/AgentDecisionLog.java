package com.dkd.manage.domain;

import java.math.BigDecimal;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体决策留痕对象 agent_decision_log（排期任务 3-7 决策审计页）。
 *
 * <p><b>为什么这个实体是只读的、没有任何 insert/update/delete 方法：</b>
 * 这张表是 AGENTS §6.3 要求的**审计证据**，写入方**只有智能体服务**（Python 侧
 * `app/audit.py`，每次写操作决策必落一行）。管理后台只提供“查”，不提供“改/删”——
 * 一旦后台能编辑留痕，它就不再是审计证据了（这属于红线级设计取舍，不是省事）。
 *
 * <p>字段与 `docs/ddl/agent_tables.sql` 一一对应；`inputContext` / `llmOutput`
 * 两个 JSON 列在列表查询里**不返回**（见 Mapper XML 的 `selectAgentDecisionLogVo`），
 * 只有详情接口才带——审计列表会随每次对话增长，列表里塞 JSON 会让分页越来越慢。
 *
 * @author ruoyi
 * @date 2026-10-01
 */
public class AgentDecisionLog extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 全链路追踪 ID（网关生成，贯穿 Java→Python→回调） */
    private String requestId;

    /** 场景：1-通用问答 2-补货 3-诊断 4-运营分析 */
    private Integer scene;

    /** 触发用户 ID（定时任务为 null） */
    private Long userId;

    /** 触发方式：1-用户会话 2-定时任务 3-人工干预 */
    private Integer triggerType;

    /** 输入上下文（JSON，脱敏后；仅详情返回） */
    private String inputContext;

    /** LLM 结构化输出（JSON；仅详情返回） */
    private String llmOutput;

    /** 动作标识，如 restock.confirm / restock.adjust / restock.analyze */
    private String action;

    /** 目标对象类型：plan / restock_pause / task / vm / sku */
    private String targetType;

    /** 目标对象 ID */
    private String targetId;

    /** 结果：0-待定 1-成功 2-失败 3-被拒绝 */
    private Integer result;

    /** 失败/拒绝原因（保留业务异常原文） */
    private String errorMsg;

    /** 置信度 0.0000~1.0000（自动建单放量的依据；人工干预恒为 null） */
    private BigDecimal confidence;

    /** 本次决策消耗 token 合计 */
    private Integer costTokens;

    // 注：createTime 由 BaseEntity 提供（同样带 @JsonFormat），不要在本类重复声明——
    // 重复声明会造成两个同名字段/getter，MyBatis 映射与 JSON 序列化都会出现“哪个生效”的歧义。

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getRequestId()
    {
        return requestId;
    }

    public void setRequestId(String requestId)
    {
        this.requestId = requestId;
    }

    public Integer getScene()
    {
        return scene;
    }

    public void setScene(Integer scene)
    {
        this.scene = scene;
    }

    public Long getUserId()
    {
        return userId;
    }

    public void setUserId(Long userId)
    {
        this.userId = userId;
    }

    public Integer getTriggerType()
    {
        return triggerType;
    }

    public void setTriggerType(Integer triggerType)
    {
        this.triggerType = triggerType;
    }

    public String getInputContext()
    {
        return inputContext;
    }

    public void setInputContext(String inputContext)
    {
        this.inputContext = inputContext;
    }

    public String getLlmOutput()
    {
        return llmOutput;
    }

    public void setLlmOutput(String llmOutput)
    {
        this.llmOutput = llmOutput;
    }

    public String getAction()
    {
        return action;
    }

    public void setAction(String action)
    {
        this.action = action;
    }

    public String getTargetType()
    {
        return targetType;
    }

    public void setTargetType(String targetType)
    {
        this.targetType = targetType;
    }

    public String getTargetId()
    {
        return targetId;
    }

    public void setTargetId(String targetId)
    {
        this.targetId = targetId;
    }

    public Integer getResult()
    {
        return result;
    }

    public void setResult(Integer result)
    {
        this.result = result;
    }

    public String getErrorMsg()
    {
        return errorMsg;
    }

    public void setErrorMsg(String errorMsg)
    {
        this.errorMsg = errorMsg;
    }

    public BigDecimal getConfidence()
    {
        return confidence;
    }

    public void setConfidence(BigDecimal confidence)
    {
        this.confidence = confidence;
    }

    public Integer getCostTokens()
    {
        return costTokens;
    }

    public void setCostTokens(Integer costTokens)
    {
        this.costTokens = costTokens;
    }

    @Override
    public String toString()
    {
        return new ToStringBuilder(this, ToStringStyle.MULTI_LINE_STYLE)
                .append("id", getId())
                .append("requestId", getRequestId())
                .append("scene", getScene())
                .append("userId", getUserId())
                .append("triggerType", getTriggerType())
                .append("action", getAction())
                .append("targetType", getTargetType())
                .append("targetId", getTargetId())
                .append("result", getResult())
                .append("errorMsg", getErrorMsg())
                .append("confidence", getConfidence())
                .append("costTokens", getCostTokens())
                .append("createTime", getCreateTime())
                .toString();
    }
}
