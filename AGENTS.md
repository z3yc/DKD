# AGENTS.md — DKD 帝可得智能售货机平台 · 开发规范

> 本文档是本仓库所有人类开发者与 AI 编码助手（Copilot / Cursor / Claude Code / CodeBuddy 等）共同遵守的统一约定。
> 遵循优先级：**安全红线 > 数据一致性 > 测试门槛 > 本文档规范 > 个人习惯**。
> 配套文档：《DKD智能体接入方案-LangChain-LangGraph.md》《DKD智能体接入项目排期计划.md》（见 `docs/`）。

---

## 1. 项目概览（先看这里）

| 子系统 | 技术栈 | 位置 | 说明 |
| --- | --- | --- | --- |
| 管理后台 | **Java 8** / Spring Boot 2.5.15 / MyBatis / Druid / Redis / Maven 多模块 | `dkd-parent/` | RuoYi-Vue 3.8.7 改造，业务事实唯一所有者，端口 8080 |
| 运维移动端 | Java 8 / Spring Boot / MyBatis-Plus / JWT | `dkd-app/` | 与后台共用 `dkd` MySQL 库 |
| 管理前端 | **Vue 3.4 + Element Plus + Vite（JavaScript，非 TS）** | `dkd-vue/` | RuoYi-Vue3 前端 |
| 智能体服务（在建） | Python 3.11+ / FastAPI / LangGraph / SQLAlchemy(async) | `dkd-agent/`（规划，设计见 `docs/DKD智能体接入方案-LangChain-LangGraph.md` V1.1、排期见 `docs/DKD智能体接入项目排期计划.md` V2） | 旁路集成，读走 MySQL 只读账号、写走 Java REST 回调 |

**Maven 模块划分（dkd-parent）**：`dkd-admin`(启动/系统Controller) → `dkd-framework`(安全/AOP) → `dkd-system`(系统域) → `dkd-manage`(**核心业务域：设备/货道/商品/点位/订单/工单/库存**) → `dkd-common`(工具+AI包) → `dkd-quartz`(定时) → `dkd-generator`(代码生成)。

**核心架构约束（不可违背）**：
1. Java 系统是**业务事实唯一所有者**——所有写操作必须经 Java 服务的事务与校验链（如 `TaskServiceImpl.insertTaskDto` 的防重/状态/区域校验）；**dkd-agent 永远不得直连数据库写入**；
2. Agent 读数据走 MySQL **只读账号 + 表白名单**；写操作一律回调 Java REST（`AgentCallbackController`，服务间密钥鉴权）。**唯一例外**：智能体自有表（`agent_*`）可由 Agent 直接读写（不含 DELETE），它不属“业务事实”，详见 §7.3；
3. `agent.enabled` 开关必须保证 Python 服务不可用时主业务零影响（现有 `IAiService` 单轮链路兜底）；
4. Java 8 语法边界：**禁止使用 var / record / switch 表达式 / List.of() 等高版本特性**（编译目标 1.8）；
5. **基础设施实测边界（2026-09-21 核实，不得凭印象假设）**：
   - **业务表全部带 `tb_` 前缀**（`tb_inventory`/`tb_order`/`tb_task`/`tb_task_details`/`tb_inventory_log`/`tb_emp`/`tb_channel`/`tb_vending_machine`/`tb_node`/`tb_region`/`tb_sku`/`tb_job`/`tb_policy`/`tb_partner`/`tb_vm_type`/`tb_sku_class`/`tb_task_type`/`tb_role`/`tb_report`）——写 SQL、搞白名单、建 Agent 工具前先核 `dkd-manage/src/main/resources/mapper/manage/*.xml`；
   - **Redis 为 3.2.100（Microsoft 2016 移植版）**：无模块系统（不支持 RedisJSON/RediSearch）、内核 3.2 < 5.0（无 Streams）、`appendonly no`（仅 RDB）——**不得用它承载 LangGraph checkpointer，也不得用作 Stream 队列**（会话持久化用 SQLite/Postgres saver）；
   - **数据源只有单主库**（`application-druid.yml` 中 `slave.enabled: false`）：任何批量聚合/分析类查询必须限定时间窗口 + 分批 + 超时熔断，禁止无 LIMIT 的全表扫描；
   - **存量业务表 DDL 未入库**（`dkd-parent/sql/` 只有 RuoYi 系统表、quartz、`tb_report.sql`），涉及表结构工作时需先导出归档至 `docs/ddl/`。

