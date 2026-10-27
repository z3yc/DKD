# 智能体能力差距分析：ai_coliving 四角色平台 → DKD 融合方案

> 日期：2026-10-26 ｜ 输入：对方项目（`ai_coliving/fastapi-app`，合租匹配四角色）功能清单
> 对照对象：DKD 智能体（`dkd-agent` + `dkd-parent` agent 包 + `dkd-vue` 的 agentAudit/restock 页面）
> 结论先行：**能搬的是"运营可配置 + 可回放"的能力，不能搬的是"密钥入库 / 业务实体 / 四角色图"本身。**

---

## 一、一句话判断

DKD 在**安全与状态一致性**上比对方成熟（只读账号+表白名单+回调鉴权+幂等唯一键+CAS 抢占+跨重启 checkpoint），
对方在**运营可配置性与可观测性**上比 DKD 成熟（配置库化、工具中心、Run/Step 时间线、密钥脱敏回显）。

所以这不是"把对方项目并进来"，而是**用对方的产品结构补 DKD 的三块短板**：配置台、工具中心、运行时间线。
对方那套"租客档案 / 生活照 / 合住偏好 / 同性匹配"是另一个业务域，搬进来等于在售货机平台里再养一条业务线。

---

## 二、逐项对照

| # | 对方能力 | DKD 现状 | 判定 |
| --- | --- | --- | --- |
| 1 | 管理员：用户管理、个人资料与改密 | RuoYi `system/user`、`/system/user/profile` 已有，含密码旧值校验、手机/邮箱唯一性 | **已有**，不用搬 |
| 2 | 租客档案 + 生活照审核 + 兴趣标签词表 | 无对应实体（DKD 是设备/点位/商品/工单） | **业务域不同**，不搬（见 §五） |
| 3 | 系统参数配置（7 个：调度轮数、单角色工具调用上限、同工具连续调用上限、放宽次数、默认交付人数、候选池写入上限、补画次数） | **39 个参数全在 `app/config.py` + 环境变量**，改一个要改 env + 重启；无后台页 | **缺**（上一轮审计附录 B 已确认） |
| 4 | AI 模型配置（4 条用途 + 连通性测试） | 单模型：`DKD_AGENT_LLM_MODEL/BASE_URL/TIMEOUT`，无用途区分、无测试按钮 | **缺** |
| 5 | Prompt 模板管理（4 套） | 硬编码在 `app/prompts/assistant_chat.py`、`app/prompts/restock_calibration.py`，改文案要发版 | **缺** |
| 6 | 子 Agent 定义（编码/人格提示词/工具分组/启用开关） | **无"角色"抽象**：`chat_graph.py` 是"助手"，`restock_graph.py` 是固定管线，工具分组写死在 `tool_names_for_scene()` | **缺（架构级）** |
| 7 | 工具中心（注册/真启用开关/只读试调/调用日志） | 工具是 Python 静态函数；**无注册表、无开关、无调用日志、无试调接口** | **缺**（四样全缺） |
| 8 | 调度时间线 + 候选漏斗（Run/Step：派给谁、调了哪个工具、拿到几行、多久） | 只有 `agent_decision_log`（按"决策"记，无 run_id/step_no/tool/耗时/行数）；`agent_restock_plan` 有明细但无过程 | **部分缺** |
| 9 | 普通用户维护档案 → 待审核 | 无 | 业务域不同 |
| 10 | 合住偏好（必须/加分维度） | 无；DKD 对应物是补货服务水平/覆盖率参数 | 业务域不同 |
| 11 | 我的动态（画像素材） | `agent_conversation` + `agent_message` 已有对话存储 | 形态类似，**不用搬** |
| 12 | 智能匹配：唯一入口、后台运行、离开页面可恢复、回来恢复状态 | **已有且更强**：SSE + `Last-Event-ID` 断点续传、LangGraph checkpointer（SQLite/PG）跨进程恢复、`interrupt()` 人工确认点、restock 后台跑 | **DKD 领先** |
| 13 | 画像/候选/审核结果：每个结论带解释与原文明依据 | 补货建议有 `reason`，但是模板文本；**无"必须引用数据依据"的硬校验** | **部分缺**（可借鉴） |
| 14 | 代码兜底：放宽上限、交付人数现数、否决必须分类 | 已有同级或更强：系数夹取、容量 clamp、唯一键幂等、CAS 抢占建单、回调校验链、human-in-loop | **DKD 领先** |
| 15 | AI 配置数据库化 + 密钥脱敏（前 4 + 8 个星 + 后 4） | 无配置接口；无脱敏逻辑 | **缺**，但**密钥入库与 AGENTS §7.1 冲突**（见 §三） |
| 16 | 服务重启自动收尾"运行中"记录 | 用 checkpointer 恢复未完成图；但**没有 run 表**，页面无法显示"上次跑到哪一步/已中断" | **部分缺** |

