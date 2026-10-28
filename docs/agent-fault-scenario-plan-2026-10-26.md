# 故障诊断场景 · 实施计划与任务清单

> 日期：2026-10-26 ｜ 配套设计：`docs/agent-fault-scenario-draft-2026-10-26.md`
> 状态图例：⬜ 未开始 ｜ 🔄 进行中 ｜ ✅ 完成 ｜ ⛔ 阻塞 ｜ ⏸ 暂缓
> 说明：本清单是**故障场景增量**，工时未计入 `DKD智能体接入项目排期计划.md` 的 64 人日基线；
> 待确认排期后并入该文档并重新核算可用工日（AGENTS §4.3）。

---

## 一、已决策事项（记录在案，后续不再反复）

| # | 决策 | 依据 | 落地 |
| --- | --- | --- | --- |
| **D1** | **维修工单允许"停机"设备创建**（运行中 1 + 停机 2；未投放 0 / 已撤机 3 拒绝；补货/撤机仍只允许运行中） | 原规则导致"停机 → 修不了 → 永远停机"；故障场景派单必须先能建单 | ✅ F-00 已实现 |
| **D2** | 主管调度用**代码路由**，LLM 只在角色内解释与选择 | 3 个角色路由可枚举；代码路由可单测、可复现、零 token | 设计已定，F-06 落地 |
| **D3** | 模型 API Key **不入库**，库里只存 `api_key_env`（环境变量名） | AGENTS §7.1 密钥永不入库；对方项目"存库 + 脱敏回显"不可照搬 | F-14 落地 |
| **D4** | **不搬合租业务**（租客/生活照/偏好/同性匹配），只移植能力 | 业务域不同，搬进来等于平台里再养一条业务线 | — |
| **D5** | 两个场景**共享基座、不共享图**；不改 `restock_graph` | 它依赖 `interrupt()` + 幂等副作用 + checkpointer，塞角色会打坏恢复语义 | F-01..F-05 落地 |

---

## 二、任务清单

### F0 前置（业务规则）

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-00** | 维修工单设备状态例外：`DkdContants.VM_STATUS_STOPPED=2`；`checkCreateTask` 允许 1/2、拒绝 0/3/null；修正误写的拒绝文案；`==` 改常量 `.equals` | 0.5d | — | `TaskServiceRepairStatusTest` 6 用例 + 既有建单用例共 **18/18 通过**，`mvn BUILD SUCCESS` | ✅ 2026-10-26 |

> F-00 的验收证据（真实执行）：`mvn -pl dkd-admin -am test -Dtest=TaskServiceRepairStatusTest,TaskServiceCreateConflictTest,AgentTaskCreateServiceTest`
> → `Tests run: 18, Failures: 0, Errors: 0`，`BUILD SUCCESS`。

### F1 基座（两个场景共用）

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-01** | DDL：`agent_run` / `agent_step` / `agent_tool_call`（含索引、回滚语句、归档 `docs/ddl/`）；`create_agent_db_user.sql` 增量授权（配置类只 SELECT、运行类 SELECT/INSERT/UPDATE，无 DELETE/DDL） | 1.5d | F-00 | 回滚脚本可执行；授权矩阵在真实账号上复测 3 项拒绝 | 🔄 脚本就绪（2026-10-26）；**真库执行被阻塞**：本会话无 MySQL 口令（`DKD_DB_PASSWORD` 未注入、root 无空口令），需人工执行 `docs/ddl/agent_run_step.sql` + `create_agent_db_user.sql` |
| **F-02** | Python `app/obs/run_recorder.py`：写 run/step/tool_call，失败只 WARN 不阻断（同 `audit.py` 原则）；`request_id` 贯穿 | 1.5d | F-01 | 单测：写失败不抛；三表可查同一 `run_id` | 🔄 代码 + 10 个单测完成（2026-10-26）；"三表可查同一 run_id"需真库，待 F-01 执行后验 |
| **F-03** | Java `AgentRunController`：`/manage/agent/run/list`、`/{runId}`、`/{runId}/steps`；权限点复用 `manage:agentAudit:list/query` | 1.5d | F-01 | 接口返回与 DDL 一致；无权限 403 | ✅ 2026-10-26：Controller/Service/Mapper/XML 落地；`mvn -pl dkd-admin -am test`（含 4 个分组单测）BUILD SUCCESS |
| **F-04** | 前端 `views/manage/agentRun/index.vue`：列表 → 展开 step → 展开工具调用（角色/动作/行数/耗时）；两场景共用 | 2d | F-03 | 手动造两条 run 可逐轮回放 | ✅ 2026-10-26：页面 + `api/manage/agentRun.js` + 菜单 SQL 完成；`npm run build:prod` **exit 0**；真库菜单待执行 |
| **F-05** | 启动扫描：`status='running'` 超 N 分钟 → `interrupted` + `stop_reason='服务重启，可重新发起'` | 0.5d | F-02 | 杀进程重启后页面显示"已中断" | ✅ 2026-10-26：lifespan 已接入 + 单测；F-04 后页面可展示 interrupted（运行时手测待启动服务） |

