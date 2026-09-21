package com.dkd.common.agent.support;

/**
 * 中继过程中前端主动断开（AbortController / 关闭页面）。
 *
 * <p>为什么单独建类：上游读超时与“写入客户端失败”都表现为 IOException，若不加区分，
 * 用户主动取消会被记成“智能体服务故障”并触发降级告警，污染监控口径（AGENTS §6.4）。
 * 因此中继层把写失败单独抛出，由控制器按“正常终止、不写错误帧”处理。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public class AgentClientAbortException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    public AgentClientAbortException(String message, Throwable cause)
    {
        super(message, cause);
    }
}
