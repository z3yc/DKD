package com.dkd.manage.agent;

/**
 * 智能体配置校验失败（字段级错误），契约 §4.2 的「字段错误」通道。
 *
 * <p>与 {@link AgentConfigConflictException} 分开的原因：两者的 HTTP 语义完全不同——
 * 字段错误是 HTTP 200 + {@code code:400 + data:{field:msg}}（前端表单标红用），
 * 并发冲突是 HTTP 409（前端要提示刷新）。混成一个异常就得在 Controller 里靠字符串判断。
 *
 * <p>为什么继承 {@link AgentFieldErrorException}：工具中心/模型档案的字段错误走同一个响应形状
 * （HTTP 200 + code 400 + data{field:msg}），抽公共基类后新控制器不必复制异常处理器；
 * 本类名保留是为了不打断配置台已有的 Controller/前端契约（改名属于无收益的破坏性变更）。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigValidationException extends AgentFieldErrorException
{
    private static final long serialVersionUID = 1L;

    /**
     * @param field   出错字段名（camelCase）
     * @param message 用户可读错误文案
     */
    public AgentConfigValidationException(String field, String message)
    {
        super(field, message);
    }
}