### F2 诊断官

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-06** | `fault_state.py` + `fault_graph.py` 前半段：取数（复用 `read_tools`）→ `compose_diagnosis()` → 落 step | 2d | F-02 | 真库跑一台设备出结构化诊断 | ✅ 2026-10-27：`fault_state.py` + `fault_graph.py`（supervisor + 诊断官，6 次只读工具）；真库 A1000001 两次运行（24h 窗口：0 证据/不可下结论；35000h 窗口：11 条证据/高置信/可派单） |
| **F-07** | 证据硬校验：`evidenceId` 必须映射到本轮 `tool_call` 返回行，映射不上丢弃；全丢则 `cannotConclude=true` 且**禁止生成派单提案** | 1d | F-06 | 单测覆盖"越界丢弃""全丢不可派单""部分有效" | ✅ 2026-10-27：`trace_evidence()` + 9 个单测（行不存在 / 工具未调用 / row_count 不一致 / 在途集合不一致 / 历史 total 不一致 / 全丢禁派单）；真库证据带 `evidenceId → toolCallId`（如 e1→s1c2） |

### F3 派单官

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-08** | 提案生成：接单人（区域匹配 + 在途数）、优先级、工单描述；必须引用 `basedOnEvidence` 与 `assigneeBasis` | 2d | F-07 | 单测：缺依据拒绝生成；引用越界拒绝 | ✅ 2026-10-28：`build_dispatch_proposal` / `validate_proposal_refs` + 派单节点（step 2）；9 个新单测；真库 `fault-8ecd8058c359` 提案落库（派给=孙权、优先级 2、依据 12 条、run=`waiting_confirm`） |
| **F-09** | `interrupt()` 生成提案 → `POST /runs/{id}/confirm` → 回调 `/agent/callback/task`（`productTypeId=3`）→ 幂等 | 2.5d | F-08 | 重复确认不重复建单；未确认不建单；拒单回写状态 | ✅ 2026-10-28：确认核心 + HTTP 接口 + 网关权限 + **前端确认/驳回/发起诊断**（两阶段 DB 状态机，见 D11）。证据：10+10 Python 单测、Java 31 项、真库建单 `taskId=579` 且二次确认幂等、HTTP 驳回未建单、`npm build:prod exit 0`；**浏览器交互未验到**（手测清单见交付说明） |

### F4 审核官

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-10** | `get_task_detail`（读 `tb_task`/`tb_task_details`）+ `check_repair_effective`（修复后窗口订单恢复）+ 四分类结论（`fix_verified/not_fixed/wrong_dispatch/evidence_insufficient`） | 2.5d | F-09 | 四类各有单测；`not_fixed` 必须点名证据 | ⬜ |

