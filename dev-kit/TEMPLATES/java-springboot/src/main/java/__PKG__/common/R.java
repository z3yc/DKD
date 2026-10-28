package __PKG__.common;

import java.util.HashMap;
import java.util.Map;

/**
 * 统一响应包装（契约见 CONTRACTS/api-envelope.md）。
 *
 * <p>为什么所有接口都要过这一层：调用方只需写一次解析逻辑；直接返回数组会让
 * "以后想加 total" 变成破坏性变更。
 *
 * <p>DEPS: 无（可单独摘取）。
 */
public class R {

    private String code;
    private String message;
    private Object data;
    private String requestId;

    public R() {
    }

    public R(String code, String message, Object data, String requestId) {
        this.code = code;
        this.message = message;
        this.data = data;
        this.requestId = requestId;
    }

    public static R ok(Object data) {
        return new R(Code.OK, "ok", data, RequestContext.getRequestId());
    }

    public static R ok() {
        return ok(null);
    }

    public static R fail(String code, String message) {
        return new R(code, message, null, RequestContext.getRequestId());
    }

    /**
     * 分页响应的固定形状（契约 api-envelope.md §2）。
     */
    public static R page(java.util.List<?> items, long total, int page, int pageSize) {
        Map<String, Object> data = new HashMap<String, Object>();
        data.put("items", items);
        data.put("total", total);
        data.put("page", page);
        data.put("page_size", pageSize);
        data.put("has_more", total > (long) page * pageSize);
        return ok(data);
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public Object getData() {
        return data;
    }

    public void setData(Object data) {
        this.data = data;
    }

    public String getRequestId() {
        return requestId;
    }

    public void setRequestId(String requestId) {
        this.requestId = requestId;
    }
}
