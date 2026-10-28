package com.dkd.common.constant;

/**
 * 帝可得业务常量
 *
 * <p>⚠️ P1-14 契约纪律：本类是「状态码/角色码」在 dkd-parent 侧的定义处。
 * 唯一事实源是 {@code docs/CONTRACTS/status-codes.md}——改任何取值前先读它。
 * 同一取值还在两处独立定义，**必须同步改**：
 * <ul>
 *   <li>{@code dkd-app/src/main/java/com/dkd/app/common/constant/VmSystemConstant.java}
 *       （运维移动端，独立 POM 无法依赖本模块，取值靠契约对齐）；</li>
 *   <li>{@code dkd-agent/app/constants.py}（Python 智能体，角色码与检索窗口）。</li>
 * </ul>
 * Mapper XML 中的 SQL 字面量（如 vm_status=2）允许保留，但需在契约表登记。
 */
public class DkdContants {

    /**
     * 工单类型：投放工单
     */
    public static final Long TASK_TYPE_DEPLOY = 1l;

    /**
     * 工单类型：补货工单
     */
    public static final Long TASK_TYPE_SUPPLY = 2l;

    /**
     * 工单类型：维修工单
     */
    public static final Long TASK_TYPE_REPAIR = 3l;

    /**
     * 工单类型：撤机工单
     */
    public static final Long TASK_TYPE_REVOKE = 4l;

    /**
     * 工单状态：创建(待处理)
     */
    public static final Long TASK_STATUS_CREATE = 1l;
    /**
     * 工单状态：进行
     */
    public static final Long TASK_STATUS_PROGRESS = 2l;
    /**
     * 工单状态：取消
     */
    public static final Long TASK_STATUS_CANCEL = 3l;
    /**
     * 工单状态：完成
     */
    public static final Long TASK_STATUS_FINISH = 4L;

    /**
     * 售货机状态：未投放
     */
    public static final Long VM_STATUS_NODEPLOY = 0l;
    /**
     * 售货机状态：运营
     */
    public static final Long VM_STATUS_RUNNING = 1l;
    /**
     * 售货机状态：停机（现场停用但未撤机）。
     *
     * <p>口径来源：设备管理页的状态字典 `vm_status`（0-未投放 1-运行中 2-停机 3-已撤机）。
     * ⚠️ 存量业务表 DDL 未入库（AGENTS §1 约束 5），本常量于 2026-10-26 按字典口径补充；
     * 若库里 `sys_dict_data` 的 `vm_status` 定义与此不同，只需改这一处（调用点是 TaskServiceImpl.isRepairAllowed）。
     */
    public static final Long VM_STATUS_STOPPED = 2l;
    /**
     * 售货机状态：撤机
     */
    public static final Long VM_STATUS_REVOKE = 3l;

    /**
     * 员工启用
     */
    public static final Long EMP_STATUS_NORMAL = 1L;

    /**
     * 员工禁用
     */
    public static final Long EMP_STATUS_DISABLE = 0L;

    /**
     * 角色编码：运营员
     */
    public static final String ROLE_CODE_BUSINESS = "1002";

    /**
     * 角色编码：维修员
     */
    public static final String ROLE_CODE_OPERATOR = "1003";

    /**
     * 角色编码：管理人员（可执行任意类型工单，用于兜底/值班场景）
     */
    public static final String ROLE_CODE_ADMIN = "1001";

}
