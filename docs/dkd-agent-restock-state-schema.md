# 补货子图 state schema · 设计评审稿（任务 0-13）

> 状态：**待评审**（评审通过后置为「已冻结」，并在本文件顶部标注冻结日期与版本）
> 评审范围：补货子图（`restock_graph`）的 state 字段、明细模型、与 Java 侧的字段映射、中断/恢复契约、版本兼容规则
> 代码实现：`dkd-agent/app/graphs/restock_state.py`（冻结版）+ `dkd-agent/tests/test_restock_state.py`（15 项契约测试，已通过）
> 评审人：Python / Java / 前端 / 产品（0.5 人日）
> 前置依赖：方案 V1.1 §4.2 / §5.2 / §5.5；`docs/ddl/agent_tables.sql`

---

## 1. 为什么要为 state 单独做评审（而不是直接写代码）

LangGraph 的持久化语义是：**最新图代码会立即作用于所有历史 checkpoint**，而不是把旧会话固定在旧代码上。翻译成人话：

> 只要线上还有"上一版代码创建、尚未走完"的会话（补货计划停在「待确认」、诊断对话停在多轮中间），
> 你这次发版改的 state 字段，就要能读懂那些**旧结构**的存档。

所以 state 字段一旦上线：

- **不能改名**（旧存档里还是旧 key）；
- **不能删字段**（反序列化或节点读取会失败）；
- **新增字段必须有默认值**（旧存档里没有这个 key）。

这与「数据库加字段」是同一类问题，但**没有 DDL 迁移可依赖**——唯一的防线就是评审 + 契约测试。这就是 0-13 存在的意义：**在写第一个节点之前把 state 定下来**，否则 Phase 1 的 1-6（`interrupt` 人工确认）一旦上线，改动成本会陡增。

**代码侧已做机械可检查的冻结**：`test_state_fields_are_frozen` 断言字段清单，任何增删都要同时改测试 → 强制开发者看到这份文档。

---

## 2. 图结构（state 的消费者）

```
START
  │
  ▼
collect_inventory        取数：tb_inventory + tb_order 聚合 + 在途补货工单 + 员工目录
  │                      → inventory_rows / sales_baseline / pending_restock_tasks / employee_directory
  ▼
compute_baseline         统计基线：7/14/30 天分位销量 + 货道容量约束 + 预计撑至日期
  │                      → plans[].items[].{suggested_quantity, estimated_days, priority}
  ▼
calibrate_with_llm       LLM 校准：点位画像 / 节假日 / 异常波动 + 生成「依据」文案
  │                      → calibration_notes（LLM 输出**先夹取再落 plan**，AGENTS §7.6）
  ▼
assign_assignee          接单人分配：按设备 region_id 匹配 tb_emp（§5.5）
  │                      → plans[].assignee_id；无可用人 → status=6 待指派
  ▼
◆ interrupt(...)         在这里中断，等待运营确认（payload 见 §5）
  │                      恢复后写入 decisions / adjustments / reviewed_by
  ▼
create_tasks             调 Java POST /manage/task 批量建单（幂等：状态机 + 唯一键）
  │                      → created_tasks[] / failures[]（追加型）
  ▼
  END（复盘由独立 Job 完成，不在本图内）
```

> 说明：`create_tasks` 失败**不回滚已成功设备的工单**，而是把失败原因按设备记入 `failures[]` 返回前端（运营看到"哪台设备为什么没建成"，而不是整体失败）。

---

## 3. state 字段冻结表

命名约定：snake_case；与 `agent_restock_plan` / `RestockSuggestionDto` 对齐。