---

## 三、三个硬冲突（直接照搬会推翻 DKD 的既有约束）

### 冲突 1：模型 API Key 入库 vs "密钥永不入库"

对方做法：模型连接参数 + Key 一起存库，接口返回脱敏（前 4 + `********` + 后 4），编辑时留星号表示沿用。

DKD 红线：`AGENTS §7.1` 任何新代码不得延续明文模式，密钥一律环境变量注入。

**融合方案**：`agent_llm_profile` 表只存**非密钥字段**（用途、provider、base_url、model、temperature、max_tokens、超时、启用），
新增 `api_key_env`（存环境变量名，如 `DKD_DEEPSEEK_API_KEY`）。接口回显"是否已配置 + 变量名"，永远不回显值。
"连通性测试"由 Java 侧发一次最小请求（或调用 Python 的 `/agents/llm/ping`），结果只回 `ok/latency/error_code`。

### 冲突 2："改完立即生效" vs 决策可复现

对方是热生效；DKD 的 `agent_decision_log` 要求能回答"当时为什么这么建议"。
参数一改，历史记录就无法复现。

**融合方案**：配置表加自增 `version`；每次 run 在 `agent_run.config_version` 记录快照。
热生效保留（运营体验），但**必须能按版本回放**。七个阈值参数里，`单轮工具调用上限`/`同工具连续调用上限`
与 DKD 已修过的 FIX-29（轮次按会话累计）是同一族问题，移植时复用现有实现，别另造一套。

### 冲突 3：四角色由一张图驱动 vs DKD 的两条图

DKD 的 `restock_graph` 依赖 `interrupt()` + 幂等副作用 + checkpointer，语义很脆（见该文件 docstring §1-§3）。
往里塞"主管调度 + 三个子角色"会直接打坏中断恢复。

**融合方案**：角色抽象**先落对话场景**（`chat_graph` 是现成的 ReAct 循环，加"角色=提示词+工具分组"最自然）；
`restock_graph` 保持单管线，最多把"校准"与"基线"拆成两个可开关的步骤，不动中断语义。

---

## 四、建议方案（业务不搬，能力移植）

### 4.1 目标结构

```
管理端（dkd-vue）                            Java（dkd-manage）                 Python（dkd-agent）
├─ 智能体配置  agentConfig/index.vue   →   AgentConfigController        →   Settings 从 DB 读 + 版本
│   ├─ 参数（7+ 个阈值）                     GET/PUT /manage/agent/config
│   ├─ 模型档案（4 条 + 连通性测试）          /manage/agent/llm-profile
│   └─ Prompt 模板（含变量校验）              /manage/agent/prompt
├─ 工具中心    agentTools/index.vue    →   AgentToolController          →   ToolRegistry 从 DB 读（含 enabled）
│   ├─ 注册/启停                             /manage/agent/tool
│   ├─ 只读试调（仅 read-only 工具）          /manage/agent/tool/{code}/try
│   └─ 调用日志                              /manage/agent/tool-call
└─ 运行时间线  agentRuns/index.vue     →   AgentRunController           ←   RunRecorder 写 agent_run/step/tool_call
    └─ 按 run 展开 step / 工具 / 耗时                       （Python 写自有表，不授 DELETE）
```

### 4.2 表（全部 `agent_` 前缀，留 MySQL，不搬 PG）

| 表 | 关键列 | 写入方 |
| --- | --- | --- |
| `agent_config` | `config_key`(uk) / `config_value` / `value_type` / `version` / 审计四件套 | **Java 写**，Python 只读 |
| `agent_llm_profile` | `purpose`(1助手 2补货校准 3诊断 4审核) / `provider` / `base_url` / `model` / `params` / `api_key_env` / `enabled` | Java |
| `agent_prompt_template` | `code` / `purpose` / `version` / `content` / `variables` / `enabled` | Java |
| `agent_tool` | `tool_code`(uk) / `group_code` / `display_name` / `read_only` / `enabled` / `sort` | Java |
| `agent_role` | `role_code` / `persona` / `tool_group` / `enabled`（阶段 3 才用） | Java |
| `agent_run` | `run_id`(uk) / `scene` / `trigger_type` / `status`(running/done/interrupted/failed) / `config_version` / 起止时间 | Python INSERT/UPDATE |
| `agent_step` | `run_id` / `step_no` / `role_code` / `action` / `summary` / `input_tokens` / `output_tokens` / 耗时 | Python INSERT |
| `agent_tool_call` | `run_id` / `step_no` / `tool_code` / `params_digest` / `row_count` / `duration_ms` / `result_status` | Python INSERT |

