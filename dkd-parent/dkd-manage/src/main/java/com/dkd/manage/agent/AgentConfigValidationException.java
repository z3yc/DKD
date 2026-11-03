package com.dkd.manage.agent;

/**
 * 智能体配置校验失败（字段级错误），契约 §4.2 的「字段错误」通道。
 *
 * <p>与 {@link AgentConfigConflictException} 分开的原因：两者的 HTTP 语义完全不同——
 * 字段错误是 HTTP 200 + {@code code:400 + data:{field:msg}}（前端表单标红用），
 * 并发冲突是 HTTP 409（前端要提示刷新）。混成一个异常就得在 Controller 里靠字符串判断。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigValidationException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /** 出错的 DTO 字段名（camelCase，与请求体一致；前端据此标红） */
    private final String field;

    /**
     * @param field   出错字段名（camelCase）
     * @param message 用户可读错误文案
     */
    public AgentConfigValidationException(String field, String message)
    {
        super(message);
        this.field = field;
    }

    public String getField()
    {
        return field;
    }
}
