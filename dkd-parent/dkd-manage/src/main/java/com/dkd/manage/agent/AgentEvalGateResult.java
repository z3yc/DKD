package com.dkd.manage.agent;

import com.dkd.manage.domain.AgentEvalReport;

/**
 * 配置保存的评测护栏判定结果（排期 5-1）。
 *
 * <p>为什么单独一个值对象而不是 boolean：拒绝时要给页面三样东西——
 * 为什么被拒（reason）、有没有报告（latest）、以及可供展示的基线/准确率；
 * 只返回 boolean 会让前端只能弹一句"保存失败"，运营无法判断该去跑评测还是去改配置。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentEvalGateResult
{
    /** 是否通过（true = 允许保存） */
    private final boolean passed;

    /** 未通过原因（passed=true 时可为 null） */
    private final String reason;

    /** 最新一条评测报告（可能为 null = 从未评测过） */
    private final AgentEvalReport latest;

    /**
     * @param passed 是否通过
     * @param reason 未通过原因
     * @param latest 最新报告
     */
    public AgentEvalGateResult(boolean passed, String reason, AgentEvalReport latest)
    {
        this.passed = passed;
        this.reason = reason;
        this.latest = latest;
    }

    /**
     * 通过。
     *
     * @param latest 最新报告（可为 null：关闭护栏时不做检查）
     * @return 结果
     */
    public static AgentEvalGateResult pass(AgentEvalReport latest)
    {
        return new AgentEvalGateResult(true, null, latest);
    }

    /**
     * 拒绝。
     *
     * @param reason 原因
     * @param latest 最新报告（可为 null）
     * @return 结果
     */
    public static AgentEvalGateResult reject(String reason, AgentEvalReport latest)
    {
        return new AgentEvalGateResult(false, reason, latest);
    }

    public boolean isPassed()
    {
        return passed;
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
