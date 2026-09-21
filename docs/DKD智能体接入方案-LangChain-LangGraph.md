# 帝可得（DKD）智能售货机平台 LangChain/LangGraph 智能体接入方案

> 版本：V1.0（供评审）
> 日期：2026-09-20
> 范围：现有架构分析 → 智能体场景规划 → 集成方式 → 数据链路 → 技术实现路径 → 分阶段实施计划
> 说明：原型图与接入效果展示待本方案确认后另行输出。

---

## 一、现有系统架构分析

### 1.1 系统组成现状

| 子系统 | 技术栈 | 角色 | 关键信息 |
| --- | --- | --- | --- |
| dkd-parent | Spring Boot 2.5.15 + Java 8 + MyBatis + Druid + Redis + PageHelper | 管理后台（运营方使用） | RuoYi-Vue 3.8.7 改造，端口 8080，7 个 Maven 模块 |
| dkd-app | Spring Boot + MyBatis-Plus + JWT | 运维人员移动端 API（补货/维修作业） | 与后台共用 `dkd` MySQL 库，独立端口 |
| dkd-vue | Vue 3.4 + Element Plus 2.4 + Vite 5 + Pinia + ECharts | 管理前端 | RuoYi-Vue3 前端，axios 统一请求 |

**dkd-parent 模块划分：**

- `dkd-admin`：启动入口、系统管理 Controller（用户/角色/菜单/日志等）
- `dkd-framework`：安全（JWT Token 过滤器）、拦截器、AOP 配置
- `dkd-system`：系统域（用户、角色、部门、字典、日志）
- `dkd-common`：通用工具 + **已有的 AI 能力包 `com.dkd.common.ai`**
- `dkd-manage`：**核心业务域（本项目智能化的主战场）**
- `dkd-quartz`：定时任务调度
- `dkd-generator`：代码生成器

### 1.2 核心业务域与数据模型（dkd-manage）

| 业务域 | 核心实体 | 说明 |
| --- | --- | --- |
| 渠道与设备 | Region、Partner、Node、VendingMachine、VmType、Channel | 区域→点位→售货机→货道 四级结构，点位挂合作商分成 |
| 商品 | Sku、SkuClass、Policy | 商品、分类、售货策略（价格/折扣） |
| 交易 | Order、OrderDetailDto | 售货机产生的销售订单 |
| 作业 | Task、TaskDetails、TaskType、Job | 工单（投放/撤机/补货/维修），派发给运维人员，dkd-app 接单执行 |
| 库存 | Inventory、InventoryLog、InventoryAlertDto、RestockSuggestionDto | 货道级库存 + 变动日志 + 预警/补货建议（规则计算） |

**核心业务流程（闭环）：**

```
售货机投放 → 货道配置/商品上架 → 库存初始化(Inventory) → 用户扫码下单(Order)
→ 库存扣减(InventoryLog) → 库存预警(规则: 低库存/缺货, 1~3级) → 补货建议(RestockSuggestionDto 规则计算)
→ 人工创建补货工单(Task+TaskDetails, 校验: 设备状态/未完成工单/员工区域匹配) → 运维人员 dkd-app 接单
→ 现场补货 → 工单完成 → 库存回写 → 循环
```

### 1.3 已有 AI 能力现状（重要：本方案的演进起点）

系统已存在一条**初级 AI 链路**，但属于"单轮 Prompt 拼接"模式：

- **后端**：`com.dkd.common.ai` 包 —— `IAiService`（diagnose / ask / generateSuggestion），`AiServiceImpl` 用 Hutool HTTP 直连 DeepSeek Chat Completions（OpenAI 兼容协议），配置在 `application.yml` 的 `ai.*` 节点；
- **Prompt 工程**：硬编码在 Java 的 switch-case 中（`generateDiagnosisPrompt` / `generateSuggestionPrompt`），按模块类型（DEVICE/ORDER/INVENTORY/FINANCE/OPERATION/USER/PRODUCT/ANALYSIS/NODE 九类）拼装固定字段；
- **业务封装**：`AiOperationServiceImpl.getSmartAdvice(innerCode)` 仅取设备基础 5 个字段喂给 LLM，输出 100 字以内文本建议；
- **前端**：`vm/index.vue`（设备 AI 诊断按钮）、`node/index.vue`（点位 AI 诊断）、`inventory/index.vue`（补货建议弹窗）已有交互入口。