### F5 配置台 / 工具中心（上一版 P0/P1）

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-11** | `agent_config` 表 + `version` + Java 读写接口（`manage:agentConfig:edit` + `@Log`）+ run 记录 `config_version` | 2d | F-01 | 改参数不重启生效，且 run 可回放当时版本 | ⬜ |
| **F-12** | `agent_tool` 注册表（`tool_code/group/read_only/enabled/sort`）+ 组装 `tool_list` 时过滤 + 启动校验"DB 工具必须在代码里存在"（fail-fast） | 2d | F-01 | 关掉某工具后模型确实看不到；配了不存在的工具则拒启动 | ⬜ |
| **F-13** | 工具中心页 + 只读试调接口 + 调用日志查询 | 2.5d | F-12 | 写工具不可试调（403）；日志可按 run 查 | ⬜ |
| **F-14** | 配置台页：系统参数 / Prompt 模板（变量校验）/ 模型档案（`api_key_env`，永不回显密钥）+ 连通性测试 | 3d | F-11 | 密钥不出现在任何接口响应里；测试只回 ok/latency/错误码 | ⬜ |

### F6 触发与验收

| 任务 | 内容 | 工时 | 依赖 | 验收 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **F-15** | 定时巡检触发（quartz，对异常设备批量发起；复用 `agent_restock_job.sql` 注册方式） | 1.5d | F-09 | 任务失败写 `sys_job_log` 且可告警；开关关闭记 WARN 不报失败 | ⬜ |
| **F-16** | 真库 e2e：诊断 → 确认 → 建单 → 审核；三表可查、页面可回放；脚本带写入护栏与退出码约定（0 全绿 / 1 失败 / 2 未验到） | 2d | F-10 | 一次完整闭环通过 | ⬜ |
| **F-17** | 文档同步：方案/排期互引版本号；DDL 归档；接口补 Swagger 注释 | 1d | 全部 | 文档与实现一致 | ⬜ |

**合计 31.5 人日**（不含评审与联调缓冲）。

---

## 三、里程碑与交付物

| 里程碑 | 任务 | 工时 | 交付物 |
| --- | --- | --- | --- |
| **M1 基座** | F-01 ~ F-05 | 7d | ✅ 2026-10-26：三张运行表 + recorder + 时间线接口与页面 + 重启收尾（真库已执行） |
| **M2 诊断官** | F-06 ~ F-07 | 3d | ✅ 2026-10-27：一台设备出诊断结论 + 证据回溯 + 时间线（真库两次运行） |
| **M3 派单官** | F-08 ~ F-09 | 4.5d | ✅ 2026-10-28：提案 + 人工确认 + 回调建单 + 幂等 + 接口/网关/前端（浏览器交互待人工冒烟） |
| **M4 审核官** | F-10 | 2.5d | 工单完成后复核与四分类结论 |
| **M5 配置台/工具中心** | F-11 ~ F-15 | 11d | 运营可调参数、工具开关、模型/Prompt 管理 |
| **收尾** | F-16 ~ F-17 | 3d | e2e + 文档 |

**演示节点：M1 + M2 = 10 人日**（可先交付这一段）。

---

## 四、全局约束（每个任务都要满足）

- Java 8 语法（禁 var/record/List.of）；Controller 不做业务逻辑；写操作 `@PreAuthorize` + `@Log`。
- Python：ruff line-length 100、公开函数类型注解、禁 print；LLM 调用一律打桩，**测试不连真库/真 API**。
- 写操作仍只走 `/agent/callback/task`（服务间密钥 + 白名单 + 限流），**Python 不得直写 tb_\***。
- 密钥只在环境变量；配置表只存 `api_key_env`；日志/响应不出现明文。
- DDL 带回滚并归档 `docs/ddl/`；授权脚本同步更新，仍不授 DELETE/DDL。
- 验收命令：`pytest -q`、`ruff check .`、`mvn -pl dkd-admin -am test`、`npm run build:prod`，全绿才算完成。
- 端到端脚本必须带写入护栏，且护栏读数跨事务；环境类拦截打印"未验到"并用退出码 2（AGENTS §10）。

---

## 五、决议与"防遗忘"登记

### 5.1 已拍板（2026-10-26，用户确认"按建议来"）

