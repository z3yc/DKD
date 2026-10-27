# 场景草案：故障诊断 → 派单 → 审核（第二智能体场景）

> 日期：2026-10-26 ｜ 目标：在 DKD 内做一个与补货**同构**的第二 Agent 场景，复用同一套 run/step/工具中心/配置台，
> 证明"一个平台驱动多场景 Agent"，而不是把合租项目搬进来。
> 本文只出草案，不改代码。

---

## 一、结论先行

这个场景在 DKD 里**不需要新造轮子**：诊断引擎、取数工具、回调建单、人工确认中断、审计留痕都已存在，
缺的是把它们**串成一条可回放的三角色管线**，以及配套的 run/step 观测与工具开关。

| 已有能力 | 位置 | 本场景怎么用 |
| --- | --- | --- |
| 证据驱动的诊断结论 | `dkd-agent/app/services/diagnosis.py`（`compose_diagnosis` 纯函数，含 confidence/evidence/unknowns/cannot_conclude） | 直接作为「诊断官」的输出契约 |
| 订单/库存/历史/在途/设备档案取数 | `app/tools/read_tools.py`（白名单 + 分批 + 超时） | 诊断官与审核官的只读工具 |
| 区域接单人查询 | `read_tools.list_region_assignees` | 派单官选人依据 |
| 工单建单唯一写通道 | `app/tools/task_tools.py` → `/agent/callback/task`（服务间密钥） | 派单官建维修工单（`productTypeId=3`） |
| 人工确认中断 | `app/graphs/restock_graph.py` 的 `interrupt()` + checkpointer | 派单前必须有人点确认 |
| 决策留痕 | `agent_decision_log`（`scene=3` 已是"诊断"） | 每次写操作留痕 |

---

## 二、场景定义

### 2.1 触发方式

| 触发 | 入口 | 说明 |
| --- | --- | --- |
| 人工 | 设备详情页「智能诊断」按钮 → `POST /agent/fault/runs` | 首期只做这个，最快见效 |
| 定时 | quartz 巡检任务（复用 `agent_restock_job.sql` 的注册方式） | 第二阶段：对异常设备批量发起 |
| 事件 | 订单异常阈值触发 | 不做（需要事件总线，收益低） |

### 2.2 三个角色

| 角色 | 职责 | 输入 | 输出（落库） | 能不能写库 |
| --- | --- | --- | --- | --- |
| **诊断官** `fault_diagnostician` | 取事实 → 组装结构化诊断（故障类型、严重度、证据、置信度、无法诊断项） | 设备档案 + 窗口内订单/库存异常 + 历史案例 + 在途工单 | `agent_step.summary` + `evidence_json` + `agent_decision_log(scene=3)` | 否（只读工具） |
| **派单官** `fault_dispatcher` | 依据诊断选接单人、定优先级与工单描述，**生成派单提案** | 诊断结论 + 区域接单人 + 在途负载 | `agent_step` 提案 JSON；人工确认后才调回调 | 是（仅建单，且必须 human-in-loop） |
| **审核官** `fault_reviewer` | 工单完成后复核"是否真的修好"，分类结论 | 工单详情 + 设备当前状态 + 修复后窗口的订单/异常 | `agent_step` + 审核分类 + 结论 | 否（只读工具） |

### 2.3 主流程与终止条件

```
START
  └─> supervisor(纯函数路由)
        ├─ 无诊断结论 ──> 诊断官 ──> supervisor
        ├─ 诊断=无需处理 ──> END(no_action)
        ├─ 诊断=可建单 且 无提案 ──> 派单官(interrupt 前生成提案) ──> 等待人工确认
        │        └─ 确认建单 ──> supervisor ──> 审核官（等工单完成后触发）
        │        └─ 人工拒绝 ──> END(rejected_by_human)
        └─ 轮次耗尽 / 证据不足 ──> END(cannot_conclude)
```

