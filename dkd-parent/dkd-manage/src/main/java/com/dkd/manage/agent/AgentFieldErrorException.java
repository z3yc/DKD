package com.dkd.manage.agent;

/**
 * 智能体管理（配置台 / 工具中心 / 模型档案）的**字段级校验错误**基类。
 *
 * <p>为什么需要它：RuoYi 的 {@code AjaxResult.error(msg)} 只能给一句笼统提示，而表单需要
 * "哪个字段错了"才能标红。本异常的形状（field + message）就是契约 §4.2 的字段错误通道：
 * HTTP 200 + {@code code:400 + data:{field:msg}}。
 *
 * <p>为什么抽基类而不是给每个模块各写一个：配置台已有
 * {@link AgentConfigValidationException}（历史类名，为了不打断既有前端契约保留），
 * 工具中心与模型档案用本类；两个新控制器只需 {@code @ExceptionHandler(AgentFieldErrorException.class)}
 * 就能同时接住自己的字段错误，不必各自复制一份处理逻辑。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentFieldErrorException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /** 出错的 DTO 字段名（camelCase，与请求体一致；前端据此标红） */
    private final String field;

    /**
     * @param field   出错字段名（camelCase）
     * @param message 用户可读错误文案
     */
    public AgentFieldErrorException(String field, String message)
    {
        super(message);
        this.field = field;
    }

    public String getField()
    {
        return field;
    }
}
