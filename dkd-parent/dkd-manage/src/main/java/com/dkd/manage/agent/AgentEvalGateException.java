package com.dkd.manage.agent;

import com.dkd.manage.domain.AgentEvalReport;

/**
 * 配置保存被评测护栏拒绝（排期 5-1）。
 *
 * <p><b>HTTP 语义是刻意的：HTTP 200 + body {@code code:409} + data{field:"evalGate",reason,latest}</b>，
 * 而不是 HTTP 409。原因：
 * <ul>
 *   <li>RuoYi 前端拦截器把非 200 当"传输/权限错误"，会走全局报错分支；而这里是**业务规则拒绝**，
 *       需要页面拿到 field/reason/latest 做"去跑评测 / 强制跳过"的专用提示；</li>
 *   <li>与乐观锁冲突（真 HTTP 409）区分开：那个要刷新重试，这个要补评测或走 force 流程，
 *       前端处理方式完全不同，用同一个状态码会让人只能二选一。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentEvalGateException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /** 未通过原因（给运营看） */
    private final String reason;

    /** 最新一条评测报告（可为 null = 从未评测） */
    private final AgentEvalReport latest;

    /**
     * @param reason 未通过原因
     * @param latest 最新报告
     */
    public AgentEvalGateException(String reason, AgentEvalReport latest)
    {
        super(reason);
        this.reason = reason;
        this.latest = latest;
    }

    public String getReason()
    {
        return reason;
    }

    public AgentEvalReport getLatest()
    {
        return latest;
    }
}
