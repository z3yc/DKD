package com.dkd.common.agent.support;

/**
 * 调用 Python 智能体服务失败。
 *
 * <p>为什么自定义异常而不是直接用 RestClientException：调用方需要区分“传输层不可达/超时”
 * 与“上游返回了非预期状态码”两类情况，才能决定是降级提示还是透传状态码；
 * 同时约定 message **只含异常类型名与简短原因**，绝不携带上游原始报文（可能含用户数据）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public class AgentUpstreamException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /**
     * 传输层失败（连不上 / 超时）时的状态码占位值
     */
    public static final int STATUS_TRANSPORT_FAILURE = 0;

    private final int status;

    /**
     * @param status 上游 HTTP 状态码；传输层失败时用 {@link #STATUS_TRANSPORT_FAILURE}
     * @param message 简短原因（不含上游原始报文）
     */
    public AgentUpstreamException(int status, String message)
    {
        super(message);
        this.status = status;
    }

    /**
     * @param message 简短原因（不含上游原始报文）
     * @param cause 原始异常，仅供日志追溯
     */
    public AgentUpstreamException(String message, Throwable cause)
    {
        super(message, cause);
        this.status = STATUS_TRANSPORT_FAILURE;
    }

    /**
     * @param status 上游 HTTP 状态码
     * @param message 简短原因
     * @param cause 原始异常
     */
    public AgentUpstreamException(int status, String message, Throwable cause)
    {
        super(message, cause);
        this.status = status;
    }

    public int getStatus()
    {
        return status;
    }
}