**现状局限（接入智能体的根本动因）：**

| 局限 | 具体表现 | 智能体的解法 |
| --- | --- | --- |
| 无上下文记忆 | 每次请求单轮直答，无法追问 | LangGraph 多轮会话 + checkpointer 状态持久化 |
| 无工具调用 | LLM 拿到什么算什么，不能主动查库存/订单/工单 | Function Calling 工具层封装 Java 业务 API |
| 数据维度单薄 | 诊断仅 5 个设备静态字段 | Agent 自主编排多数据源（库存+订单+工单+故障历史） |
| 决策不可执行 | 只输出 100 字文本，人工需自行去各页面操作 | Agent 直接回写：创建工单、生成补货计划 |
| 无结构化输出 | 自然语言文本，前端无法渲染为可操作列表 | Pydantic 结构化输出（如补货清单 JSON） |
| 无审计与可观测 | 只有 HTTP 响应日志 | LangSmith/LangGraph 全链路 trace + 决策留痕表 |

### 1.4 架构约束识别（决定集成方式的硬条件）

1. **Java 8 + Spring Boot 2.5.15**：无法升级到 Java 17，**排除 Spring AI / LangChain4j 新版本等 JVM 内智能体框架** → 必须采用 Python 独立进程旁路集成；
2. **单体架构、共库**：dkd-parent 与 dkd-app 共用 `dkd` MySQL 库，无消息中间件（MQ）→ 数据触发机制需在"定时轮询 / API 回调 / Binlog CDC"中选择低成本方案；
3. **已有 Redis**：可复用作会话状态、任务队列（List/Stream）；
4. **已有 LLM 供应商**：DeepSeek（OpenAI 兼容），Python 侧可无缝复用同一账号，并支持切换通义千问等兼容端点；
5. **前端统一 JWT 鉴权**（token header: Authorization，30 分钟有效）→ 智能体服务必须纳入同一鉴权体系，不能裸奔。

---

## 二、智能体职责定位与应用场景规划

### 2.1 总体定位

新增独立子系统 **dkd-agent（Python 智能体服务）**，定位为**"运营决策副驾 + 作业自动化引擎"**：

- Java 系统继续作为**业务事实的唯一所有者**（数据、事务、权限、审计）；
- 智能体作为**决策层与编排层**：负责感知（取数）、推理（LLM + 规则）、建议/执行（回调 Java API）、解释（对运营人员自然语言交互）；
- 遵循**人机分级**：只读分析类自动执行；写操作（创建工单等）首期一律"建议 + 人工确认"，成熟后按置信度分级放开。

### 2.2 场景优先级矩阵（基于现有数据就绪度排序）

| 优先级 | 场景 | 依赖数据 | 数据就绪度 | 价值 |
| --- | --- | --- | --- | --- |
| P0 | 智能补货 Agent | Inventory、InventoryLog、Order、Channel、Sku、Task | 高（表已齐备，且有规则版补货建议做对照基线） | 直接降缺货率、减人工排单 |
| P0 | 设备故障诊断 Agent | VendingMachine、Task（维修工单历史）、Inventory | 高（现有 /manage/ai/diagnose 直接升级） | 升级现有功能，用户可感知度最高 |
| P1 | 运营分析 Copilot | Order、Node、Region、Sku、Partner | 高 | 对话式取数，替代运营人员手写 SQL/导表 |
| P2 | 点位选址与商品配置 Agent | Node、Order、Sku、Policy | 中（需外部商圈数据增强） | 提升点位收益与货道坪效 |

### 2.3 场景一：智能补货 Agent（P0）

**目标**：将现有"规则版补货建议（RestockSuggestionDto）"升级为"预测驱动 + 可一键创建补货工单"的闭环智能体。

**端到端链路：**

```
[定时] dkd-quartz 触发(每日 06:00) → 调用 Python 服务 /agents/restock/analyze
→ Agent 工具取数: 货道库存(Inventory) + 近 30 天销量(Order 聚合) + 在途补货工单(Task)
→ 需求测算: 移动平均/分位数打底, LLM 结合点位画像校准（节假日前/天气/异常波动）
→ 约束校验: 货道容量(maxCapacity)/最小预警值/设备未完成工单检查（复用 TaskServiceImpl 校验规则）
→ 结构化输出: 补货清单 JSON（每货道: 现库存/建议量/预计撑到日期/优先级/理由）
→ 人工确认: 前端"补货工作台"一键确认 → Agent 调 POST /manage/task (insertTaskDto 通道) 批量创建补货工单
→ 闭环复盘: 工单完成 7 日后自动对比"建议量 vs 实际消耗量"，回写评估表，迭代预测参数
```