**常用验证命令**（Windows，二选一 shell）：
```powershell
# PowerShell（推荐）
cd dkd-parent; mvn -pl dkd-manage -am compile -q        # 增量编译后端模块
cd dkd-vue; npm run build:prod                          # 前端构建（含语法检查）
# dkd-agent（建成后）
cd dkd-agent; .venv\Scripts\python.exe -m pytest        # 全量测试
.venv\Scripts\ruff.exe check .                           # lint 必须全过
```
> 当前项目尚无统一 CI，本地验证全绿是合并的前置条件（见 §10）。

---

## 2. 代码风格与命名约定

### 2.1 Java（dkd-parent / dkd-app，沿用 RuoYi 惯例，强制）

| 对象 | 规则 | 示例 |
| --- | --- | --- |
| 类-Controller | `XxxController extends BaseController`，URL 用 `@RequestMapping("/manage/xxx")` 小写复数 | `TaskController` → `/manage/task` |
| 类-Service 接口 | `IXxxService`（I 前缀） | `ITaskService` |
| 类-Service 实现 | `XxxServiceImpl implements IXxxService`，加 `@Service` | `TaskServiceImpl` |
| 类-Mapper | `XxxMapper` + 对应 `resources/mapper/**/XxxMapper.xml` | `TaskMapper` |
| 类-实体 | `Xxx`（与表名对应，见 2.4），字段手写 getter/setter 或 Lombok（dkd-parent 用手写保持 RuoYi 风格，dkd-app 允许 Lombok） | `Order` |
| DTO/VO | 出参用 `vo` 包、入参用 `dto` 包，类名后缀 `Dto` / `Vo` | `RestockSuggestionDto`、`TaskVo` |
| 方法-查询 | `selectXxxById` / `selectXxxList` / `selectByXxxAndYxx` | `selectInventoryById` |
| 方法-写入 | `insertXxx` / `updateXxx` / `deleteXxxByIds`（软删见 §7.4） | `insertTask` |
| 常量 | `static final`，全大写下划线，集中在 `common/constant` 包 | `VmStatusConstant.RUNNING` |
| 枚举 | code + name 双字段，提供 `getByCode` 静态方法 | `AiSuggestionType.OPERATION` |

其他硬规则：
- 每文件一个顶级类；方法超过 80 行必须拆分；
- Controller 只做参数校验 + 调 Service + 包装 `AjaxResult`，**禁止在 Controller 写业务逻辑**；
- Service 之间可以互相调用（`@Autowired` 接口），**Controller 不得直接调 Mapper**；
- 金额用 `BigDecimal`（禁 float/double）；时间用 `java.util.Date` + `DateUtils`（RuoYi 惯例）；布尔语义字段避免 `isXxx` 开头的 Lombok 陷阱。

### 2.2 Vue / JavaScript（dkd-vue）

- **项目无 TS，勿引入**：不要擅自添加 TypeScript 配置或 `.ts` 文件（与 RuoYi-Vue3 模板一致）；
- 组件命名：多词 PascalCase（`VmPanel.vue`），页面级组件保持 RuoYi 目录惯例 `src/views/manage/<域>/index.vue`；
- API 层：每个业务域一个 `src/api/manage/<域>.js`，函数名 `getXxx` / `listXxx` / `addXxx` / `updateXxx` / `delXxx`，与后端 REST 一一对应，**禁止在组件里手写 axios 直连**；
- 路由与权限：菜单走 RuoYi 动态路由（sys_menu 配置），不硬编码新路由；
- 渲染 AI/Markdown 输出必须过 `dompurify`（依赖已有）——`v-html` 仅允许渲染 sanitize 后的内容；
- 异步两条铁律：loading 状态必须 `finally` 复位；SSE/长请求必须可取消（AbortController）。

