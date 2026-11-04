package com.dkd.manage.agent;

/**
 * MCP 上游（Python {@code /agents/mcp/**}）调用失败（排期 3-7）。
 *
 * <p>与 {@link AgentKbUpstreamException} 同构但类型分开：两者的降级文案与排查方向不同
 * （知识库是"语料/检索"，MCP 是"外部 server 连通/只读闸"），共用一个异常会让
 * Controller 无法按域给出正确提示。消息一律由 Java 侧重新组织，**不透传上游响应体**
 * （AGENTS §6.1）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentMcpUpstreamException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /** 上游 HTTP 状态码；传输层失败（不可达/超时）时为 null */
    private final Integer httpStatus;

    /** true = 传输层失败（连接/超时），false = 上游有响应但状态码/信封不成功 */
    private final boolean transportFailure;

    /**
     * @param httpStatus       上游状态码（可 null）
     * @param transportFailure true=Python 不可达/超时
     * @param message          面向运营的安全提示（不得含上游响应体）
     */
    public AgentMcpUpstreamException(Integer httpStatus, boolean transportFailure, String message)
    {
        super(message);
        this.httpStatus = httpStatus;
        this.transportFailure = transportFailure;
    }

    public Integer getHttpStatus()
    {
        return httpStatus;
    }

    public boolean isTransportFailure()
    {
        return transportFailure;
    }
}
