# 状态码与角色码契约（唯一事实源）

> P1-14 常量收敛的产物。**改任何一列取值前先读本文档**：同一取值在 Java 两工程与 Python
> 侧各有消费方，三处独立 POM/运行时无法共享代码，只能靠「契约表 + 双向引用注释」防漂移。
> 维护纪律：改取值 = ① 改本文档 → ② 同步 `DkdContants.java` / `VmSystemConstant.java` /
> `app/constants.py` → ③ grep 全仓字面量确认无漏网 → ④ 各自跑测试门禁。
> 编制日期：2026-10-28

## 消费方速查

| 子系统 | 常量定义处 | 说明 |
| --- | --- | --- |
| dkd-parent | `dkd-common/.../constant/DkdContants.java`（Long/String） | 后台管理端，建单校验/报表口径 |
| dkd-app | `dkd-app/.../common/constant/VmSystemConstant.java`（Integer/String） | 运维移动端，接单/作业/出货；独立 POM 无法依赖 dkd-common，**取值必须与 DkdContants 对齐** |
| dkd-agent | `dkd-agent/app/constants.py` | Python 智能体，派单角色过滤/历史检索窗口 |
| SQL 字面量 | 各 Mapper XML（允许，但新增时在此登记） | `vm_status=2`（报表故障口径）、`task_status` 等 |

## 工单状态 tb_task.task_status

| 值 | 含义 | dkd-parent | dkd-app | dkd-agent |
| --- | --- | --- | --- | --- |
| 1 | 创建（待接单） | TASK_STATUS_CREATE | TASK_STATUS_CREATE | read_tools._TASK_STATUS_LABELS |
| 2 | 进行中 | TASK_STATUS_PROGRESS | TASK_STATUS_PROGRESS | 同上 |
| 3 | 已取消 | TASK_STATUS_CANCEL | TASK_STATUS_CANCEL | 同上 |
| 4 | 已完成 | TASK_STATUS_FINISH | TASK_STATUS_FINISH | 同上 |

## 工单类型 tb_task.product_type_id

| 值 | 含义 | 接单角色（见角色码表） | dkd-parent | dkd-app |
| --- | --- | --- | --- | --- |
| 1 | 投放 | 1003 维修 | TASK_TYPE_DEPLOY | TASK_TYPE_DEPLOY |
| 2 | 补货 | 1002 运营 | TASK_TYPE_SUPPLY | TASK_TYPE_SUPPLY |
| 3 | 维修 | 1003 维修 | TASK_TYPE_REPAIR | TASK_TYPE_REPAIR |
| 4 | 撤机 | 1003 维修 | TASK_TYPE_REVOKE | TASK_TYPE_REVOKE |

## 工单创建类型 tb_task.create_type（审计口径）

| 值 | 含义 | 定义处 |
| --- | --- | --- |
| 0 | 自动（智能体回调建单） | TaskServiceImpl.CREATE_TYPE_AUTO（私有常量，仅建单链路使用） |
| 1 | 手动（后台建单） | TaskServiceImpl.CREATE_TYPE_MANUAL（同上） |

## 售货机状态 tb_vending_machine.vm_status

| 值 | 含义 | dkd-parent | dkd-app | 备注 |
| --- | --- | --- | --- | --- |
| 0 | 未投放 | VM_STATUS_NODEPLOY | VM_STATUS_NODEPLOY | 正常生命周期，**不是故障** |
| 1 | 运行中 | VM_STATUS_RUNNING | VM_STATUS_RUNNING | 在线口径看心跳 JSON `$.status`，与此列无关 |
| 2 | 停机 | VM_STATUS_STOPPED | VM_STATUS_STOPPED（P1-14 补齐） | 报表「故障」唯一口径（业务规范 §4.5） |
| 3 | 已撤机 | VM_STATUS_REVOKE | VM_STATUS_REVOKE | 正常生命周期，**不是故障** |

## 订单状态 tb_order.status / 支付状态 tb_order.pay_status（dkd-app 定义）

| 值 | 订单状态 | 支付状态 |
| --- | --- | --- |
| 0 | 创建 | 未支付（PAY_STATUS_NOPAY） |
| 1 | 支付完成（PAYED） | 支付完成（PAY_STATUS_PAYED） |
| 2 | 出货成功（VENDOUT_SUCCESS） | 退款中（PAY_STATUS_REFUNDING） |
| 3 | 出货失败（VENDOUT_FAIL） | 退款完成（PAY_STATUS_REFUNDIED） |
| 4 | 失效（INVALID） | — |

> 报表口径：营收/订单只认 `status=2`（出货成功；业务规范 §4.5），dkd-parent 侧以
> `ReportServiceImpl.ORDER_STATUS_SUCCESS` 私有常量表达，未入 VmSystemConstant。

## 员工状态 tb_emp.status

| 值 | 含义 | dkd-parent |
| --- | --- | --- |
| 1 | 启用 | DkdContants.EMP_STATUS_NORMAL（建单校验拒收停用员工） |
| 0 | 禁用 | DkdContants.EMP_STATUS_DISABLE |

## 员工角色码 tb_role.role_code

| 值 | 含义 | dkd-parent | dkd-app | dkd-agent |
| --- | --- | --- | --- | --- |
| 1001 | 管理人员（任意工单兜底） | ROLE_CODE_ADMIN | USER_ROLE_ADMIN_ODE | — |
| 1002 | 运营人员（补货工单） | ROLE_CODE_BUSINESS | USER_ROLE_SUPPLY_ODE | ROLE_CODE_BUSINESS（read_tools 派单过滤） |
| 1003 | 维修人员（维修/投放/撤机） | ROLE_CODE_OPERATOR | USER_ROLE_REPAIRER_CODE | ROLE_CODE_REPAIR（fault_graph 派单过滤） |

> 命名各侧不统一（历史原因），取值与语义以本表为准；重命名属行为无关的清理，另行评审。

## 库存变更类型 tb_inventory_log.change_type（dkd-parent InventoryLog）

| 值 | 含义 | business_type 字符串 |
| --- | --- | --- |
| 1 | 销售 | SALE |
| 2 | 补货 | RESTOCK |
| 3 | 损耗 | LOSS |
| 4 | 调整（含初始化） | ADJUST / INIT |
| 5 | 盘点 | STOCKTAKE |

另：RECONCILE（对账）目前仅作为 business_type 字符串存在，无对应 change_type。

## dkd-agent 历史案例检索窗口（非状态码，一并收敛防漂移）

| 常量 | 值 | 消费方 | 为什么不同 |
| --- | --- | --- | --- |
| CASE_WINDOW_DAYS_PROGRAMMATIC | 1095（3 年） | read_tools（程序化调用） | 程序调用窗口明确，3 年足够且不拉全表 |
| CASE_WINDOW_DAYS_ASSISTANT | 2000（≈5.5 年） | assistant_tools（自然语言"历史/以前"） | 自然语言的"历史"含糊，窗口小会得出"没有历史"的假结论（维修单稀疏，本机全在 2023-03）；真实成本护栏是 per-device 过滤 + 超时熔断 |

> ⚠️ 这两个值**刻意不同**，不是漂移——合并成一个值会让其中一侧语义受损。
> 2026-10-28 曾被当作"同义不同值"的漂移实锤提报，核查注释后确认双方均有明确意图，
> 故收敛为「同一模块内可对照的两常量 + 本表登记」而非强行统一。