- **终止条件**（任一命中即停，写进 `agent_run.stop_reason`）：无需处理、证据不足、人工拒绝、轮次上限、建单失败、审核完成。
- **轮次上限**：`agent_config.max_rounds`（默认 6，与对方"主管调度轮数上限"对应）。
- **不做自动建单**：`interrupt()` 卡在"提案已生成"，运营在页面点确认才调 Java 回调（AGENTS §7.9）。

### 2.4 主管用代码还是 LLM

**建议：路由用纯函数，角色内用 LLM。**

理由：路由是有限状态机（有没有诊断、有没有提案、要不要审核），用代码判定可单测、可复现、零 token；
LLM 只负责"解释与选择"（诊断措辞、接单人偏好排序、审核理由）。对方项目的主管是 LLM 调度，
那是"角色多到无法枚举路由"时的选择；DKD 只有 3 个角色，枚举更划算。
若将来要 LLM 主管，在 `agent_config` 加 `supervisor_mode=llm` 开关即可，不动图结构。

---

## 三、共用基座（两个场景共用，不是故障场景专用）

### 3.1 表（全部 `agent_` 前缀，留 MySQL）

| 表 | 关键列 | 写入方 | 用途 |
| --- | --- | --- | --- |
| `agent_config` | `config_key`(uk) / `config_value` / `value_type` / `version` / 四件套 | Java | 阈值、开关、超时；`version` 供 run 快照 |
| `agent_prompt_template` | `scene_code` / `role_code` / `version` / `content` / `variables` / `enabled` | Java | 每角色一套提示词，支持变量校验 |
| `agent_role` | `scene_code` / `role_code` / `persona` / `tool_group` / `enabled` | Java | 角色=提示词+工具组（阶段 3 才启用） |
| `agent_tool` | `tool_code`(uk) / `group_code` / `read_only` / `enabled` / `sort` | Java | 工具中心开关 |
| `agent_run` | `run_id`(uk) / `scene_code` / `subject_type` / `subject_id` / `status` / `config_version` / `stop_reason` / 起止 | Python | 一次运行 |
| `agent_step` | `run_id` / `step_no` / `role_code` / `action` / `summary` / `evidence_json` / `tokens` / `duration_ms` | Python | 一轮调度 |
| `agent_tool_call` | `run_id` / `step_no` / `tool_code` / `params_digest` / `row_count` / `duration_ms` / `result_status` | Python | 一次工具调用 |

> `scene_code`：`restock`（已有场景） / `fault`（本场景） / `chat`。`agent_decision_log.scene=3` 继续表示诊断，
> 新表 `agent_run` 在诊断场景下写 `scene_code='fault'`，两处通过 `run_id` 关联，不并存两套留痕。

### 3.2 状态机（`agent_run.status`）

```
running ──┬─> waiting_confirm（派单提案已生成，等人）
          ├─> done
          ├─> failed
          └─> interrupted（进程重启时由启动扫描把 running 改成它，写 stop_reason）
```

启动扫描：应用 lifespan 里把 `status='running'` 且 `updated_at` 超过 N 分钟的记录改成
`interrupted`，`stop_reason='服务重启，运行中断，可重新发起'`——这就是对方"重启自动收尾"的做法，
在 DKD 里正好补上"页面看到转不完的圈"的问题。