| 字段 | 类型 | reducer | 默认 | 写入节点 | 语义与约束 |
| --- | --- | --- | --- | --- | --- |
| `plan_date` | `str` | 覆盖 | — | 入口注入 | `YYYY-MM-DD`，对应 `agent_restock_plan.plan_date`（唯一键的一半） |
| `trigger_type` | `int` | 覆盖 | — | 入口注入 | 1-用户会话 2-定时任务 3-人工干预（与 `agent_decision_log.trigger_type` 同码） |
| `request_id` | `str` | 覆盖 | — | 入口注入 | 全链路追踪，落 `agent_decision_log.request_id` |
| `candidate_vm_ids` | `list[int]` | 覆盖 | `[]` | collect | 待分析设备（运营中设备；分批输入以控制单次规模） |
| `inventory_rows` | `list[dict]` | 覆盖 | `[]` | collect | `tb_inventory` 原始行；**只存本次快照**，不回写业务表 |
| `sales_baseline` | `dict[str, Any]` | 覆盖 | `{}` | collect / compute | `vm_id(str)` → 分位销量等基线值 |
| `pending_restock_tasks` | `dict[str, Any]` | 覆盖 | `{}` | collect | `vm_id(str)` → 在途补货工单（建单前防重） |
| `employee_directory` | `dict[str, Any]` | 覆盖 | `{}` | collect | `region_id(str)` → 可用运维员工（接单人分配用） |
| `plans` | `list[dict]` | 覆盖 | `[]` | compute / calibrate / assign | `RestockPlan.model_dump()` 列表；**整表覆盖**（人工调整时按 vm_id 替换单条） |
| `calibration_notes` | `dict[str, Any]` | 覆盖 | `{}` | calibrate | `vm_id(str)` → LLM 校准结论与理由（原始输出，便于审计） |
| `adjustments` | `dict[str, Any]` | 覆盖 | `{}` | interrupt 恢复 | `vm_id(str)` → `{suggested_quantity?, reason, predicted_window?}` |
| `decisions` | `dict[str, Any]` | 覆盖 | `{}` | interrupt 恢复 | `vm_id(str)` → `"confirm"` / `"skip"` / `"adjust"` |
| `reviewed_by` | `int \| None` | 覆盖 | `None` | interrupt 恢复 | 确认人 user_id（来自网关注入头） |
| `created_tasks` | `list[dict]` | **`operator.add`** | `[]` | create_tasks | 追加型：`{vm_id, task_id, task_code}` |
| `failures` | `list[dict]` | **`operator.add`** | `[]` | create_tasks | 追加型：`{vm_id, reason}`（保留 Java 侧 `ServiceException` message） |
| `review_metrics` | `dict[str, Any]` | 覆盖 | `{}` | 复盘 Job | 预留：7 日复盘指标（不在本图主流程内） |
| `summary` | `dict[str, Any]` | 覆盖 | `{}` | 出口汇聚 | 面向前端的汇总（计划数/建议总量/失败数） |

**两条硬规则**（评审时请重点确认）：

1. **state 只放可 JSON 序列化的数据**（dict/list/基本类型）。Pydantic 模型只用于节点内部校验与对外契约，进 state 前先 `.model_dump()`。
   - 理由：checkpointer 用 JSON 序列化状态；塞自定义类需要注册反序列化器，LangGraph 升级时极易出现 `cannot deserialize` 类故障，且排障成本极高。
   - 已有测试：`test_state_is_json_serializable`。
2. **追加型集合必须用 `operator.add`**（`created_tasks`、`failures`）。其余字段是「后写覆盖」。
   - 理由：中断恢复会**重放**节点，覆盖语义会丢掉恢复前的执行结果。

---

## 4. 三端字段映射与语义陷阱（⭐ 本次评审最重要的一节）

### 4.1 明细字段对照

