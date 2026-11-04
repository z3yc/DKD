package com.dkd.manage.domain;

import com.dkd.common.core.domain.BaseEntity;

/**
 * 智能体只读页面的"时间范围查询"基类（Batch A · 排期 2-4 / 2-5）。
 *
 * <p><b>为什么单独抽一个基类：</b>日志页（{@code agent_log}）与会话页（{@code agent_conversation}）
 * 的时间窗护栏必须**逐字一致**——默认近 24h、最大 30 天、日期串补齐到当天末尾。
 * 如果两个实体各写一对 beginTime/endTime 字段、两处再各写一遍解析，迟早出现
 * "日志页能查 30 天、会话页只能查 7 天"这类口径漂移。
 * 护栏本身只在 {@code AgentReadQuerySupport.resolveWindow} 一处实现。
 *
 * <p>字段用 {@code String} 而不是 {@code Date} 是刻意的：RuoYi 的日期绑定器
 * （{@code BaseController#initBinder} → {@code DateUtils.parseDate}）解析失败会
 * **静默返回 null**，于是一个拼错的时间会被当成"没传"而悄悄退化为默认 24h；
 * 这里保留原文，由护栏统一校验并给出明确错误。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentTimeRangeQuery extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 查询开始时间（yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss；空 = 默认近 24h） */
    private String beginTime;

    /** 查询结束时间（同上；归一后**包含**该时刻） */
    private String endTime;

    public String getBeginTime()
    {
        return beginTime;
    }

    public void setBeginTime(String beginTime)
    {
        this.beginTime = beginTime;
    }

    public String getEndTime()
    {
        return endTime;
    }

    public void setEndTime(String endTime)
    {
        this.endTime = endTime;
    }
}