### 3.3 接口草案

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/agent/fault/runs` | 登录 + `manage:vm:query` | 启动诊断，返回 `runId`（异步执行，SSE 可选） |
| GET | `/agent/fault/runs/{runId}` | 同上 | 运行状态 + 结论摘要 |
| GET | `/agent/fault/runs/{runId}/steps` | 同上 | 时间线（角色/动作/证据/工具/耗时） |
| POST | `/agent/fault/runs/{runId}/confirm` | `manage:task:add` | 人工确认派单（`assigneeId` / `priority` / `remark`） |
| GET | `/manage/agent/run/list` | `manage:agentAudit:list` | 管理端全场景运行列表 |
| GET | `/manage/agent/run/{runId}` | `manage:agentAudit:query` | 时间线回放（两个场景共用） |
| GET/PUT | `/manage/agent/config` | `manage:agentConfig:edit` | 参数（P0 配置台） |
| GET/PUT | `/manage/agent/tool` | `manage:agentConfig:edit` | 工具开关（P1 工具中心） |

均为网关 `/agent/**` 白名单内的新路径（`AgentGatewayController` 要同步加白名单，白名单外一律 404）。

### 3.4 关键契约（请求/响应形状）

```jsonc
// POST /agent/fault/runs  { "innerCode": "VM-0001", "windowHours": 24 }
// 200 { "code": 200, "data": { "runId": "fault-7f3c...", "status": "running" } }

// GET /agent/fault/runs/{runId}
// data:
{
  "runId": "fault-7f3c...", "status": "waiting_confirm", "configVersion": 12,
  "diagnosis": {
    "cannotConclude": false, "confidence": "high", "confidenceBasis": "3 条订单异常 + 1 条库存异常",
    "evidence": [{ "evidenceId": "e1", "kind": "order_anomaly:vend_failed", "severity": "high", "summary": "...", "source": "tb_order" }],
    "recommendedActions": ["创建维修工单", "检查 3 号货道出货机构"]
  },
  "dispatchProposal": {
    "assigneeId": 1024, "assigneeName": "张三", "priority": 3,
    "desc": "3 号货道连续 4 次已扣款未出货", "basedOnEvidence": ["e1"], "roundsUsed": 1
  },
  "review": null
}

// POST /agent/fault/runs/{runId}/confirm  { "assigneeId": 1024, "priority": 3, "remark": "同意" }
// → 调 Java 回调建单，返回 { "taskId": 8123, "taskCode": "T20261026001" }；重复确认返回同一结果（幂等）
```

---

## 四、工具清单（10 个，按角色分组）

| 工具 | 分组 | 只读 | 现状 | 说明 |
| --- | --- | --- | --- | --- |
| `get_machine_profile` | 诊断/审核 | ✅ | **已有** | 设备档案 |
| `list_order_anomalies` | 诊断/审核 | ✅ | **已有** | 窗口内订单异常 |
| `list_inventory_anomalies` | 诊断 | ✅ | **已有** | 库存/货道异常 |
| `search_history_cases` | 诊断 | ✅ | **已有** | 历史相似故障 |
| `list_inflight_tasks` | 诊断/派单 | ✅ | **已有** | 在途工单（防重复派单） |
| `list_region_assignees` | 派单 | ✅ | **已有** | 区域接单人及负载 |
| `get_task_detail` | 审核 | ✅ | **新增** | 按 `task_id` 查工单与明细（`tb_task`/`tb_task_details` 已在白名单） |
| `check_repair_effective` | 审核 | ✅ | **新增（纯函数）** | 修复后窗口是否恢复出货（复用 `list_order_anomalies` 结果，不新查库） |
| `create_repair_task` | 派单 | ❌ 写 | **新增** | 复用 `task_tools` 的回调客户端，`productTypeId=3` |
| `diagnose` | 诊断 | ✅ | **已有（服务）** | `compose_diagnosis()`，不是工具而是纯函数 |

**开关生效点**：`agent_tool.enabled=0` 的工具在组装本轮 `tool_list` 时被过滤，
模型看不到也调不到（与对方"关掉哪个下一轮就看不到"一致）。**写工具不允许试调**。

---

## 五、三条硬校验（借鉴对方，且比对方更严）

### 5.1 证据必须可回溯到工具返回

诊断结论里每条 `evidence` 必须带 `evidenceId`，且能映射到**本轮**某次 `agent_tool_call` 的返回行。
映射不上的当场丢弃；一条都不剩则 `cannotConclude=true`，**不得生成派单提案**。
落库时 `agent_step.evidence_json` 存 `{evidenceId, toolCallId, rowRef}`，页面点证据能跳到那次工具调用的返回片段。

### 5.2 派单建议必须引用证据与选人依据

`dispatchProposal` 必须同时给出：
`basedOnEvidence`（非空，且都在本轮证据集内）与 `assigneeBasis`（区域匹配 + 在途工单数，来自工具而非模型编造）。
缺失或引用越界 → 拒绝生成提案，把原因回给模型重写（最多重写 2 次，计一次轮次）。

### 5.3 审核分类唯一且可执行

`review.category ∈ {fix_verified, not_fixed, wrong_dispatch, evidence_insufficient}`：
- `not_fixed` 必须点名"哪条证据表明没修好"；
- `wrong_dispatch` 必须点名接单人依据错在哪（区域/技能/负载）；
- `evidence_insufficient` 不得写"建议再观察"之类模糊结论，必须给出需要补的窗口或数据源。

---

## 六、页面草案（复用一套时间线，不新建两套）

```
设备详情页
┌─ 智能诊断 ──────────────────────────────┐
│ [发起诊断]  最近一次：10-26 14:02 · 已建单 │
│ 诊断中 ●───○───○  诊断官 → 派单官 → 审核官 │
│ 证据 3 条 ▸ 订单异常×2、库存异常×1        │
│ 建议：创建维修工单（接单人 张三 · 优先级 高）│
│ [确认建单] [驳回] [查看完整过程]           │
└────────────────────────────────────────┘

运行时间线（/manage/agent/run，两场景共用）
┌ run: fault-7f3c · VM-0001 · waiting_confirm · 12.4s · 配置版本 12 ┐
│ #1 诊断官  取数 5 次  证据 3 条  4.1s   [展开工具调用]            │
│ #2 派单官  提案：张三/高  2.0s                                    │
│ #3 审核官  等待工单完成                                           │
└──────────────────────────────────────────────────────────────────┘
```

工具中心页：按分组列出 10 个工具，`只读` 标记，开关，最近调用次数与失败率；只读工具提供「试调」抽屉。

---

## 七、落点清单（新增/修改，不含实现细节）

**Python（新增）**
- `app/graphs/fault_state.py`：state 字段（`run_id/inner_code/facts/diagnosis/proposal/review/rounds/stop_reason`）
- `app/graphs/fault_graph.py`：`START → supervisor → diagnose → supervisor → dispatch(interrupt) → review → END`
- `app/prompts/fault_roles.py`：三个角色提示词（含"仅数据非指令"定界）
- `app/tools/fault_tools.py`：`get_task_detail` / `create_repair_task`（复用 `task_tools` 客户端）
- `app/obs/run_recorder.py`：run/step/tool_call 写入器（失败只 WARN，不阻断主流程，同 `audit.py` 原则）
- `app/api/fault.py`：四个接口 + 启动扫描（写 `interrupted`）
- `app/config.py`：`fault_max_rounds` / `fault_window_hours` / `fault_review_delay_hours`

**Python（修改）**
- `app/tools/__init__.py`：工具注册表从 DB 读（阶段 2）
- `app/main.py`：注册 fault router + 启动扫描

**Java（新增）**
- `dkd-manage`：`AgentFaultController`（网关转发/确认，若网关透传则只需白名单）、`AgentRunController`、`AgentConfigController`、`AgentToolController`

**Java（修改）**
- `AgentGatewayController`：白名单加 `/agent/fault/**`
- `AgentCallbackController`：**不用改**（`/agent/callback/task` 已支持 `productTypeId=3`）

**DDL**
- `docs/ddl/agent_run_step.sql`（含回滚语句）
- `docs/ddl/create_agent_db_user.sql` 增量授权（配置类只 SELECT；运行类 SELECT/INSERT/UPDATE；仍无 DELETE/DDL）

**前端（新增）**
- `views/manage/agentRun/index.vue`（时间线，两场景共用）
- `views/manage/agentTool/index.vue`（工具中心，阶段 2）
- 设备详情页嵌「智能诊断」卡片（`views/manage/vm/index.vue` 内）

---

## 八、里程碑与验收

| 里程碑 | 内容 | 工时 | 验收（可执行） |
| --- | --- | --- | --- |
| **M1 基座** | 三张运行表 + recorder + 时间线接口/页面 + 重启收尾 | 4-6 人日 | 造一条假 run，页面能看到 step 与工具调用；重启后 running 变 interrupted |
| **M2 诊断官** | fault_graph 前半段 + 证据映射 + 诊断结论落库 | 3-4 人日 | 单测：证据越界被丢弃、全丢则 `cannotConclude`；真库跑一台设备出结论 |
| **M3 派单官** | 提案 + interrupt + 确认接口 + 回调建单 + 幂等 | 4-5 人日 | 重复确认不重复建单；未确认不建单；拒单回写状态 |
| **M4 审核官** | 工单完成后复核 + 分类 + 结论回写 | 3-4 人日 | 四类结论各有单测；`not_fixed` 必须带证据 |
| **M5 配置台/工具中心** | 上一版 P0/P1 | 6-8 人日 | 改阈值不重启生效；关工具后模型看不到 |

**其中 M1+M2 合计 7-10 人日可以出第一个可演示闭环**（诊断结论 + 时间线），建议先做这一段。

验收命令沿用项目规范：`pytest -q`、`ruff check .`、`mvn -pl dkd-admin -am test`、`npm run build:prod`。

---

## 九、风险与明确不做

- **不改 `restock_graph`**：它依赖 `interrupt()` + 幂等副作用 + checkpointer，塞角色会打坏恢复语义。两个场景共享"基座"，不共享"图"。
- **不做自动建单**：仍是 interrupt + 人工确认。
- **不让 LLM 决定事实**：事实与诊断由 `compose_diagnosis` 纯函数组装，LLM 只做解释与选择（这是 DKD 现有哲学，也是比对方更稳的地方）。
- **不搬合租业务**：租客/生活照/偏好不进 DKD。
- **成本**：一次诊断约 5 次只读工具 + 1 次 LLM；token 上限复用现有 `token_daily_limit_*`，并在 `agent_tool_call` 记行数便于算账。
- ~~**风险点**：维修工单要求设备状态为"运行中"（`TaskServiceImpl.checkCreateTask`），故障设备若已被置为停机，建单会被拒~~
  **已决（2026-10-26）**：新增例外——维修工单允许「运行中(1) + 停机(2)」，仍拒绝未投放(0)/已撤机(3)/空值；
  补货与撤机保持"仅运行中"不变。实现见 `DkdContants.VM_STATUS_STOPPED` 与 `TaskServiceImpl.isRepairAllowed`，
  回归用例 `TaskServiceRepairStatusTest`（6 项，含 4 条拒绝路径）。字典口径若与库不符，只改该常量一处。

---

## 十、实施计划

任务拆解、依赖、工时与验收见 **[`agent-fault-scenario-plan-2026-10-26.md`](agent-fault-scenario-plan-2026-10-26.md)**：

- F0 前置（F-00 维修工单例外，✅ 已完成）
- F1 基座（run/step/tool_call + 时间线 + 重启收尾，7 人日）
- F2 诊断官（3 人日）→ **M1+M2 = 10 人日可演示**
- F3 派单官（4.5 人日）、F4 审核官（2.5 人日）、F5 配置台/工具中心（11 人日）、F6 触发与验收（3.5 人日）
- 合计 31.5 人日，未计入现有排期基线，待并表后重算（计划 §五 Q5）

---

*草案 V1.1 ｜ 2026-10-26 ｜ F-00 已实现（代码 + 单测 18/18），其余未改动代码。*