| Agent 侧（`RestockItem`） | Java 出参（`RestockSuggestionDto`） | Java 建单入参（`TaskDetailsDto`） | 表列（`tb_task_details`） | 备注 |
| --- | --- | --- | --- | --- |
| `sku_id` | `skuId` | `skuId` | `sku_id` | |
| `sku_name` | `skuName` | `skuName` | `sku_name` | |
| `channel_id` | `channelId` | — | — | 建单只用 `channelCode` |
| `channel_code` | `channelCode` | `channelCode` | `channel_code` | |
| `current_quantity` | `currentQuantity` | — | — | 来自 `tb_inventory.current_stock` |
| `max_capacity` | `maxCapacity` | **`expectCapacity`（禁止）** | `expect_capacity` | ⚠️ 见 4.2 |
| `suggested_quantity` | `suggestedQuantity` | **`expectCapacity`（必须）** | `expect_capacity` | ⚠️ 见 4.2 |
| `after_restock_quantity` | `afterRestockQuantity` | — | — | 校验用（必须 = 现库存 + 建议量） |
| `estimated_days` | `estimatedDays` | — | — | |
| `priority` | `priority` | — | — | 1~4 |
| `reason` | `reason` | — | — | 写入工单 `desc` 或留痕 |

### 4.2 ⚠️ 语义陷阱：`expect_capacity` 到底是"货道容量"还是"补货数量"

同一个字段名在三处含义不一致（代码证据见 §8）：

| 位置 | 注释/用法 | 事实 |
| --- | --- | --- |
| `dkd-parent` `TaskDetailsDto.expectCapacity` | 注释「**货道容量**」 | ❌ 注释错误 |
| `dkd-parent` `TaskDetails.expectCapacity` | 注释「**补货数量**」 | ✅ 与行为一致 |
| `dkd-app` `VendingMachineServiceImpl:62` | `setCurrentCapacity(currentCapacity + d.getExpectCapacity())` | ✅ **当作补货数量直接累加进库存** |

**结论（必须写进实现注释）**：建单时 `expectCapacity` 必须传 `suggested_quantity`（相对现库存的**增量**），**绝不能传 `max_capacity`**。

**如果填错会发生什么**（这是把它单列一节的唯一理由）：

```
Agent 把容量 10 填进 expectCapacity（本意"这台机器该补到 10"）
  → 运维现场补货完成 → dkd-app 执行 currentCapacity + 10
  → 货道库存虚增（实际只补了几件）
  → 下一轮库存预警/补货建议基于虚增数据 → 建议量继续偏离
  → 数据滚雪球，且很难归因（因为"看起来"每一步都成功）
```

**已实现的防线**（不靠人记）：

1. `RestockItem` 的范围夹取：`0 ≤ suggested_quantity ≤ max_capacity - current_quantity`，
   填容量必然越界 → **Pydantic 直接拒绝**（`test_item_rejects_capacity_mistaken_as_quantity`）；
2. `after_restock_quantity` 必须等于「现库存 + 建议量」→ 拦掉"补到某值"的语义混用；
3. 映射函数只从 `suggested_quantity` 取值，且测试断言 `expectCapacity != max_capacity`。

**建议的 Java 侧改动（待评审决定）**：把 `TaskDetailsDto.expectCapacity` 的注释从「货道容量」改为「补货数量」。
- 仅注释改动，**零行为变更、零回归风险**；收益是消除下一次事故的诱因。
- 不建议改字段名（`expectCapacity` → `restockQuantity`）：跨 3 个模块 + 前端 + app 端，收益不匹配成本。

---

## 5. 中断与恢复契约

### 5.1 中断点与 payload

```python
# 在 assign_assignee 之后调用；payload 必须是 JSON 可序列化的 dict
interrupt({
    "type": "restock_confirm",
    "plan_date": state["plan_date"],
    "plans": [ { "vm_id", "inner_code", "status", "sku_count",
                 "total_quantity", "assignee_id", "assignee_name", "items": [...] } ],
    "request_id": state["request_id"],
})
```

### 5.2 恢复

前端确认/调整后，用**同一 thread_id**恢复：