**为什么不是全自动创建工单**：`TaskServiceImpl.insertTaskDto` 存在硬校验（设备状态、未完成工单、员工区域匹配），且补货涉及实物流转成本 —— 首期人工确认是刻意设计，Phase 3 再评估按置信度自动派单。

### 2.4 场景二：设备故障诊断 Agent（P0）

**目标**：升级现有单轮 `/manage/ai/diagnose` 为多轮、多工具、可执行的诊断智能体。

**端到端链路：**

```
运营人员: "3号门那台机器最近老是卡货" → Agent 意图识别 + 设备实体消歧(innerCode)
→ 工具调用: 查设备详情/查近 7 天订单(异常成交) /查库存变动(InventoryLog)/查历史维修工单(Task)
→ LLM 综合推理: 症状 → 可能原因（排序）→ 处置建议 → 给出"创建维修工单"操作卡片
→ 人工点击确认 → Agent 调工单创建接口（自动带上诊断结论写入工单备注，便于维修人员预判）
→ 知识沉淀: 诊断结论结构化入库, 定期清洗为 RAG 语料（故障案例库越滚越厚）
```

### 2.5 场景三：运营分析 Copilot（P1）

**目标**：对话式运营取数与分析（"上周哪个点位卖得最好？""某某商品这月环比怎么样？"）。

- 技术路线：**受控 NL2SQL**（只读账号 + 表白名单 + SQL 校验器，杜绝越权与写操作）+ LLM 生成 ECharts 图表配置回传前端渲染；
- 兜底：复杂问题走"预置分析模板 + 参数抽取"（如点位排行、SKU 动销、区域对比、合作商分润），模板内 SQL 人工审核固化，准确率可控。

### 2.6 场景四：点位选址与商品配置 Agent（P2，暂列规划）

- 基于点位画像（商圈类型、现有订单密度）+ 候选地址对比 → 选址建议报告；
- 货道商品配置优化：按点位历史动销给出"哪些 SKU 该下架、该补进什么"，联动 `ChannelController` 的货道配置接口。

---

## 三、与 Java 系统的集成方式

### 3.1 总体集成架构