### 2.3 Python（dkd-agent，建成后生效）

- lint 以 **ruff** 为准（line-length=100），`noqa` 必须带原因注释；
- 公开函数签名必须带类型注解；Pydantic 模型用于一切对外契约（请求/响应/LLM 结构化输出）；
- 命名：模块 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`；LangGraph 节点函数用动词短语（`collect_inventory`、`compute_baseline`、`await_confirmation`）；
- Prompt 模板集中在 `app/prompts/` 按 Agent 分文件，**禁止硬编码在业务逻辑里**（吸取现有 `AiServiceImpl` switch-case 硬编码的教训）；
- 禁止 `print`，统一 `logging`；禁止同步阻塞调用进 async 路由（用 `run_in_executor`）。

### 2.4 数据库

- 表名前缀按 RuoYi 惯例：系统表 `sys_`，**业务表 `tb_`**（`tb_task`/`tb_inventory`/`tb_order`/`tb_vending_machine`…，完整清单见 §1 约束 5），**Agent 新表统一 `agent_` 前缀**；
- 列名 snake_case；审计列固定四件套：`create_time` / `update_time` / `create_by` / `update_by`；删除标记 `del_flag`（RuoYi 惯例，char '0'/'1'）；
- 唯一约束、索引变更必须附在 DDL 文件中并经评审（见 §7.4）。

---

## 3. 目录结构与模块边界

```
F:\DKD\
├─ dkd-parent\                 # Maven 多模块（模块职责见 §1，禁止跨职责放代码）
│  └─ dkd-manage\
│     ├─ controller\           # 只做参数校验+调用Service
│     ├─ service\ + service\impl\
│     ├─ mapper\
│     ├─ domain\               # 实体（表映射）
│     │  ├─ dto\               # 入参
│     │  └─ vo\                # 出参
│     └─ resources\mapper\     # MyBatis XML，与 Mapper 接口同名同目录结构
├─ dkd-app\                    # 独立 Spring Boot，只含运维作业域，勿塞管理功能
├─ dkd-vue\
│  └─ src\
│     ├─ api\manage\           # 按业务域分文件
│     ├─ views\manage\<域>\    # 页面
│     ├─ components\           # 仅全局通用组件
│     └─ store\                # Pinia 全局状态
├─ dkd-agent\                  # （规划）Python 服务，结构见方案 §5.3
└─ docs\                       # 方案、排期、DDL、原型
```

边界规则：
1. **业务代码只进 `dkd-manage`**：新的售货机业务功能不往 `dkd-system`/`dkd-admin` 塞；通用工具进 `dkd-common`；
2. 跨子系统共用的表结构变更，需同步检查 `dkd-app` 的同名实体与 Mapper；
3. 前端业务组件放对应页面目录下，全局组件才进 `src/components`；
4. **`target/` 目录是构建产物**：AI 助手不得读取或修改 `target/` 下文件作为依据（读 `.class` 无意义），一切以 `src/` 为准；
5. `dkd-manage` 中现有 AI 包（`com.dkd.common.ai`）为降级兜底链路，重构 Agent 时**保留不删**。

---

## 4. 注释与文档

### 4.1 Java

- 类头：作者 `@author ruoyi`（沿用模板）+ `@date`，简述职责；
- 公共方法必须有 Javadoc：参数 `@param`、返回 `@return`、抛出 `@throws`；
- **复杂业务逻辑必须写"为什么"注释**（如 `TaskServiceImpl` 里的工单校验链——每条校验的失败后果要写清）；
- 临时调试代码、TODO 必须带负责人与日期：`// TODO(zyc 2026-09-20): 促销因子尚未接入预测`；
- 禁止提交被注释掉的死代码（删掉，Git 会记住）。