| # | 原问题 | 决议 | 为什么这么定 | 验证方式 / 复查触发点 |
| --- | --- | --- | --- | --- |
| **D6** | Q1 `vm_status=2` 字典口径 | **已连库核对（2026-10-26）**：`sys_dict_data` 的 `vm_status` 只有 **0-未投放 / 1-运营 / 3-撤机，没有 2**；`tb_vending_machine` 实际数据也只有 0（4 台）与 1（11 台），全仓没有把 `vm_status` 置为 2 的代码。结论：**"停机"在本项目尚未建模**。保留 `VM_STATUS_STOPPED=2` 作为**预留**（将来若把停机建模为 2，校验不用再改）；若业务说的"停机"其实是设备运行异常（`running_status.status=false`）而 `vm_status` 仍为 1，则维修单本来就允许，无需例外 | 若产品确认要一个真正的"停机"状态，需要同时补：①字典 `vm_status` 加 2-停机；②设备管理页可设置该状态；③确认停机设备是否仍允许补货/撤机（当前只放开维修，补货仍要求运行中） | 复查触发：产品确认"停机"口径时；或首次出现 `vm_status=2` 的数据时 |
| **D7** | Q2 建维修单后是否改设备状态 | **首期不改**，建单不动 `tb_vending_machine.vm_status` | 改状态会牵动订单/报表口径；先与现状保持一致，观察后再定 | 复查触发：审核官上线后若"停机→修好→仍停机"导致无法恢复销售，再评审 |
| **D8** | Q3 审核官触发时点 | **先做页面点「复核」**；定时拉取放 F-15 | 页面触发可先跑通闭环，且不引入定时任务告警口径 | F-10 完成后，若运营反馈"忘记点复核"，再把 F-15 提前 |
| **D9** | Q4 工具开关是否对补货场景生效 | **一致生效**（同一套 `agent_tool` 注册表） | 两套开关必然漂移；补货场景也要能关工具 | F-12 完成时补一条补货侧回归：关工具后补货图也看不到 |
| **D10** | Q5 何时并入排期计划 | **待排期窗口确认后**再并入 `DKD智能体接入项目排期计划.md` 并重算可用工日 | AGENTS §4.3 要求双文档互引 + 重算；现在并表会污染 64 人日基线 | 复查触发：本计划 F-17 执行前必须完成并表 |
| **D11** | F-09 的 `interrupt()` 实现方式 | **改用「提案落库 + 独立确认调用」两阶段 DB 状态机**（`waiting_confirm → running → done`），不引入 checkpointer interrupt | ① 确认来自独立 HTTP 请求，天然跨进程；DB 状态机本身即可协调，无需持久化图实例/thread_id；② 幂等真正兜底是"Java 同设备同类型在途工单防重 + claim 回查"，不是 checkpointer；③ 少一条需长期维护的恢复路径（AGENTS：不新增两套并存路径）。若将来出现多轮人工介入再评估 | 复查触发：审核官（F-10）若也要人工确认，或出现"确认后需回退再改提案"的需求时 |

### 5.2 防遗忘清单（每条都有触发点，不靠记忆）

| 事项 | 必须做的人/时机 | 记录位置 |
| --- | --- | --- |
| D6 字典核对 | 下次连库时（任何人执行一条 SQL） | 本表 + 计划 §六 进度日志 |
| D7 设备状态 | 审核官上线后第一次复盘 | F-10 验收记录 |
| D8 审核触发 | F-10 验收时确认运营是否能坚持手点 | F-15 任务说明 |
| D9 补货侧回归 | F-12 完成当天 | F-12 验收记录 |
| D10 排期并表 | F-17 开工前（未并表不得宣称计划完成） | 排期文档版本记录 |
| Q2 备忘 | 代码注释里留 `TODO(F-09 2026-10-26): 建维修单不改设备状态，见计划 D7` | `TaskServiceImpl` / 派单回调实现处 |

> 以上 6 条已同步到 `AGENTS.md` 不需要（属项目内决策，不属开发规范）；但**任何一条被违反时，必须在进度日志里写一条"决策变更"**，不允许悄悄改。


---

## 六、进度日志