```
┌─────────────────────────────────────────────────────────────────────┐
│  dkd-vue (Vue3 + Element Plus)                                        │
│  新增: AI 助手侧边栏(流式对话) / 补货工作台 / 智能诊断对话框            │
└────────────────────────────┬────────────────────────────────────────┘
                             │  同源 axios, 统一 JWT (走 Java 网关转发)
┌────────────────────────────▼────────────────────────────────────────┐
│  dkd-parent (Spring Boot 2.5.15, :8080)          【业务事实所有者】   │
│  新增 dkd-common/agent 包:                                            │
│   ├─ AgentGatewayController  (/agent/** → 转发 Python, 透传用户身份)  │
│   ├─ AgentCallbackController  (Python 回调: 创建工单/写数据, 服务间鉴权)│
│   └─ AgentTokenFilter         (JWT 解析 → 注入 X-Agent-User 头)        │
│  复用: /manage/task 工单创建 / /manage/inventory 等 REST 能力           │
└──────────┬─────────────────────────────┬───────────────────────────┘
           │ REST (回调写操作, HTTP)      │ MySQL 只读直连 (分析读操作)
┌──────────▼─────────────────────────────▼───────────────────────────┐
│  dkd-agent (Python 3.11+ / FastAPI, :8090)       【新增智能体服务】  │
│   ├─ API 层: /chat(SSE流式) /agents/restock/** /agents/diagnose/**    │
│   ├─ LangGraph 编排: Supervisor + 补货/诊断/分析 子 Agent             │
│   ├─ 工具层: Java REST 封装(MySQL 读工具 + 工单写工具)                 │
│   ├─ 记忆: Redis checkpointer(会话) / 决策留痕表(审计)                 │
│   └─ LLM: DeepSeek(OpenAI兼容), 可切换通义千问等                       │
└─────────────────────────────────────────────────────────────────────┘
           ▲ 定时触发
┌──────────┴──────────────────────────────────────────────────────────┐
│  dkd-quartz: 每日补货分析任务 → HTTP 调用 Python /agents/restock/analyze│
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 集成模式选型对比（结论：API 网关旁路 + 读写分离）

| 方案 | 描述 | 结论 |
| --- | --- | --- |
| A. JVM 内嵌（LangChain4j 等） | Java 进程内跑智能体 | **否决**：本项目 Java 8，主流智能体框架要求 Java 17+ |
| B. Python 直连数据库（全直连） | Python 同时读写 dkd 库 | **否决写路径**：绕过 Java 的事务、校验（如工单防重）、权限、操作日志，产生数据一致性风险 |
| C. **API 网关旁路（推荐）** | Python 独立服务；**读走 MySQL 只读账号，写走 Java REST 回调**；前端经 Java 网关统一转发 | **采纳**：Java 保持事实所有者，Python 侧无侵入，前端不改鉴权体系 |
| D. 消息队列解耦 | MQ 驱动事件 | 暂缓：当前无 MQ 基础设施，规模不需要；预留升级路径（后续可加 RabbitMQ/Redis Stream） |

### 3.3 Java 侧改造点（刻意保持最小侵入）

新增 `dkd-common` 下一个 `agent` 包，约 4 个类，**不改动任何现有业务代码**：

1. `AgentGatewayController`：`/agent/**` 请求反向代理到 `http://localhost:8090`，透传方法/路径/Body，并在 header 注入当前登录用户（用户名、角色、区域），用于智能体侧的权限对齐与审计；
2. `AgentCallbackController`：供 Python 服务间回调（创建工单、查询业务数据兜底），使用独立的**服务间密钥**鉴权（配置项，非用户 JWT）；
3. `AgentProperties`：`agent.enabled / agent.baseUrl / agent.secret` 配置；
4. 网关超时与降级：对话类请求透传 SSE 流；Python 服务不可用时返回友好降级提示，**不影响主业务任何功能**（开关可一键下线智能体）。

现有 `IAiService`/`AiOperationController` 保留为**降级兜底**：`agent.enabled=false` 或 Python 服务故障时，`/manage/ai/diagnose` 继续走原单轮逻辑，保证功能不断档。

### 3.4 鉴权与安全设计

| 链路 | 鉴权方式 | 说明 |
| --- | --- | --- |
| 前端 → Java 网关 → Python | 复用现有 JWT（`token.header: Authorization`） | 网关解析后转发，Python 不解析 JWT，只信任网关注入的用户头 |
| Python → Java 回调（写操作） | 服务间密钥（HMAC 签名或固定 Secret Header） | 回调接口在白名单路径内，仅限内网 |
| Python → MySQL | 独立只读账号（仅 SELECT 权限，仅授权业务表） | 物理隔离写风险；NL2SQL 场景再叠加表白名单+SQL 校验 |
| 写操作二次确认 | 前端确认卡片（工单创建前展示完整参数预览） | 首期所有写操作必须人工点击确认 |

> 附带安全建议（与本方案并行推进）：`application.yml` 中目前存在明文的 OSS AccessKey/SecretKey 与 DeepSeek API Key，建议尽快轮换密钥并外置到环境变量/配置中心，避免随代码库泄露扩大影响面。

---

## 四、数据交互链路设计

### 4.1 读写分离原则（数据链路的核心设计）

| 数据方向 | 通道 | 理由 |
| --- | --- | --- |
| **读（分析、批量聚合）** | Python 直连 MySQL 只读账号 | 补货分析需聚合近 30 天 Order × Inventory × Task，走 REST 会导致 N 次调用与 Java 侧接口膨胀；只读账号 + 白名单保证安全 |
| **读（单实体、实时详情）** | 调 Java REST（如 `/manage/vm/{innerCode}`） | 复用现有接口与权限语义，工具层天然与页面数据一致 |
| **写（创建工单、回写结果）** | 只走 Java REST（`POST /manage/task` 等） | 复用 `TaskServiceImpl` 的业务校验（设备状态/防重/区域匹配）与操作日志 |
| **触发（定时任务）** | dkd-quartz 定时 Job → HTTP 调 Python webhook | 复用现有调度体系，无需引入新中间件 |
| **流式（对话）** | 前端 → Java 网关 → Python SSE 透传 | 逐 token 输出，体验关键 |

### 4.2 智能体侧新增数据表（挂在 dkd 库，由 Java/DBA 建表）

| 表 | 用途 | 关键字段 |
| --- | --- | --- |
| `agent_conversation` | 会话元数据 | id、user_id、scene、created_at |
| `agent_message` | 消息明细（LangGraph checkpointer 的业务投影） | conversation_id、role、content、tool_calls(JSON)、created_at |
| `agent_decision_log` | **决策留痕（审计核心表）** | id、scene、input_context(JSON)、llm_output(JSON)、action、target_id、result、confidence、created_at |
| `agent_restock_plan` | 补货计划（建议→确认→执行全生命周期） | id、vm_id、inner_code、items(JSON)、status(建议/已确认/已建单/已复盘)、task_id、review_metrics(JSON) |

> 建表 DDL 与存量 RuoYi 表风格保持一致（create_time/update_time/by 等审计列），DDL 脚本在 Phase 0 交付。

### 4.3 典型时序（补货场景全链路）

```
06:00 dkd-quartz Job
  → POST http://python:8090/agents/restock/analyze  (服务间密钥)
     → [读] MySQL: 全量运营中设备的 Inventory + 近30天 Order 聚合 + 未完成补货工单
     → [算] 基线需求(统计) + LLM 校准(点位/节假日/异常)
     → [校] 货道容量/MOQ等价约束/未完成工单去重
     → [写] agent_restock_plan(status=建议) + agent_decision_log
  → 前端"补货工作台"展示待确认清单（含每条建议的依据）
运营人员 点击"确认建单"
  → POST /agent/restock/{planId}/confirm (用户JWT)
     → Java 网关转发 → Python 校验计划归属
     → Python 回调 Java: POST /manage/task (AgentCallback, 服务间密钥)
        → TaskServiceImpl.insertTaskDto: 设备状态/防重/区域校验 → 创建工单+明细
     → [写] agent_restock_plan(status=已建单, task_id) + 留痕
运维人员(dkd-app) 接单 → 补货完成 → 库存回写
7日后 Python 复盘 Job → 对比建议量 vs 实际消耗 → review_metrics → 迭代参数
```

---

## 五、技术实现路径（Python 侧详细设计）

### 5.1 技术选型

| 组件 | 选型 | 版本基线 | 理由 |
| --- | --- | --- | --- |
| 服务框架 | FastAPI + Uvicorn | ≥0.110 | 原生 async、SSE、Pydantic 结构化输出 |
| 编排框架 | **LangGraph**（+ LangChain 核心包） | langgraph ≥0.2 | 有状态多 Agent 编排、checkpointer、human-in-the-loop 中断恢复 |
| LLM 接入 | `langchain-openai`（OpenAI 兼容模式指向 DeepSeek） | — | 与现有 `ai.api-url` 同一供应商账号，零迁移成本；可切通义千问 |
| 会话持久化 | `langgraph-checkpoint-redis` | — | 复用现有 Redis；多实例水平扩展友好 |
| 观测 | LangSmith（可选）+ 结构化日志 + `agent_decision_log` | — | 全链路 trace，Prompt/工具调用可回放审计 |
| 数据访问 | SQLAlchemy 2.x（async, aiomysql）+ 只读账号 | — | 分析类查询 ORM/原生 SQL 双模式 |
| 部署 | 独立 venv/conda，systemd 或 Windows 服务；与 dkd-parent 同机或同内网 | — | 与现有运维形态一致，Phase 0 不引入容器化复杂度 |

### 5.2 LangGraph 编排设计（Supervisor 模式）

```
                     ┌──────────────┐
   用户/定时触发 ──▶ │  Supervisor   │  意图路由 + 会话管理 + 结果汇聚
                     └──────┬───────┘
        ┌───────────┬───────┴────────┬────────────┐
        ▼           ▼                ▼            ▼
 ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌────────────┐
 │ 补货Agent   │ │ 诊断Agent   │ │ 分析Agent  │ │ 闲聊/FAQ    │
 │ (P0)       │ │ (P0)       │ │ (P1)      │ │ 兜底路由     │
 └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ └────────────┘
       │   共享工具层 (LangChain Tools, function calling)
       ├─ vm_tools: 查设备/查货道/查点位        → Java REST
       ├─ inventory_tools: 查库存/查变动日志    → MySQL 只读
       ├─ order_tools: 销量聚合/订单明细        → MySQL 只读(白名单SQL)
       ├─ task_tools: 查工单/创建工单(确认后)   → 查:MySQL; 建:Java回调
       └─ knowledge_tools: 故障案例RAG检索      → 向量库(Phase 2)
```

- **Human-in-the-loop**：LangGraph `interrupt()` 原生支持——补货 Agent 生成计划后中断，等待前端确认事件恢复执行建单节点，天然的"建议→确认→执行"实现；
- **结构化输出**：补货清单用 Pydantic 模型约束（字段对齐现有 `RestockSuggestionDto`：vmId/skuId/channelId/currentQuantity/suggestedQuantity/priority/reason），前端可直接复用现有补货建议弹窗的渲染逻辑。

### 5.3 Python 工程结构（Phase 0 交付骨架）

```
dkd-agent/
├─ app/main.py                 # FastAPI 入口
├─ app/config.py               # env: LLM/API/DB/Redis/Agent密钥
├─ app/api/                    # /chat(SSE) /agents/restock/** /agents/diagnose/**
├─ app/graphs/
│   ├─ supervisor.py           # 总路由图
│   ├─ restock_graph.py        # 补货子图(取数→测算→校验→interrupt→建单→复盘)
│   └─ diagnose_graph.py       # 诊断子图(问诊→工具循环→结论→工单卡片)
├─ app/tools/                  # Java REST/MySQL 只读 工具封装(带鉴权/超时/重试)
├─ app/models/                 # Pydantic: 补货计划/诊断报告/对话消息
├─ app/db/                     # SQLAlchemy 只读引擎 + SQL 白名单校验器
├─ app/security.py             # 服务间密钥校验(回调) + 用户头解析(网关透传)
└─ tests/                      # 单测 + 工具层集成测试(打桩Java接口)
```

### 5.4 前端改造点（dkd-vue，Phase 1）

1. 全局 **AI 助手侧边栏**（右侧抽屉，SSE 流式渲染，支持 Markdown + 操作卡片）；
2. `inventory/index.vue` 现有"补货建议"按钮升级 → 跳转**补货工作台**（确认式建单）；
3. `vm/index.vue` 的 AI 诊断弹窗升级为多轮对话框（入口 URL 不变，内部改调 `/agent/**`）；
4. axios 层新增 `/agent` 前缀路由（无需改动拦截器，JWT 自动携带）。

---

## 六、分阶段实施计划

> 原则：每阶段都有可独立演示的闭环交付物；总周期约 10~12 周（2~3 人投入：1 Python + 1 Java + 前端兼职）。

### Phase 0：基础设施与集成底座（第 1~2 周）

- Python 工程骨架 + FastAPI 服务 + 健康检查；DeepSeek 连通（复用现有账号）；
- Java 侧 `AgentGatewayController/AgentCallbackController/AgentProperties` + 单测；SSE 透传验证；
- MySQL 只读账号、表白名单；Redis checkpointer 初始化；
- 建表 DDL（agent_conversation/message/decision_log/restock_plan）；
- 前端 AI 助手侧边栏壳（纯流式 echo 验证全链路）；
- **验收标准**：前端侧边栏发起对话，经 Java 网关到 Python，LLM 流式返回；`agent.enabled=false` 一键降级回原 `/manage/ai` 逻辑。

### Phase 1：智能补货 Agent MVP（第 3~5 周）

- 工具层：库存/订单聚合/在途工单查询（MySQL 只读）+ 工单创建（Java 回调）；
- 补货子图：统计基线（7/14/30 天分位销量 + 货道容量约束）→ LLM 校准与理由生成 → `interrupt` 人工确认 → 建单 → 留痕；
- dkd-quartz 每日 06:00 触发任务；
- 前端补货工作台（清单渲染 + 确认建单 + 失败原因展示）；
- **验收标准**：对 ≥10 台真实设备跑通"夜间分析 → 早晨确认 → 批量建单 → 工单正常流转"；与规则版 `generateRestockSuggestions` 并行对比 2 周，输出效果对比报告。

### Phase 2：设备故障诊断 Agent（第 6~7 周）

- 诊断子图 + 设备/库存/工单历史工具；多轮问诊；
- 诊断结论结构化（原因排序/处置建议/置信度）→ 维修工单创建卡片（结论写入工单备注）；
- `vm/index.vue`、`node/index.vue` 入口切换至 `/agent/**`；
- 故障案例库冷启动（历史维修工单清洗入库，RAG 可选，先规则检索）；
- **验收标准**：运营人员在无培训情况下完成 5 个真实故障的诊断→建单闭环；诊断信息量显著优于现有 100 字单轮建议。

### Phase 3：运营分析 Copilot + 体系化收尾（第 8~10 周）

- 预置分析模板（点位排行/SKU 动销/区域对比/合作商分润）+ 参数抽取；受控 NL2SQL（只读+白名单+校验器）；
- 补货复盘自动化（7 日回看指标迭代）；
- 可观测看板：`agent_decision_log` → 决策审计页面（RuoYi 标准列表页）；
- 安全收尾：密钥外置、回调白名单、压测与故障演练；
- **验收标准**：运营高频取数问题 80% 由 Copilot 直接回答；全部写操作可审计追溯。

### Phase 4（后续规划，暂不承诺）

- 按置信度分级自动建单（放量前需 Phase 3 复盘数据支撑）；
- 点位选址 Agent、货道配置优化；
- MQ 事件化改造、多实例部署、模型灰度切换。

---

## 七、风险与对策

| 风险 | 等级 | 对策 |
| --- | --- | --- |
| LLM 建议错误导致错补/漏补 | 高 | 首期全部人工确认；复盘指标闭环；对比期与规则版并行跑 |
| NL2SQL 越权/慢查询拖垮库 | 高 | 只读账号 + 白名单 + SQL 静态校验 + 查询超时熔断；分析走独立只读实例（可后续加从库） |
| Python 服务故障影响主业务 | 中 | 旁路架构 + `agent.enabled` 开关 + 现有 IAiService 兜底，主业务零依赖 |
| 服务间回调被滥用 | 中 | 内网限定 + 密钥鉴权 + 路径白名单 + 速率限制 |
| Prompt 注入（用户输入操纵工具调用） | 中 | 系统提示隔离、工具入参白名单化、写操作二次确认不受对话影响 |
| 成本失控（LLM token 消耗） | 低 | 会话长度上限、缓存取数结果、按用户/场景限额与计量报表 |
| 团队 Python 运维经验不足 | 低 | 单服务极简部署（无容器依赖）、LangSmith/日志双观测、完整 runbook |

---

## 八、价值量化指标（接入后 3 个月评估口径）

| 指标 | 基线来源 | 目标 |
| --- | --- | --- |
| 缺货率（缺货货道数/总货道） | Inventory 预警数据 | 降低 30%+ |
| 补货排单人工耗时 | 运营实际工时 | 减少 60%+（从逐台看库存到清单式确认） |
| 故障响应时长（发现→建维修单） | Task 创建间隔 | 缩短 50%+ |
| 运营取数需求交付周期 | 现状依赖开发导数 | 从小时级降到分钟级（Copilot 自助） |
| 决策可审计率 | 0（现状无留痕） | 写操作 100% 留痕可回溯 |

---

## 九、待评审决策点（请重点确认）

1. **写操作边界**：首期"全部人工确认"是否符合预期？还是允许低风险场景（如库存盘点修正）直接执行？
2. **补货建单粒度**：按"设备"整单创建（现状 insertTaskDto 即设备级）还是支持按货道拆分？
3. **LLM 供应商**：继续 DeepSeek，还是评估通义千问（两者均为 OpenAI 兼容，切换成本≈0，仅改配置）？
4. **部署位置**：dkd-agent 与 dkd-parent 同机部署（当前单机形态）还是独立服务器？
5. **Phase 1 对比期**：与规则版补货建议并行跑 2 周是否可接受（涉及双入口并存的 UI 处理）？
6. **安全整改**：application.yml 明文密钥轮换与外置是否随 Phase 0 一并执行？

---

*本方案基于对 dkd-parent/dkd-app/dkd-vue 源码的实际分析编写（关键依据：`com.dkd.common.ai` 包、`TaskServiceImpl.insertTaskDto` 校验链、`IInventoryService` 预警/补货建议接口、`application.yml` 配置）。评审通过后输出：① 原型图（AI 助手侧边栏/补货工作台/诊断对话）；② Phase 0 详细技术设计与 DDL。*
