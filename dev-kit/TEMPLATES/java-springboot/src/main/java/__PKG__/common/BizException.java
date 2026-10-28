package __PKG__.common;

/**
 * 业务异常。**所有**预期内的失败都必须用它，不允许裸抛 RuntimeException。
 *
 * <p>为什么强制：内部错误码必须只由全局兜底产生。业务分支抛 RuntimeException 会让
 * 调用方看到 500，把"用户填错了"当成"系统挂了"，告警随即被污染。
 *
 * <p>DEPS: 无（可单独摘取）。契约见 CONTRACTS/error-codes.md。
 */
public class BizException extends RuntimeException {

    private final String code;
    private final int httpStatus;
    private final boolean retryable;

    public BizException(String code, String message, int httpStatus, boolean retryable) {
        super(message);
        this.code = code;
        this.httpStatus = httpStatus;
        this.retryable = retryable;
    }

    // --- 便捷构造器：避免各处手写状态码导致归类不一致 ---
    public static BizException param(String code, String message) {
        return new BizException(code, message, 400, false);
    }

    public static BizException unauthorized(String code, String message) {
        return new BizException(code, message, 401, false);
    }

    public static BizException forbidden(String code, String message) {
        return new BizException(code, message, 403, false);
    }

    public static BizException notFound(String code, String message) {
        return new BizException(code, message, 404, false);
    }

    /** 状态冲突（重复提交/状态不允许）。必须把当前状态放进 R.data，否则前端无法引导用户。 */
    public static BizException conflict(String code, String message) {
        return new BizException(code, message, 409, false);
    }

    /** 下游不可用。retryable=true，调用方应退避重试。 */
    public static BizException dependency(String code, String message) {
        return new BizException(code, message, 503, true);
    }

    public String getCode() {
        return code;
    }

    public int getHttpStatus() {
        return httpStatus;
    }

    public boolean isRetryable() {
        return retryable;
    }
}
