package com.dkd.manage.agent;

/**
 * 知识库上游（Python {@code /agents/kb/**}）调用失败（排期 3-6）。
 *
 * <p>为什么单独一个异常而不是复用 {@code AgentUpstreamException}（dkd-common 的网关异常）：
 * 这里只需要"失败类型 + 能否归因到状态码"两件事，且消息必须由 Java 侧**重新组织**——
 * 知识库接口的失败提示要给运营看（"请检查服务间密钥配置"/"服务暂不可用"），
 * 不能把上游响应体/堆栈透出去（AGENTS §6.1）。混用网关异常会让两条链路的降级文案互相污染。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentKbUpstreamException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /** 上游 HTTP 状态码；传输层失败（不可达/超时）时为 null */
    private final Integer httpStatus;

    /** true = 传输层失败（连接/超时），false = 上游有响应但状态码/信封不成功 */
    private final boolean transportFailure;

    /**
     * @param httpStatus       上游状态码（可 null）
     * @param transportFailure true=传输层失败（Python 不可达/超时）
     * @param message          面向运营的安全提示（**不得含上游响应体**）
     */
    public AgentKbUpstreamException(Integer httpStatus, boolean transportFailure, String message)
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

    /**
     * @return true 表示上游明确拒绝（4xx，如 401/407 鉴权失败）
     */
    public boolean isClientError()
    {
        return httpStatus != null && httpStatus.intValue() >= 400 && httpStatus.intValue() < 500;
    }
}
