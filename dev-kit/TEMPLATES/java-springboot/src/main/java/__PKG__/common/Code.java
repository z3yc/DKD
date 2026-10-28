package __PKG__.common;

/**
 * 错误码常量。结构 `<SYS><CC><NN>`，类别见 CONTRACTS/error-codes.md。
 *
 * <p>新增错误码必须同步登记 CONTRACTS/error-codes.md 的附录表（场景 / 是否可重试 / 首版日期）。
 * 未登记的错误码不允许上线 —— 否则半年后没人知道 0537 是什么意思。
 */
public final class Code {

    public static final String OK = "0000";
    public static final String INTERNAL = "5000";

    // 01 参数错误：不可重试
    public static final String PARAM_MISSING = "0101";
    public static final String PARAM_INVALID = "0102";

    // 02/03 鉴权与权限
    public static final String UNAUTHORIZED = "0201";
    public static final String FORBIDDEN = "0301";

    // 04/05 资源与状态
    public static final String NOT_FOUND = "0401";
    public static final String CONFLICT = "0501";

    // 07 依赖不可用：**唯一可自动重试**的类别（06 限流除外）
    public static final String DEPENDENCY_DOWN = "0701";

    private Code() {
    }
}
