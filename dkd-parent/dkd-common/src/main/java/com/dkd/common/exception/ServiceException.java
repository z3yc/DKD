package com.dkd.common.exception;

/**
 * 业务异常
 * 
 * @author ruoyi
 */
public final class ServiceException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /**
     * 错误码
     */
    private Integer code;

    /**
     * 错误提示
     */
    private String message;

    /**
     * 错误明细，内部调试错误
     *
     * 和 {@link CommonResult#getDetailMessage()} 一致的设计
     */
    private String detailMessage;

    /**
     * 空构造方法，避免反序列化问题
     */
    public ServiceException()
    {
    }

    public ServiceException(String message)
    {
        this.message = message;
    }

    public ServiceException(String message, Integer code)
    {
        this.message = message;
        this.code = code;
    }

    /**
     * 带根因的业务异常（FIX-1 配套改动，2026-10-01）：
     * 文案给用户看，{@code cause} 留给日志与排障——「把技术异常翻译成业务文案」
     * 不能以丢掉真因为代价（否则数据完整性问题会永远查不出是哪条约束触发的）。
     *
     * @param message 用户可读提示
     * @param cause   原始异常（如 {@code DuplicateKeyException}）
     */
    public ServiceException(String message, Throwable cause)
    {
        super(cause);
        this.message = message;
    }

    public String getDetailMessage()
    {
        return detailMessage;
    }

    @Override
    public String getMessage()
    {
        return message;
    }

    public Integer getCode()
    {
        return code;
    }

    public ServiceException setMessage(String message)
    {
        this.message = message;
        return this;
    }

    public ServiceException setDetailMessage(String detailMessage)
    {
        this.detailMessage = detailMessage;
        return this;
    }
}