### 4.2 前端 / Python

- Vue：组件顶部块注释说明用途与数据来源接口；复杂 computed/watch 写单行注释；
- Python：模块 docstring + 关键函数 docstring；LangGraph 节点函数必须注明该节点在图中的位置与输入/输出 state 键。

### 4.3 文档同步义务

- 接口变更（新增/改参数）→ 同步更新 `docs/` 下接口文档或在 Swagger 注解补全；
- 表结构变更 → DDL 脚本归档至 `docs/ddl/`，注明执行环境与是否可回滚；
- Agent 策略/参数变更（预测窗口、服务水平系数）→ 记入决策留痕设计说明，便于复盘归因；
- **排期/方案变更 → 两份文档（方案、排期）必须同步改版并互相引用版本号**；排期变更需附「可用工日」重新核算（扣除法定假期）。

---

## 5. Git 分支管理与提交规范

### 5.1 分支策略

- 分支：`main`（稳定，对应生产）/ `develop`（集成分支）/ `feature/*` / `fix/*` / `hotfix/*`；
- 新功能从 `develop` 切出 `feature/<模块>-<功能简述>`（如 `feature/agent-gateway`、`feature/restock-workbench`）；线上紧急修复从 `main` 切 `hotfix/*`，合并后同步回 `develop`；
- 禁止个人命名分支（`zyc-dev` 这类）；功能分支超过 3 天必须 rebase/merge 一次 `develop`；
- `main` / `develop` 的合并与推送需项目负责人明确批准（AI 助手**不得自行执行 push/merge**，只做本地 commit）。

### 5.2 提交信息（Conventional Commits，中文描述）

格式：`type(scope): 描述——补充说明`，说明部分写"为什么/防什么"：

| type | 用途 | 示例 |
| --- | --- | --- |
| `feat` | 新功能 | `feat(manage): 补货建议支持人工调整数量` |
| `fix` | 修 bug | `fix(task): 补货工单防重校验增加状态过滤` |
| `refactor` | 重构（不改行为） | `refactor(ai): 提示词拼接抽离为模板方法` |
| `docs` | 文档 | `docs: 新增智能体接入方案与排期` |
| `test` | 测试 | `test(manage): 库存预警阈值边界用例` |
| `chore` | 配置/依赖 | `chore(config): 密钥外置为环境变量` |

scope 建议：`manage` / `system` / `common` / `app` / `vue` / `agent` / `ai` / `config` / `ddl`。

**一次提交 = 一个问题**：禁止"顺手改"混入无关内容；修复类提交必须能关联一个 bug 描述（issue 或 commit 说明里写清复现路径）。

---

## 6. 错误处理与日志规范

### 6.1 Java

- 异常分层：Mapper/外部调用抛原始异常 → Service 转业务异常（RuoYi `ServiceException`，带用户可读 message）→ Controller 层由全局异常处理器统一转 `AjaxResult.error`；
- **禁止捕获后静默吞掉**（catch 空块直接吞异常是最严重的违规之一）；
- 事务：Service 写方法加 `@Transactional`；跨表写必须在同一事务内（参照 `TaskServiceImpl.insertTaskDto`：工单+明细+库存一次入库）；
- 日志：用 `private static final Logger log = LoggerFactory.getLogger(Xxx.class)`；WARN 及以上必须带上下文（关键参数或 `e` 堆栈）；现有 `AiServiceImpl` 的"打印请求/响应排查报错"是调试期产物，**日志中禁止输出完整 LLM 请求体（可能含用户数据与密钥）**，正式规范只记 model/duration/状态码/token 数。

### 6.2 dkd-vue

