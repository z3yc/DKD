package __PKG__.common;

import org.slf4j.MDC;

/**
 * request_id 上下文（契约见 CONTRACTS/log-fields.md）。
 *
 * <p>用 MDC 而非 ThreadLocal：SLF4J 日志能自动带上，不需要每个调用点手动传参。
 * 在 Filter/Interceptor 里 set，结束后**必须 clear** —— 线程池复用会导致串号。
 */
public final class RequestContext {

    public static final String HEADER = "X-Request-Id";
    private static final String MDC_KEY = "request_id";
    private static final String DEFAULT = "-";

    private RequestContext() {
    }

    public static void setRequestId(String requestId) {
        MDC.put(MDC_KEY, requestId == null ? DEFAULT : requestId);
    }

    /** 未绑定时返回 `-` 而非抛错：日志本身不应成为新的故障源。 */
    public static String getRequestId() {
        String value = MDC.get(MDC_KEY);
        return value == null ? DEFAULT : value;
    }

    public static void clear() {
        MDC.remove(MDC_KEY);
    }
}
