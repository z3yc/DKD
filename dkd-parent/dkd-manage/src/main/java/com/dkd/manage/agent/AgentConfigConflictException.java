package com.dkd.manage.agent;

/**
 * 智能体配置乐观锁冲突（契约 §4.2：HTTP 409）。
 *
 * <p>为什么必须单独成类而不是抛 {@code ServiceException}：RuoYi 的全局异常处理器把
 * ServiceException 统一转成 HTTP 200 + code=500，前端没法与「参数错误」区分；
 * 而并发覆盖是**必须让用户刷新重取**的场景，契约明确要求 HTTP 409 与当前/提交版本号。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public class AgentConfigConflictException extends RuntimeException
{
    private static final long serialVersionUID = 1L;

    /** 库内当前版本 */
    private final long currentVersion;

    /** 本次请求提交的版本 */
    private final long submittedVersion;

    /**
     * @param currentVersion   库内当前版本
     * @param submittedVersion 请求提交的版本
     */
    public AgentConfigConflictException(long currentVersion, long submittedVersion)
    {
        super("配置已被他人更新，请刷新后重试");
        this.currentVersion = currentVersion;
        this.submittedVersion = submittedVersion;
    }

    public long getCurrentVersion()
    {
        return currentVersion;
    }

    public long getSubmittedVersion()
    {
        return submittedVersion;
    }
}