```python
graph.ainvoke(
    Command(resume={
        "decisions": {"1001": "confirm", "1002": "adjust", "1003": "skip"},
        "adjustments": {"1002": {"suggested_quantity": 4, "reason": "促销备货"}},
        "reviewed_by": 42,
    }),
    config={"configurable": {"thread_id": plan_date}},   # ← thread_id 约定
)
```

### 5.3 约定（评审确认项）

| 约定 | 取值 | 理由 |
| --- | --- | --- |
| `thread_id` | **`restock:{plan_date}:{region_id}`**（按区域分批）或 `restock:{plan_date}`（全量） | 与 `agent_restock_plan` 的 (vm_id, plan_date) 唯一键互补：计划落库是设备粒度，图会话可按批 |
| 幂等 | 状态机 `can_transition()` + DB 唯一键 + 建单前查在途工单 | 三重防线，覆盖重复点击/多端并发/任务重跑 |
| 超时未确认 | 计划保持 `status=1`（建议），**不自动建单**；次日新分析按 `plan_date` 隔离，不会互相污染 | 避免"没人确认就自动补货" |
| **状态机变更记录（2026-09-21 / 排期 1-7）** | `ALLOWED_TRANSITIONS` 新增 **3-已跳过 → 1-建议**，且仅由显式 `restore` 动作触发（带必填原因、仅限当天） | 0-13 冻结稿原把 3 设为不可逆终态，与原型 V2 的「恢复建议」按钮冲突：误点跳过当天无法挽回，运营只能等次日新计划。**5-已复盘仍为真正终态**（结论固化后不可改写）；跨日恢复被服务层拒绝（会污染 3-6 复盘口径）。变更同步落在 `restock_state.ALLOWED_TRANSITIONS`（含理由注释）、`restock_decisions.ACTION_RESTORE` 与 `docs/ddl/agent_tables.sql` 尾部变更记录 |
| 恢复时的越权校验 | `reviewed_by` 必须等于当前网关注入的用户；不同用户恢复同一 thread 需拒绝 | 会话归属校验（方案 §4.3 时序里写了"校验计划归属"） |

---

## 6. 版本与兼容规则（冻结后生效）

### 6.1 允许 / 禁止

| 操作 | 是否允许 | 说明 |
| --- | --- | --- |
| 新增字段（带默认值） | ✅ 允许 | 旧存档缺该 key 时取默认值 |
| 修改字段**类型**（如 int → list） | ❌ 禁止 | 旧存档反序列化会失败 |
| 改字段名 | ❌ 禁止 | 等同于删除 + 新增，且旧数据无法迁移 |
| 删除字段 | ❌ **禁止**（除非走 6.2 流程） | 需存量会话处理方案 |
| 改 reducer（覆盖 ↔ add） | ⚠️ 需评审 | 改变语义，可能重复计数或丢数据 |
| 改 status 码值 | ❌ 禁止 | 已落 `agent_restock_plan.status` 与 DDL 注释，属跨端契约 |

### 6.2 必须删除/改动字段时的流程（三步）

1. **停用**：先把字段标为 `deprecated`，节点改为「读默认值、不再写入」，发版一次；
2. **排空**：确认不存在引用该字段的活跃 checkpoint（可按 `thread_id` 前缀清理：
   `AsyncSqliteSaver.adelete_thread()` / `aprune()`）；
3. **删除**：确认排空后再删字段 + 删测试断言，并在本文档记录版本与日期。

### 6.3 依赖版本锁定（0-13 的另一半）

| 包 | 锁定版本 | 说明 |
| --- | --- | --- |
| `langgraph` | **1.2.11** | 方案原写 `≥0.2`（开放区间）→ 实际落 1.x，`interrupt` 字段在 0.6 被移除、1.0 又变更 |
| `langgraph-checkpoint` | **4.2.0** | 与 langgraph 主版本强绑定 |
| `langgraph-checkpoint-sqlite` | **3.1.1** | 会话持久化实现 |
| `langchain-openai` | **1.6.2** | LLM 接入 |

