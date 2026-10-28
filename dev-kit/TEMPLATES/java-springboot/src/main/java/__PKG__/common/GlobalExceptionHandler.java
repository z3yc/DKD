package __PKG__.common;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import javax.servlet.http.HttpServletResponse;

/**
 * 全局异常处理：把异常映射为统一响应包装，保证 HTTP 状态与 code 一致。
 *
 * <p>DEPS: spring-web, slf4j。
 * 注意：BizException 用 WARN 级别 —— 业务预期内的失败不是系统故障，打 ERROR 会淹没告警。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(BizException.class)
    public R handleBiz(BizException ex, HttpServletResponse response) {
        log.warn("业务异常 code={} msg={}", ex.getCode(), ex.getMessage());
        response.setStatus(ex.getHttpStatus());
        return new R(ex.getCode(), ex.getMessage(), null, RequestContext.getRequestId());
    }

    @ExceptionHandler(Exception.class)
    public R handleUnexpected(Exception ex, HttpServletResponse response) {
        // 记一次、抛一次，不重复记录；生产环境不回传堆栈（契约 error-codes.md §5）
        log.error("未处理异常 requestId={}", RequestContext.getRequestId(), ex);
        response.setStatus(500);
        return new R("5000", "服务内部错误，请稍后重试", null, RequestContext.getRequestId());
    }
}
