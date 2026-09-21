package com.dkd.common.agent.support;

import java.util.UUID;
import javax.servlet.http.HttpServletRequest;
import org.springframework.util.StringUtils;
import com.dkd.common.agent.domain.AgentHeaders;

/**
 * 全链路 requestId 的解析与净化（任务 0-6）。
 *
 * <p>为什么单独成类：requestId 需要在 {@code AgentTokenFilter}（写入 request attribute）与
 * {@code AgentGatewayController}（回带响应头、转发给 Python）两处得到**同一个值**，
 * 且两处都要做同样的净化处理；各写一遍必然出现“净化只改了一处”。
 *
 * <p>为什么要净化客户端传来的值（AGENTS §6/§7.8）：requestId 会进入日志行与 HTTP 响应头。
 * 客户端若塞入换行/控制字符，可伪造日志行（日志注入）或触发响应头非法字符异常（Tomcat 会抛
 * IllegalArgumentException 使请求 500）。因此只保留可见 ASCII 并限制长度，非法值一律更换为新 ID。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
public final class AgentRequestId
{
    /**
     * 长度上限：正常请求 ID 远小于此；截断比拒绝更友好（不因前端多带几个字符就整条链路失败）
     */
    public static final int MAX_LENGTH = 64;

    private AgentRequestId()
    {
    }

    /**
     * 解析请求头中的 requestId，非法或缺失时生成新的。
     *
     * @param request 当前请求
     * @return 可直接写入日志/响应头/上游请求头的 requestId
     */
    public static String resolve(HttpServletRequest request)
    {
        String sanitized = sanitize(request == null ? null : request.getHeader(AgentHeaders.REQUEST_ID));
        return StringUtils.hasText(sanitized) ? sanitized : generate();
    }

    /**
     * 净化：仅保留可见 ASCII（0x21~0x7E），并截断到 {@link #MAX_LENGTH}。
     *
     * @param raw 原始值
     * @return 净化后的值；全部字符非法时返回 null
     */
    public static String sanitize(String raw)
    {
        if (raw == null || raw.isEmpty())
        {
            return null;
        }
        StringBuilder builder = new StringBuilder(Math.min(raw.length(), MAX_LENGTH));
        for (int i = 0; i < raw.length() && builder.length() < MAX_LENGTH; i++)
        {
            char current = raw.charAt(i);
            if (current >= 0x21 && current <= 0x7E)
            {
                builder.append(current);
            }
        }
        return builder.length() == 0 ? null : builder.toString();
    }

    /**
     * 生成新的 requestId（无横线 UUID，32 字符）。
     *
     * @return requestId
     */
    public static String generate()
    {
        return UUID.randomUUID().toString().replace("-", "");
    }
}