- axios 响应拦截器统一处理 code!=200；页面级错误必须 toast/ElMessage 反馈，禁止静默失败；
- 前端不打敏感日志（token、用户手机号）。

### 6.3 dkd-agent（建成后）

- 异常链：工具执行异常由编排层兜底转为结果消息（**不抛穿 LangGraph 循环**）；LLM 调用失败统一转 `LLMError` → API 层映射 502；
- 日志分级同通用标准；WARN 以上必须带 `request_id` + `user_id`（由网关注入的 `X-Agent-User` 头获得）；
- **每次写操作决策必须留痕 `agent_decision_log`**（输入上下文/LLM 输出/动作/结果/置信度）——这是审计要求，不是可选项。

### 6.4 可观测基线

- 请求入口生成 `request_id` 贯穿 网关→Python→工具→回调 全链路；
- 定时任务（dkd-quartz → Agent 分析）失败必须有告警，不允许"半夜悄悄失败"。

---

## 7. 安全红线（违反即阻塞合并，AI 助手生成代码同样适用）

1. **密钥永不入库**：
   - 现状（2026-09-21 核实）：`application.yml`/`application-druid.yml`/`dkd-app application-dev.yml` 中的 OSS AK/SK、DeepSeek API Key、DB/Redis 密码、JWT secret **已改为 `${ENV}` 占位符**并新增 `.env.example`（Druid 控制台弱口令亦已占位符化）；
   - **存量风险现状（2026-09-21 复核修正）**：① 配置外置改造**已提交**（初始提交已重写为 `3e1f4a8`，`main` 与 `origin/main` 的**可达历史中已无明文密钥**）；② 旧提交对象已按 14c 清理完毕（`git reflog expire --expire=now --all && git gc --prune=now` 后**未可达对象 0、对象库密钥命中 0**）；但凭据在重写前已进入过版本历史，**轮换仍属必须项**——实测旧 DeepSeek Key 已失效（HTTP 401）；`dkd_agent` 库账号已于 2026-09-21 轮换并复测通过（旧口令 1045）；**OSS AK/SK、MySQL/Redis 服务端口令、JWT secret、Druid 口令待轮换**，完整步骤/影响面/回滚见 `docs/security-credential-rotation.md`（14b）；③ JWT secret 已替换为新随机值（原 RuoYi 模板弱密钥 `abcdefghijklmnopqrstuvwxyz` 废弃），**轮换 JWT secret 会使所有在线用户掉线**，生产变更需协调值班窗口；④ 本机开发凭据集中存放于 `.env`（已 gitignore），**禁止把值写回任何入仓文件**；
   - 任何新代码不得延续明文模式；新增密钥一律环境变量/启动参数注入，模板文件（`application-*.example.yml`）写占位符。AI 助手在输出配置示例时必须用 `${OSS_ACCESS_KEY}` 占位，禁止照抄真实值；
2. **SQL 注入**：MyBatis XML 中 `${}` 仅允许用于排序字段等已白名单化的场景，其余一律 `#{}`；新增任何拼 SQL 代码必须评审；
3. **Agent 读写分离**（本项目特有，最高优先级）：dkd-agent 对 MySQL 采用**两级授权账号**（建权脚本 `docs/ddl/create_agent_db_user.sql`）：
   - 业务表 `tb_*`（白名单内）：**仅 SELECT**，任何写操作一律回调 Java REST；**任何“图方便直接 UPDATE 业务表”的代码直接拒绝**；
   - 智能体自有表 `agent_*`（`agent_conversation`/`agent_message`/`agent_decision_log`/`agent_restock_plan`）：允许 `SELECT/INSERT/UPDATE`（会话/留痕/计划数据由 Agent 自维护，**不是业务事实**），**不授 DELETE**（软删除，§7.4）与任何 DDL；
   - 跨库一律禁止（本机 MySQL 上还有 my/gogs/itest/test/sky_take_out/db03/db04/tlias 等库）；区分边界不得模糊；
