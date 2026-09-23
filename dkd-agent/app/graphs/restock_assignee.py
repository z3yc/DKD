"""接单人分配策略（Phase 1 / 任务 1-8）。

只做一件事：**在同一个区域的可选运营人员里挑负载最低的那个**。纯函数、无 IO、无时钟，
所以「10 台跨区域设备怎么分」可以不开图、不连库地单测。

## 三条硬约束（来自 Java 侧的真实校验，不是我们的偏好）

1. **必须同区域**：`TaskServiceImpl.insertTaskDtoInternal` 里有
   `if (!emp.getRegionId().equals(vm.getRegionId()))` →
   `throw ServiceException("员工区域与设备区域不一致，请勿创建工单")`。
   所以「本地找不到人」时必须返回 `None`（计划落 6-待指派），
   **绝不能挑一个别的区域的人凑数**——那样运营看到的是“系统故障”，
   而不是“这个区域没人，请人工指派”。
2. **必须是运营人员（role_code=1002）且启用**：补货工单在 dkd-app 上是运营人员的作业队列
   （`EmpController.businessList` 同款过滤），派给维修人员（1003）等于把工单丢进错的队列。
3. **必须是确定性的**：负载相同就按 `emp_id` 升序，同一份输入永远得到同一个人。
   为什么不用轮询（round-robin）：游标是内存态，进程一重启就重置，分配会突然整体偏移；
   而重启（0-10 的中断恢复、3-9 的故障演练）在本项目是**常态**，不是异常。

## 负载怎么算（而不是“怎么感觉平均”）

负载 = **当日已占用该人的计划数**（`agent_restock_plan` 里 `plan_date=今天` 且
`assignee_id=该人` 且未软删的行数）。这个口径有三个好处：
- 是**已经发生过的事实**（不是估算的工时），复跑可复现；
- 与工作台看到的是同一份数据（运营不会再问“为什么系统说他不忙”）；
- 一个 SQL 聚合就能拿到，不引入新的负载表（AGENTS §1 单主库，禁止无谓的聚合开销）。

真正的排班（请假、路顺、每个人的实际工时）不在本期范围；等 1-14 双跑的偏差数据出来再谈。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.tools.read_tools import Assignee


def pick_assignee(candidates: Sequence[Assignee], *, loads: Mapping[int, int]) -> Assignee | None:
    """从同区域候选里挑一个接单人；无候选返回 `None`（调用方落 6-待指派）。

    @param candidates 同区域、启用中的运营人员（已按 emp_id 排序，见 `read_tools`）
    @param loads `emp_id` → 当日已占用计划数（缺失视为 0）
    @return 被选中的接单人；`candidates` 为空时返回 None

    排序键 `(loads, emp_id)`：先比负载，再比 emp_id。
    显式写 `emp_id` 而不是依赖输入顺序——调用方如果哪天改了 SQL 的 order by，
    分配结果不该跟着变（这是最容易悄悄退化的一处）。
    """
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (loads.get(candidate.emp_id, 0), candidate.emp_id))
