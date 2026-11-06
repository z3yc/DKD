package com.dkd.manage.agent;

import java.util.Date;

/**
 * 智能体只读页面的**归一化时间窗**（Batch A · 排期 2-4 / 2-5）。
 *
 * <p>为什么要有这个值对象，而不是直接把请求里的 beginTime/endTime 塞进 Mapper：
 * <ul>
 *   <li>请求给的是**字符串**（前端日期组件可能给 {@code yyyy-MM-dd}，也可能给带秒的时间），
 *       而 SQL 需要的是明确的<b>闭区间</b>上下界。"日期串算到 00:00:00 还是 23:59:59.999"
 *       这类口径必须在一处定死，否则两个页面（日志页、会话页）迟早各算一套；</li>
 *   <li>时间窗是**硬护栏**（默认近 24h、最大 30 天），由
 *       {@link AgentReadQuerySupport#resolveWindow} 统一校验并抛出可读的 {@code ServiceException}；
 *       Mapper 只接受"已经合法"的窗口，不在 SQL 里再做业务判断。</li>
 * </ul>
 *
 * <p>不可变：窗口一旦算出来就被 Service 传给 Mapper，中间不应再被改动
 * （改了就与"校验时看到的范围"不一致，等于把护栏绕开）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentQueryWindow
{
    /** 查询下界（含） */
    private final Date beginTime;

    /** 查询上界（含；已按请求精度归一，日期串会补齐到当天 23:59:59.999） */
    private final Date endTime;

    /**
     * @param beginTime 下界（含），不能为 null
     * @param endTime   上界（含），不能为 null
     */
    public AgentQueryWindow(Date beginTime, Date endTime)
    {
        this.beginTime = beginTime;
        this.endTime = endTime;
    }

    public Date getBeginTime()
    {
        return beginTime;
    }

    public Date getEndTime()
    {
        return endTime;
    }

    @Override
    public String toString()
    {
        return "AgentQueryWindow[beginTime=" + beginTime + ", endTime=" + endTime + "]";
    }
}