> 授权脚本要同步改：`docs/ddl/create_agent_db_user.sql` 给新表加 `SELECT`（配置类）与 `SELECT/INSERT/UPDATE`（运行类），**仍然不授 DELETE/DDL**。
> 所有 DDL 带回滚语句并归档 `docs/ddl/`，符合 AGENTS §7.4/§8。

### 4.3 关键设计取舍

1. **配置写入只走 Java**：管理动作有 `sys_oper_log`、有权限点、有事务；Python 只读，避免两处写同一个配置。
2. **密钥不进库**（冲突 1）：库只存 `api_key_env`；"脱敏回显"改成"已配置/未配置 + 变量名"。
3. **配置版本快照**（冲突 2）：`agent_run.config_version` 是回放的前提。
4. **工具注册表与代码同源**：DB 只存"元数据 + 开关"，工具实现仍在 `app/tools/*`；启动时校验"DB 里的 tool_code 都能在代码里找到"，找不到就拒绝启动（fail-fast），防止配置了一个不存在的工具。
5. **试调只允许 `read_only=1`**：写工具（建单/改库存）必须走 human-in-the-loop，不给"试调"口子。
6. **Run/Step 复用 decision_log 而不是并存**：`agent_decision_log` 加 `run_id`/`step_no` 两列即可挂到时间线上，避免两套留痕。

---

## 五、如果真的要搬"合租业务"本身

代价与边界（不建议，但先算清楚）：

- 新业务域 = 20+ 张 `tb_tenant_*` 表 + 一整套 CRUD + 权限菜单 + 前端模块，且有"生活照审核"这类内容审核链路；
- 与 DKD 现有销售/工单/库存无数据关系，等于平台里长出一条平行业务线；
- 对方的四角色图与 DKD 的补货图语义不同（一个做"筛选收窄"，一个做"基线→校准→建单"），不能共用状态机；
- 唯一可复用资产是 `ai_coliving` 的 Agent 编排代码与 UI 骨架，业务表基本要重画。

**建议**：把合租项目当作"多角色编排的参考实现"，能力移植、业务不搬。如果目标是作品集/面试演示，
更好的做法是在 DKD 里做一个**同构的第二智能体场景**（例如"设备故障诊断 → 派单 → 审核"三角色），
复用同一套 run/step/工具中心，展示"一个平台驱动多场景 Agent"的能力，而不是搬一套无关业务进来。

---

## 六、分期计划

| 阶段 | 内容 | 工时 | 验收 |
| --- | --- | --- | --- |
| P0 配置台 | `agent_config` + 版本快照 + 参数页；模型档案 + 连通性测试；Prompt 模板 + 变量校验 | 6-9 人日 | 改参数不重启生效且 run 记录版本；密钥仍只在 env；pytest/ruff + mvn + build 全绿 |
| P1 工具中心 | `agent_tool` 注册表 + 开关 + `agent_tool_call` 日志 + 只读试调接口 + 页面 | 6-8 人日 | 关掉某工具后下一轮模型确实看不到；调用日志可按 run 查 |
| P2 运行时间线 | `agent_run`/`agent_step` 两表 + 写入 + 回放页；重启时把 `running` 改 `interrupted` 并写原因 | 4-6 人日 | 一次补货分析能逐轮展开"派给谁/调什么/几行/多久"；重启后页面显示"已中断，可重跑" |
| P3 多角色 | `agent_role` + 对话场景的角色化（提示词+工具分组+开关）；补货图保持单管线 | 8-12 人日 | 三个角色可独立开关；同一套执行代码，角色只是"提示词+工具组" |

合计 **24-35 人日**。P0 先做，因为 P1/P2 都要读配置；P3 风险最高，放最后且限定在对话场景。

### 不建议照搬的

- 四套 Prompt 的具体文案（DKD 的补货口径、设备术语完全不同）；
- 密钥存库 + 明文脱敏回显（红线冲突）；
- "候选池写入上限/默认交付人数"这类业务阈值（DKD 没有候选池概念，对应物是"单设备最多货道数/单批计划数"）；
- 生活照/兴趣标签/合住偏好等业务表。

### 提醒：DKD 已有、别在移植时弄丢的

只读账号 + 表白名单 + 分批超时护栏；回调服务密钥 + 路径白名单 + 限流；`agent_restock_plan` 的唯一键幂等 + CAS 抢占 +
状态机；SSE 的 `Last-Event-ID` 断点续传；checkpointer 跨进程恢复；`agent_decision_log` 的审计留痕。

---

*本文档只做差距分析与方案，未改动任何代码。若要落 P0，按 AGENTS §9.1 拆任务后逐条实现并跑验证。*