| 日期 | 事件 |
| --- | --- |
| 2026-10-26 | 设计草案完成（`agent-fault-scenario-draft-2026-10-26.md`）；F-00 决策并实现，单测 18/18 通过 |
| 2026-10-26 | **决议落盘**：D6~D10 + 防遗忘清单（§五）；用户确认"按建议来" |
| 2026-10-26 | **M1 开工**：F-01 脚本（DDL + 授权增量）、F-02 写入器（10 单测）、F-05 启动扫描完成；`ruff` 全过、`pytest` 全量 431 项通过 |
| 2026-10-26 | **M1 收口**：F-03 接口 + F-04 页面（`mvn BUILD SUCCESS 21 项`、`npm build:prod exit 0`）；真库 3 条 SQL 待执行 |
| 2026-10-26 | **踩坑修复**：DBeaver 执行菜单脚本报 `ERROR 1064 near '?…'` —— GBK 客户端把 `AS 检查项` 转成 `AS ?`。三个脚本已加 `SET NAMES utf8mb4;`，SQL 内非引号中文（列别名）改 ASCII，并在 §6.2 标注执行必须带 `--default-character-set=utf8mb4` |
| 2026-10-26 | **真库执行完成**：三张运行表建成、`dkd_agent` 增量授权生效、菜单 9010 建成；权限矩阵实测 7 项（写允许 / DELETE、DROP、跨白名单读、业务表写 全被拒）；Python 写入器端到端落库三表成功（摘要为 sha256，无原文）；**D6 结论：字典无 2，停机状态未建模** |
| 2026-10-27 | **M2 开工**：`fault_state.py` + `fault_graph.py`（supervisor + 诊断官，6 次只读工具）、证据回溯 `trace_evidence()`；新增 9 个单测 |
| 2026-10-27 | **M2 收口**：真库两次运行（24h 无证据 → 不可下结论、不派单；宽窗口 11 条证据 → 高置信可派单）；`pytest` 全量 440 项通过、`ruff` 全过 |
| 2026-10-28 | **M3/F-08 完成**：派单提案（`build_dispatch_proposal` / `validate_proposal_refs` / 派单节点 step 2）；9 个新单测；真库 `fault-8ecd8058c359` run=`waiting_confirm`、派给=孙权、优先级 2、依据 12 条 |
| 2026-10-28 | **M3/F-09 确认核心完成**：两阶段 DB 状态机（D11）+ `task_tools.create_repair_task`；10 个单测；真库建单 `taskId=579`、二次确认幂等返回同一单、设备状态未被改（D7）、决策留痕 1 条 |
| 2026-10-28 | **M3/F-09b 接口层完成**：`app/api/fault.py`（起跑 + 确认/驳回）、Java 网关白名单与权限点（起跑=`manage:vm:query`，确认=`manage:task:add`）、人工身份进 `agent_decision_log`；Java 31 项、Python 全量通过；HTTP 真库冒烟：起跑 200（12 证据/待确认）→ 驳回 200（step3 skipped、留痕 result=3、未新增工单） |
| 2026-10-28 | **M3/F-09c 前端完成**：时间线页新增「发起故障诊断」（`manage:vm:query`）、抽屉内派单提案卡片、「确认建单 / 驳回」（`manage:task:add`）、驳回原因必填；`npm run build:prod` exit 0；浏览器交互待人工冒烟 |
| 2026-10-28 | **M3 收口 + 发版**：F-08/F-09 全部完成，版本升 `0.3.0` 并打标注 tag；下一里程碑 F-10 审核官 |
| 2026-10-28 | **自查（基线 `921eb41` → 当前工作树）**：涉及 `dkd-vue`；新增 288 行/删除 6 行；发现 2 处（`handleView` 需抽 `loadDrawer` 供确认后复用、`faultProposal` 需兼容字符串/对象两种 evidenceJson）；修复 2 处；门禁 `npm run build:prod` exit 0 |

### 6.1 本次交付明细（M1 第一批，2026-10-26）

