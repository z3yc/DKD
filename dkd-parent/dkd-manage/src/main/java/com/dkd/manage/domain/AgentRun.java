package com.dkd.manage.domain;

import java.util.Date;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体运行（agent_run）实体，对应 F-01 的 DDL。
 *
 * <p>为什么单独一张表而不是复用 agent_decision_log：decision_log 是**决策**粒度（一次写操作一条），
 * 回答不了"这一轮派给谁、调了哪个工具、拿到几行、花了多久"；run/step/tool_call 是**过程**粒度，
 * 两者通过 run_id 关联（详见 docs/ddl/agent_run_step.sql 头部说明）。
 *
 * <p>createBy / createTime / params 由 {@link BaseEntity} 提供，本类不重复声明
 * （重复声明会与父类字段冲突，MyBatis 映射也会出现两个同名属性）。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
public class AgentRun extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /**
     * 主键
     */
    private Long id;

    /**
     * 运行ID（业务主键，Python 生成）
     */
    private String runId;

    /**
     * 场景：restock-补货 / fault-故障诊断 / chat-对话
     */
    private String sceneCode;

    /**
     * 对象类型：vm-设备 / plan-补货计划
     */
    private String subjectType;

    /**
     * 对象ID（设备为 inner_code）
     */
    private String subjectId;

    /**
     * 全链路追踪ID（缺失为 NULL，不编造）
     */
    private String requestId;

    /**
     * 触发方式：1-用户 / 2-定时任务 / 3-人工干预
     */
    private Integer triggerType;

    /**
     * 状态：running / waiting_confirm / done / failed / interrupted
     */
    private String status;

    /**
     * 运行时的配置版本（agent_config.version，用于回放）
     */
    private Long configVersion;

    /**
     * 终止原因（interrupted / failed 必填，页面直接展示）
     */
    private String stopReason;

    /**
     * 开始时间
     */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date startedAt;

    /**
     * 结束时间（终态才写）
     */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date finishedAt;

    public Long getId()
    {
        return id;
    }

    public void setId(Long id)
    {
        this.id = id;
    }

    public String getRunId()
    {
        return runId;
    }

    public void setRunId(String runId)
    {
        this.runId = runId;
    }

    public String getSceneCode()
    {
        return sceneCode;
    }

    public void setSceneCode(String sceneCode)
    {
        this.sceneCode = sceneCode;
    }

    public String getSubjectType()
    {
        return subjectType;
    }

    public void setSubjectType(String subjectType)
    {
        this.subjectType = subjectType;
    }

    public String getSubjectId()
    {
        return subjectId;
    }

    public void setSubjectId(String subjectId)
    {
        this.subjectId = subjectId;
    }

    public String getRequestId()
    {
        return requestId;
    }

    public void setRequestId(String requestId)
    {
        this.requestId = requestId;
    }

    public Integer getTriggerType()
    {
        return triggerType;
    }

    public void setTriggerType(Integer triggerType)
    {
        this.triggerType = triggerType;
    }

    public String getStatus()
    {
        return status;
    }

    public void setStatus(String status)
    {
        this.status = status;
    }

    public Long getConfigVersion()
    {
        return configVersion;
    }

    public void setConfigVersion(Long configVersion)
    {
        this.configVersion = configVersion;
    }

    public String getStopReason()
    {
        return stopReason;
    }

    public void setStopReason(String stopReason)
    {
        this.stopReason = stopReason;
    }

    public Date getStartedAt()
    {
        return startedAt;
    }

    public void setStartedAt(Date startedAt)
    {
        this.startedAt = startedAt;
    }

    public Date getFinishedAt()
    {
        return finishedAt;
    }

    public void setFinishedAt(Date finishedAt)
    {
        this.finishedAt = finishedAt;
    }
}
