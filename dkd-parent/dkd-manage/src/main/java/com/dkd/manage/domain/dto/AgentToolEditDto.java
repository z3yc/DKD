package com.dkd.manage.domain.dto;

/**
 * 工具编辑入参（排期 3-4）。
 *
 * <p><b>为什么 DTO 里会带 code/source/readOnly/builtin（明明不可改）：</b>
 * 前端编辑弹窗通常回传整行数据，服务端需要能判断"用户有没有试图改这四个字段"。
 * 不接收它们就无法区分"没传"与"传了不同的值"——前者应放过（只改 enabled/超时/行数），
 * 后者必须明确报字段错误（静默忽略会让用户以为改成功了）。
 *
 * <p>真正的写保护在两层：Service 校验 + UPDATE SQL 只 set 三列（见 {@code AgentToolMapper.xml}）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentToolEditDto
{
    /** 启用状态 0/1（null = 不修改） */
    private Integer enabled;

    /** 工具级超时（毫秒）100~30000（null = 不修改） */
    private Integer timeoutMs;

    /** 返回行数上限 1~5000（null = 不修改） */
    private Integer rowLimit;

    /** 只读回显字段：传了且与库内不同 → 字段错误 */
    private String code;

    /** 只读回显字段：传了且与库内不同 → 字段错误 */
    private String source;

    /** 只读回显字段：传了且与库内不同 → 字段错误 */
    private Integer readOnly;

    /** 只读回显字段：传了且与库内不同 → 字段错误 */
    private Integer builtin;

    public Integer getEnabled()
    {
        return enabled;
    }

    public void setEnabled(Integer enabled)
    {
        this.enabled = enabled;
    }

    public Integer getTimeoutMs()
    {
        return timeoutMs;
    }

    public void setTimeoutMs(Integer timeoutMs)
    {
        this.timeoutMs = timeoutMs;
    }

    public Integer getRowLimit()
    {
        return rowLimit;
    }

    public void setRowLimit(Integer rowLimit)
    {
        this.rowLimit = rowLimit;
    }

    public String getCode()
    {
        return code;
    }

    public void setCode(String code)
    {
        this.code = code;
    }

    public String getSource()
    {
        return source;
    }

    public void setSource(String source)
    {
        this.source = source;
    }

    public Integer getReadOnly()
    {
        return readOnly;
    }

    public void setReadOnly(Integer readOnly)
    {
        this.readOnly = readOnly;
    }

    public Integer getBuiltin()
    {
        return builtin;
    }

    public void setBuiltin(Integer builtin)
    {
        this.builtin = builtin;
    }
}