| 文件 | 内容 | 验证 |
| --- | --- | --- |
| `docs/ddl/agent_run_step.sql` | 三张表 + 唯一键/索引 + 回滚 + 验证语句 | ✅ 已在真库执行（2026-10-26），三表建成 |
| `docs/ddl/create_agent_db_user.sql` | §4 增量授权（三表 SELECT/INSERT/UPDATE，无 DELETE/DDL）；§10 实测清单 | ✅ 已执行；权限矩阵 7 项实测（见 §6.2） |
| `dkd-agent/app/config.py` | `obs_enabled` / `run_stale_minutes` 两个配置 | 默认 true / 30 分钟 |
| `dkd-agent/app/obs/run_recorder.py` | run/step/tool_call 写入器：白名单校验、失败只 WARN、参数只存 sha256 摘要、幂等 UPSERT、启动扫描 | 10 个单测 |
| `dkd-agent/app/main.py` | lifespan 调 `sweep_stale_runs`（失败不阻断启动） | 单测覆盖失败返回 0 |
| `dkd-agent/tests/conftest.py` | autouse 兜底关闭 `DKD_AGENT_OBS_ENABLED`（防单测写本机库） | 全量测试不再碰 DB |
| `dkd-agent/tests/test_run_recorder.py` | 正常/关闭/失败/非法参数/摘要/扫描 10 用例 | 10 passed |

**下一步**：M3 已收口（v0.3.0），进入 **F-10 审核官**（`get_task_detail` + `check_repair_effective` + 四分类结论；按 D8 先做"页面点复核"）。

### 6.4 F-09c 前端手测清单（**未验到，需人工冒烟**）

构建通过只证明能编译，不证明交互正确。按顺序手测：

1. 用有 `manage:vm:query` 的账号打开「AI 智能体 → 运行时间线」，点「发起故障诊断」，填 `A1000001`、窗口 `35000` → 提示"已生成派单提案，待人工确认"，列表多一条 `waiting_confirm`，抽屉自动打开；
2. 抽屉内应看到「派单提案」卡片（接单人/优先级/依据证据数/选人依据/说明），无权限账号**看不到**「确认建单/驳回」按钮；
3. 点「确认建单」→ 成功提示带工单号；重复点应提示"该运行已建单，返回原工单"（不产生第二张）；
4. 点「驳回」不填原因 → 提示必填；填原因提交 → 运行变 `done`、终止原因含"人工驳回"、**不产生工单**；
5. 确认 `waiting_confirm` 但提案不可执行（如无接单人）时**不出现**确认按钮；
6. 断网/停 Python 时点确认 → 拦截器提示错误、按钮 loading 复位、抽屉刷新后状态不变。


### 6.3 M2 收口（2026-10-27）

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| F-06 诊断官 | ✅ | `fault_state.py` / `fault_graph.py`（supervisor 代码路由 + 诊断官；6 次只读工具，全部走 `read_tools` 护栏）；真库两次运行见下表 |
| F-07 证据硬校验 | ✅ | `trace_evidence()`；9 个新单测（行不存在 / 工具未调用 / row_count 不一致 / 在途集合不一致 / 历史 total 不一致 / 全丢禁派单）；`ruff` 全过、`pytest` 全量 **440 项通过** |

**真库运行结果（设备 A1000001，2026-10-27 08:53）**：

| 窗口 | run_id | 置信度 | 证据 | 丢弃 | 可派单 | 终止原因 |
| --- | --- | --- | --- | --- | --- | --- |
| 24h | `fault-c2b081e893ce` | none | 0 | 0 | 否 | 证据不足或不可回溯，未生成派单建议 |
| ~4 年 | `fault-9fe9b4074c68` | high | **11** | 0 | **是** | — |

- 每次运行 6 次工具调用（档案 / 订单异常 / 库存异常 / 在途 / 历史 / 货道），三表按 `run_id` 可联查；
- 证据回溯样例：`e1 → s1c2`（step 1 的第 2 次工具调用 `list_order_anomalies`）；
- 24h 那次是**负向验证**：窗口内没有异常 → `cannotConclude=true`、`dispatch_allowed=false`，符合"证据不足不得派单"。