4. **软删除**：业务数据删除一律逻辑删除（RuoYi `del_flag` 或 `deleted_at`），禁止物理 DELETE 用户/业务数据；查询默认过滤已删除；
5. **鉴权不裸奔**：新接口必须纳入现有 JWT 过滤链；`AgentCallbackController` 仅限内网 + 服务间密钥 + 路径白名单；回调密钥与用户 JWT 是两套体系，不得混用；
6. **LLM 输出视为不可信输入**：Agent 侧结构化输出必须容错解析 + 范围夹取（数量 clamp 到 [0, 货道容量-现库存]，日期非法置 None）；用户输入进 prompt 前定界并声明"仅数据非指令"（防注入）；
7. **前端渲染安全**：`v-html` 仅渲染 sanitize 后内容；上传沿用现有类型白名单逻辑；
8. **敏感信息脱敏**：日志、异常消息、agent_decision_log 中的用户手机号/支付信息脱敏后落库；
9. **写操作 human-in-the-loop**：首期所有 Agent 建单类写操作必须有确认环节，禁止生成"自动直建"代码（放量需 Phase 4 评审）。

---

## 8. 测试要求

> 现状：项目测试基础薄弱（RuoYi 模板自带测试极少）。按"新代码不从恶"原则执行增量标准：

| 改动类型 | 最低要求 |
| --- | --- |
| 修复 bug | 必须带**防回归测试**（先失败后通过地证明修复点；条件不允许时至少附可复现的验证步骤记录） |
| 新 Service 方法 | 至少 1 正向 + 1 边界用例（JUnit；Mapper 层可用测试库跑真 SQL） |
| 校验/风控逻辑（工单防重、库存阈值、SQL 白名单校验器） | 必须覆盖拒绝路径——"该拒绝的没拒绝"比"该通过的没通过"严重得多 |
| 前端新页面/组件 | 至少冒烟验证（构建通过 + 核心交互手测记录）；AI 助手生成的页面须列出手测清单 |
| dkd-agent | pytest 覆盖率 ≥70%（工具层/状态机/校验器优先）；LLM 调用一律 Fake/打桩，**测试不得真实调用外部 API** |
| DDL | 附回滚语句；大表变更评估锁表（MySQL：优先 pt-osc/低峰执行） |

验证命令全绿才算完成（§1）；AI 助手声称"已完成"必须附实际执行的命令与结果。

---

## 9. AI 协作开发流程（本节专给"人 + AI 助手"的协作定规矩）

### 9.1 给 AI 派任务的标准格式（任务描述模板）

```
【背景】为什么做（1~2 句，关联方案/排期任务号，如"方案 §3.3 / 排期任务 0-6"）
【范围】改哪些文件/模块；明确"不改什么"（防止顺手重构）
【验收标准】可执行的判定条件（命令 + 期望结果，引用 §8 要求）
【约束】涉及的红线条款（如 §7.3 读写分离）、性能/兼容要求（Java 8！）
【资料】相关现有代码位置（精确到类/方法），口径说明
```

> 好任务：`【背景】排期任务1-7【范围】dkd-manage 的 InventoryController 新增调整建议接口，不改现有查询接口【验收】mvn compile 通过 + Service 校验跳过原因必填的负向用例【约束】Java 8；写操作走 Service 事务`
> 坏任务："帮我把补货这块完善一下"（AI 会自由发挥，产出不可控）。

### 9.2 AI 生成代码的验收标准（人工审查清单）

- [ ] **能编译/构建**：`mvn compile` / `npm run build` / `ruff + pytest` 全过（AI 自报 + 人工抽查）
- [ ] **风格一致**：命名符合 §2（I*Service/XxxController 等），不像"另一个项目来的代码"
- [ ] **没有越界**：未引入未声明依赖、未升级 Java 版本特性、未改任务范围外的文件（`git diff --stat` 检查）
- [ ] **红线核对**：§7 逐条过（尤其：无硬编码密钥、无 `${}` 拼接、无直连写库、删除是软删）
- [ ] **错误处理完整**：无空 catch、异步有 finally、失败有用户可见反馈
- [ ] **测试**：修复带回归测试；拒绝路径覆盖
- [ ] **注释说了"为什么"**：AI 生成代码的注释须解释意图而非复述代码

