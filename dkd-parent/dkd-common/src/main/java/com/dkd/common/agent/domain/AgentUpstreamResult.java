package com.dkd.common.agent.domain;

/**
 * 上游（Python 智能体服务）非流式转发结果。
 *
 * <p>为什么要把状态码/响应体带回来而不是直接写进 HttpServletResponse：非 SSE 转发需要先知道上游状态码
 * 才能决定“透传”还是“降级为 503 友好提示”，先写响应体就再也改不了状态码了。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public final class AgentUpstreamResult
{
    private final int status;

    private final String contentType;

    private final byte[] body;

    private final String requestId;

    /**
     * @param status 上游 HTTP 状态码
     * @param contentType 上游 Content-Type（可能为 null）
     * @param body 上游响应体字节（已按上限截断保护，不为 null）
     * @param requestId 上游回带的 X-Request-Id（可能为 null）
     */
    public AgentUpstreamResult(int status, String contentType, byte[] body, String requestId)
    {
        this.status = status;
        this.contentType = contentType;
        this.body = body == null ? new byte[0] : body;
        this.requestId = requestId;
    }

    public int getStatus()
    {
        return status;
    }

    public String getContentType()
    {
        return contentType;
    }

    public byte[] getBody()
    {
        return body;
    }

    public String getRequestId()
    {
        return requestId;
    }

    /**
     * @return true 表示 2xx
     */
    public boolean isSuccessful()
    {
        return status >= 200 && status < 300;
    }

    /**
     * @return true 表示上游自身故障（5xx），网关应降级为友好提示而非透传上游报文
     */
    public boolean isServerError()
    {
        return status >= 500;
    }
}