**为什么正向验证用 ~4 年窗口**：本机订单数据最早 2023-03，24h 窗口必然 0 条；换宽窗口覆盖真实异常订单（10 条 `status=1` 未出货），才能证明证据链路与置信度规则在真实数据上成立。生产默认窗口仍是 24h（`DEFAULT_WINDOW_HOURS`），由触发方按场景传。

**M3 前要注意**：24h 默认窗口对"历史积压异常"不友好，建议页面手工诊断允许选窗口（默认 72h）、定时巡检（F-15）仍用 24h。该条已记入 M3 任务说明。

### 6.2 M1 收口（2026-10-26）

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| F-01 DDL/授权脚本 | ✅ 已连库执行 | 三表建成；`dkd_agent` 增量授权生效（`SHOW GRANTS` 见三表 SELECT/INSERT/UPDATE）；权限矩阵 7 项实测通过 |
| F-02 Python 写入器 | ✅ 端到端验证 | 480 行 + 10 单测；真库跑一次 run/step/tool_call 写入，三表可按 run_id 联查，`params_digest=sha256:45a8…` 无原文 |
| F-03 Java 时间线接口 | ✅ | `AgentRunController/Service/Mapper/XML` + 4 个分组单测；`mvn -pl dkd-admin -am test` **BUILD SUCCESS（21 项）** |
| F-04 前端时间线页 | ✅ | `agentRun/index.vue` + `api/manage/agentRun.js` + 菜单 SQL（9010 已入库）；`npm run build:prod` **exit 0** |
| F-05 启动扫描 | ✅ | lifespan 接入 + 单测；页面已能展示 `interrupted` |

**真库执行记录（2026-10-26 15:09~15:14，已执行）**：

| 步骤 | 结果 |
| --- | --- |
| `agent_run_step.sql` | 成功；`agent_run / agent_step / agent_tool_call` 三表存在 |
| `create_agent_db_user.sql` | 成功；`SHOW GRANTS` 含三表 `SELECT, INSERT, UPDATE`，无 DELETE/DDL |
| `agent_run_menu.sql` | 成功；menu_id **9010**「运行时间线」挂在 9000 下，`component=manage/agentRun/index` |
| 权限矩阵（7 项） | ✅ 读 `agent_run`；✅ 事务内写 `agent_run`（回滚后 0 行）；✅ 读 `tb_inventory`；✅ 拒 DELETE(1142)；✅ 拒 DROP(1142)；✅ 拒读 `tb_report`(1142)；✅ 拒写 `tb_inventory`(1142) |
| 写入器 e2e | run=`verify-m1-20261026`、step 1 轮、tool_call 1 次，三表联查一致；`request_id` 为 NULL（无网关注入，符合纪律 3） |
| D6 字典核对 | `vm_status` 只有 0/1/3，**没有 2**；数据只有 0(4)/1(11)；代码无置 2 路径 → 见 §5.1 D6 |

> 残留数据：为验证写入器落了 1 条 `run_id='verify-m1-20261026'` 的测试运行（含 1 轮 1 次工具调用），可在时间线页直接看到。确认不需要后：
> `delete from agent_tool_call where run_id='verify-m1-20261026'; delete from agent_step where run_id='verify-m1-20261026'; delete from agent_run where run_id='verify-m1-20261026';`
> （`dkd_agent` 账号无 DELETE 权限，需用 root 执行。）

**如果今后重建环境**，执行命令必须带字符集（2026-10-26 实测踩坑：GBK 客户端会把 `AS 检查项` 变成 `AS ?` → `ERROR 1064`）：

```powershell
mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < docs/ddl/agent_run_step.sql
mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p < docs/ddl/create_agent_db_user.sql
mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < docs/ddl/agent_run_menu.sql
```

**M1 结论**：代码 5/5、真库 5/5 全部落地并通过验证（编译 21 项、pytest 431 项、前端构建 exit 0、权限矩阵 7 项、写入器 e2e）。**唯一遗留**：D6 的"停机"口径需产品确认（当前保留 `VM_STATUS_STOPPED=2` 作预留）；以及 9010 菜单要分配给角色（只给 list 权限可看列表，点「查看」还需 9009 的 query 权限）。