**升级流程要求**：不是"改一行版本号"，而是 —— ① 读该版本 breaking changes；② 检查 `interrupt`/checkpointer API 变更；③ 在**测试库**用一个含中断的旧 checkpoint 验证能否恢复；④ 全量测试 + 覆盖率门槛；⑤ 记录到本文档。`uv.lock` 为唯一事实来源，禁止手写版本区间。

---

## 7. 评审检查清单

- [ ] §3 冻结表的字段与语义无遗漏（尤其 `adjustments` / `decisions` 的 key 约定）——**前端需确认**
- [ ] `thread_id` 取「按区域分批」还是「全量」——**产品/Python**
- [ ] §4.2 语义陷阱结论确认；是否接受「只改 Java 注释」的修正方案——**Java**
- [ ] `skuImage` 是否需要 Agent 填充（当前为 `None`，前端补货工作台可能缺图）——**前端/产品**
- [ ] 「部分确认」是否在范围内（当前设计为**整单确认**，与 Java 设备级防重一致）——**产品**
- [ ] `assignorId`（工单创建人）取值：系统账号 / 首个管理员 / 当前确认人——**产品**
- [ ] 超时未确认的行为（保持建议、不自动建单）确认
- [ ] 状态机迁移表 `ALLOWED_TRANSITIONS` 是否覆盖全部业务分支（含"已跳过 → 恢复"）——**产品**
- [ ] `created_tasks` / `failures` 的追加语义确认（恢复时不丢历史）
- [ ] 版本升级流程（§6.3）确认，纳入 runbook

### 评审通过后的动作

1. 本文件顶部标注「已冻结（版本 vX / 日期）」；
2. `restock_state.py` 的 `FROZEN_FIELDS` 视为契约，变更须附评审记录；
3. 若 §4.2 的 Java 注释修正通过 → 单独提一个 `docs(manage): 修正 TaskDetailsDto.expectCapacity 注释语义` 提交（零行为变更）。

---

## 8. 证据索引

| 结论 | 证据位置 |
| --- | --- |
| 建单校验链（含设备级防重、员工区域匹配） | `dkd-parent/dkd-manage/.../service/impl/TaskServiceImpl.java:139-193`（防重 :147-158，区域 :159-167） |
| **`expect_capacity` 被 app 端当作补货数量累加** | `dkd-app/.../service/impl/VendingMachineServiceImpl.java:62` |
| `TaskDetails.expectCapacity` 注释「补货数量」 | `dkd-parent/dkd-manage/.../domain/TaskDetails.java:26`、`dkd-app/.../domain/TaskDetails.java:26` |
| `TaskDetailsDto.expectCapacity` 注释「货道容量」（**错误注释**） | `dkd-parent/dkd-manage/.../domain/dto/TaskDetailsDto.java:8` |
| 出参字段清单（前端已复用） | `dkd-parent/dkd-manage/.../domain/dto/RestockSuggestionDto.java:14-50` |
| 建单入参字段清单 | `dkd-parent/dkd-manage/.../domain/dto/TaskDto.java:8-14` |
| 库存列名（current_stock/min_stock/max_stock） | `dkd-parent/dkd-manage/src/main/resources/mapper/manage/InventoryMapper.xml:25` |
| 计划表与状态码、唯一键 | `docs/ddl/agent_tables.sql`（`agent_restock_plan`，`uk_agent_restock_plan_vm_date`） |
| 工单类型码（补货=2） | `dkd-parent/dkd-common/.../constant/DkdContants.java:16`（`TASK_TYPE_SUPPLY`） |
| 接单人分配与幂等设计 | `docs/DKD智能体接入方案-LangChain-LangGraph.md` §5.5 |
| 代码侧冻结与映射实现 | `dkd-agent/app/graphs/restock_state.py` |
| 契约测试（15 项） | `dkd-agent/tests/test_restock_state.py` |