### 9.3 AI 助手的行为边界

- **只读不猜**：改代码前必须先读相关现有类（Controller→Service→Mapper 链路），禁止凭 RuoYi 通用印象猜本项目的表结构/字段名；
- **不碰**：`target/`、`.idea/`、密钥值、`main`/`develop` 的 push、生产配置；
- **不隐瞒**：不确定的依赖、可能破坏的调用方、绕过的校验，必须在产出说明中显式列出"风险与未验证项"；
- **一次一件事**：AI 单轮输出对应一次可提交的改动；大任务由人拆解为 §9.1 格式的子任务序列（对照排期清单任务号）；
- 生成 SQL/DDL 时必须给出执行环境提示与回滚语句；生成定时任务时必须带失败告警说明。

### 9.4 人工开发者义务

- 提交 AI 生成代码前，本人对代码负全责（署名提交，禁止"AI 写的我没看"）；
- 每个里程碑结束（对照排期 M1~M4）更新 `docs/` 对应文档，并把 AI 协作中发现的规范缺口回写进本文档。

---

## 10. 代码审查（CR）准入标准

所有合并到 `develop`/`main` 的改动须满足：

- [ ] 提交粒度与 message 符合 §5
- [ ] §1 验证命令全绿，无新增编译警告
- [ ] 新代码有对应测试（§8 增量标准），修复带回归用例
- [ ] §7 安全红线逐条核对无违反
- [ ] 无 N+1 查询（列表接口循环内查库是重点检查项）、无明显性能退化；分析类/批量查询必须限定时间窗口 + 分批 + 超时，**禁止 `date_format()` 等方式包裹索引列导致全表扫描**（参照 `OrderMapper.xml:44` 反例）
- [ ] 事务边界正确（跨表写同事务；无"半个工单"风险）
- [ ] 日志无敏感信息、异常不静默
- [ ] 模块边界清晰（Controller 无业务逻辑、业务代码在 dkd-manage）
- [ ] Agent 相关改动：读写分离未破坏、决策留痕已接、降级开关仍有效

---

## 11. 快速自查清单（提交前 30 秒）

- [ ] 构建全绿（mvn / npm / ruff+pytest）
- [ ] 一个提交 = 一个问题，message 符合 §5
- [ ] 无新增硬编码密钥/密码；配置示例用占位符
- [ ] SQL 全部 `#{}`（`${}` 需白名单理由）
- [ ] 表名带 `tb_` 前缀（业务表）；分析类查询有时间窗口 + LIMIT
- [ ] 删除是软删除；查询过滤 del_flag
- [ ] 修复带回归测试；拒绝路径有覆盖
- [ ] DDL 有回滚语句并归档 docs/ddl
- [ ] 日志带上下文、无敏感明文
- [ ] Agent 改动：写操作走 Java 回调 + 人工确认 + 留痕
- [ ] 未 push/merge main、develop（需批准）

---

*版本：V1.1（2026-09-21）。本次回写（§9.4）：① 修正业务表前缀为 `tb_`（§1 约束 5、§2.4，原描述“业务表无前缀”与实际不符）；② 新增§1 约束 5「基础设施实测边界」（Redis 3.2.100 不可作 checkpointer/Stream、单主库无从库、存量 DDL 未入库）；③ §7.1 更新密钥现状为“已占位符化但未提交 + Git 历史含密钥需轮换清理”；④ §4.3 新增排期/方案同步义务。本文档随项目推进持续修订：AI 协作中发现的规范缺口按 §9.4 回写；dkd-agent 建成后在 §2.3/§6.3 补充实例。*
