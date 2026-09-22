# 帝可得（DKD）智能售货机平台 LangChain/LangGraph 智能体接入方案

> 版本：V1.1（评审修订）
> 日期：2026-09-21
> 范围：现有架构分析 → 智能体场景规划 → 集成方式 → 数据链路 → 技术实现路径 → 分阶段实施计划
> 说明：原型见 `docs/prototypes/dkd-agent-prototype.html`（V2）；排期见《DKD智能体接入项目排期计划.md》**V2**。
>
> **V1.1 修订摘要（均基于源码与现场实测，证据见 §十）**：
> ① **表白名单修正**——实际业务表**全部带 `tb_` 前缀**，V1 的 `inventory/order/task/...` 写法会直接失败，且漏掉诊断/建单必需的 `tb_inventory_log`/`tb_task_details`/`tb_emp`/`tb_channel`；
> ② **会话持久化换型**——实测本机 Redis 为 3.2.100（无模块系统，不支持 RedisJSON/RediSearch；内核 < 5.0 无 Streams），`langgraph-checkpoint-redis` **不可用**，改为本地 **SQLite saver**；
> ③ **密钥整改现状修正**——源码**已**占位符化（但工作区未提交），而 **Git 初始提交中含真实密钥**，整改动作应为「提交 + 轮换 + 历史清理」，工时由 1d 修正为 1.75d；
> ④ **新增 §5.5 补货工单接单人分配与幂等设计**——`insertTaskDto` 的「同设备+同类型进行中工单防重」与「员工区域必须等于设备区域」两条校验是 M2 验收的隐性拦路虎，V1 未覆盖；
> ⑤ **新增技术约束**：LangGraph 版本精确锁定与补货 state schema 冻结评审、SQL 聚合禁用 `date_format()` 包裹列、SSE 在前端需绕开 axios 自行实现、只读账号需覆盖"读非白名单表被拒"、评测集建设、LLM 成本计量前移至 Phase 0；
> ⑥ **周期修正**——原"10~12 周"不成立（Phase 0 的 Java 侧 7 人日 vs 7 个可用工日 × 30% 投入，超载约 3.3 倍），修正为 **14 周**，详见排期计划 V2 §六。

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
3. **已有 Redis（现场实测 v3.2.100）**：本机实例为 `E:\Redis-x64-3.2.100`（Microsoft 2016-07 移植版）——**无模块系统**（不支持 RedisJSON/RediSearch，因此 `langgraph-checkpoint-redis` 不可用）、内核 3.2 < 5.0（**无 Streams**）、`appendonly no`（仅 RDB，崩溃最多丢 15 分钟数据）、`maxmemory` 未设置且与 Java 侧共用 `db 0`。**结论：Redis 仅保留给现有 Java 会话/序列使用，不承载智能体会话状态，也不作为队列**（V1 原计划作废）；
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
→ 人工确认: 前端「补货工作台」一键确认 → Agent 调 POST /manage/task (insertTaskDto 通道) 批量创建补货工单
   （**粒度约束**：`insertTaskDto` 的防重校验为「同设备 + 同工单类型 + 进行中」，因此只能**按设备整单创建、明细含多货道**，无法按货道拆分多张单；且必须按设备 `region_id` 匹配接单人，详见 §5.5）
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
│   ├─ 记忆: SQLite saver(会话) / 决策留痕表(审计)                    │
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
| D. 消息队列解耦 | MQ 驱动事件 | 暂缓：当前无 MQ 基础设施，且**本机 Redis 3.2.100 不支持 Streams**，当前规模不需要；预留升级路径（后续可加 RabbitMQ，或升级 Redis 版本后启用 Stream） |

### 3.3 Java 侧改造点（刻意保持最小侵入）

新增 `dkd-common` 下一个 `agent` 包，约 4 个类，**不改动任何现有业务代码**：

1. `AgentGatewayController`（`dkd-common`）：`/agent/**` 请求反向代理到 `http://localhost:8090`（`agent.base-url`），透传方法/路径/查询串/Body，并按**白名单重建**用户身份头（`X-Agent-User` / `X-Agent-Username` / `X-Agent-Roles` / `X-Agent-Region` / `X-Request-Id`）——客户端自带的 `X-Agent-*` 一律丢弃（防伪造，`AgentUserContext` 以 request attribute 传递而非改写请求头）；
2. `AgentCallbackController`（位于 **`dkd-manage`**）：供 Python 服务间回调建单（`POST /agent/callback/task` → `ITaskService.insertTaskDto`），使用独立的**服务间密钥**鉴权（配置项，非用户 JWT）。**为什么不在 `dkd-common`**：它需要注入 `ITaskService`，而 `dkd-manage` 已依赖 `dkd-common`，放进 common 会形成模块环；
3. `AgentProperties`：`agent.enabled / agent.base-url / agent.secret / connect-timeout / read-timeout / callback-path-whitelist` 配置。配套类：`AgentConfig`（过滤器注册顺序）、`AgentTokenFilter`（安全链**之后**注入身份，依赖 SecurityContext）、`AgentCallbackAuthFilter`（安全链**之前**做密钥 + 白名单校验）、`AgentUpstreamClient`（HTTP 中继，两条通道：SSE 字节流 / JSON 转发）、`AgentRequestId`（requestId 解析 + 净化，防日志注入）；
4. 网关超时与降级：对话类请求透传 SSE 流；Python 服务不可用时返回友好降级提示，**不影响主业务任何功能**（开关可一键下线智能体）。
   - **流式实现约束（0-6 落地修正）**：原方案要求用 `StreamingResponseBody`（或 `ResponseBodyEmitter`）逐块写出。实测发现 **`dkd-common` 只依赖 `spring-web`、不含 `spring-webmvc`**（`StreamingResponseBody` 在 webmvc 包内），在该模块无法编译。落地改为：控制器直接写 `HttpServletResponse` 输出流，**读一块写一块并立即 flush**（`AgentUpstreamClient.relayStream`，1KB 块）。语义与方案一致（**禁止先把响应体读完再返回**），并附带好处：同步写不受 Tomcat 异步 `asyncTimeout`（默认 30s）掐流影响。代价：每个在途对话占用一个 Tomcat 工作线程（`server.tomcat.threads.max=800`，当前规模无风险；若并发对话数长期 >50 应改回异步或调线程池）。
     **实测证据（2026-09-21，W1 末）**：1200 字回复产生 51 个 delta 帧、总时长 3.2s，首个 delta 帧距首行仅 **70ms**、末帧距首帧 **3217ms**——若网关整包缓冲，所有行会在毫秒级一起到达；
   - **前端约束（V1 遗漏）**：现有 `dkd-vue/src/utils/request.js` 的 axios 实例 `timeout: 10000` 且响应拦截器按 `res.data.code` 解析 JSON，**无法用于 SSE**；前端需新增独立 SSE 客户端（`fetch` + `ReadableStream`，因为 `EventSource` 不能携带 `Authorization` 头），并实现 `AbortController` 取消与心跳（AGENTS §2.2 要求长请求可取消）；
   - **降级范围界定**：`IAiService` 只能兜底**单轮文本**场景（原 `/manage/ai/diagnose`），侧边栏多轮对话**没有等价兜底**，降级时前端应隐藏/禁用对话入口并给出提示，而非伪造会话能力。

现有 `IAiService`/`AiOperationController` 保留为**降级兜底**：`agent.enabled=false` 或 Python 服务故障时，`/manage/ai/diagnose` 继续走原单轮逻辑，保证功能不断档。

### 3.3.1 网关与回调接口契约（0-6 / 0-7 实测口径）

> 本小节是 Java 侧（0-6/0-7）与 Python 侧（1-3 起）对接的**唯一口径来源**；每条均为 2026-09-21 实机验证结果，验证脚本见 `docs/scripts/g1-gateway-smoke.sh`。

**A. 前端 → 网关（复用用户 JWT）**

| 路径 | 方法 | 行为 |
| --- | --- | --- |
| `/agent/chat` | POST | SSE 透传（`text/event-stream`）。帧：`meta` → `delta`×N → `done`（异常时插入 `error`）。**token 级流式**：LLM 分片回调逐块下发（实测真 LLM：13 帧，首 token 距 meta **545ms**、末 token 距首 token **487ms**）。`delta` 带自增 `id`，支持 `Last-Event-ID` 续传。**帧字段分工**：`meta` 只带“开始时已知”的（conversation_id/request_id/scene/user/mode[echo\|llm]/resumed）；服务端**实际模型名**、`history_len`、`tokens_in/out`、`frames` 由 `done` 帧补全（首轮请求的 `meta.model` 为 `null`，**不写配置里的假值**） |
| `/agent/status` | GET | `{code:200,data:{enabled,upstream:"up"\|"down"}}`，供前端决定是否隐藏/禁用对话入口 |
| 其余 `/agent/**` | 任意 | 泛化代理（同方法/路径/查询串/Body），上游 2xx/4xx 原样透传 |
| `/agent/callback/**` | 任意 | **拒绝**（HTTP 404）：回调是 Python → Java 单向通道，不经网关暴露给前端 |

**B. 网关 → Python 请求头白名单**（只注入下列头，其余客户端头不转发）

`X-Agent-User`、`X-Agent-Username`、`X-Agent-Roles`、`X-Agent-Region`（可缺省）、`X-Request-Id`、`X-Agent-Secret`（纵深防御，Python 侧当前仅回调/运维接口强制校验）、以及客户端原样的 `Accept` / `Content-Type` / `Last-Event-ID`。

**C. 降级口径（`agent.enabled` 一键下线 + Python 故障）**

| 场景 | SSE 路径（`/agent/chat`） | 非 SSE 路径 |
| --- | --- | --- |
| `agent.enabled=false` | HTTP 200 + `error{msg:"智能体服务未启用",code:503,degrade:true}` + `done` 帧 | HTTP **503** + `{code:503,msg:"智能体服务未启用（agent.enabled=false）"}` |
| Python 不可达 / 超时 / 上游自身 5xx | HTTP 200 + `error{msg:"智能体服务暂不可用…",code:503,degrade:true}` + `done` 帧 | HTTP **503** + `{code:503,msg:"智能体服务暂不可用"}`（**不回显上游原始报文**） |
| 上游 4xx（如参数错误、token 限额） | 原样降级帧（上游透传错误帧，网关不拦截） | **原样透传**状态码与信封（4xx 携带明确业务语义，替换成 503 会让前端丢失原因） |

**D. 未认证口径（沿用 RuoYi 约定，与 `/manage/**` 完全一致）**：HTTP **200** + `{"code":401,"msg":"请求访问：…，认证失败"}`。
→ Python 侧无需感知；**前端 SSE 客户端必须按 `Content-Type` 判定是否为 SSE**，只看 `response.ok` 会把该信封当流解析，导致空白气泡（0-11 已按此修复）。

**D2. token 级流式（M1 缺口修复，2026-09-21）**：此前 LLM 节点用 `ainvoke` 整段取回、再按 24 字符分帧，属**帧级**流式（首字延迟 ≈ 整段 LLM 时延）。现改为：节点 `build_chat_model(streaming=True)` + API 层 `graph.astream(..., stream_mode=["messages","values"])`——`messages` 抽 LLM 分片回调（逐 token 下发），`values` 取每步后的完整 state（留痕/计量用）。**两个已踩的坑**：① `messages` 模式除了分片回调，也会把节点**直接返回的完整 `AIMessage`** 吐出来（echo 节点就是），因此必须按 `AIMessageChunk` 类型过滤，否则 echo 模式会重复发一遗；② 只有 `streaming=True` 或回调触发时 langchain 才真流式，显式打开以免版本升级静默回退。echo 节点（USE_LLM=0 的自检/降级演练）走兜底分帧路径，行为不变。

**E. 回调（Python → Java 写操作）**

- 端点：`POST /agent/callback/task`，Body = `TaskDto` JSON，头 `X-Agent-Secret`；
- 拒绝路径（413 前即拒绝，不进业务、不写库）：无密钥 / 伪造密钥 → **401**；密钥未配置 → **503**；路径不在白名单 → **404**；
- 成功与业务失败统一返回 RuoYi 信封（HTTP 200）：`{code:200,msg:"工单创建成功"}` 或 `{code:500,msg:"售货机不存在"}`——**失败原因是 `TaskServiceImpl` 校验链原文**（设备状态不符 / 设备有未完成工单 / 员工区域不一致），Python 侧应按**信封 `code`** 判定成败，不能只看 HTTP 状态码；
- 限流：`@RateLimiter(time=60,count=60,limitType=IP)`（Redis 计数，命中即拒，fail-closed）；
- **已知缺口（排期 1-3/1-8 处理）**：当前**不回传 `taskId`/`taskCode`**（`insertTaskDto` 只返回影响行数），Python 侧幂等回写 `agent_restock_plan.task_id` 需要它；
- **不伪造 `assignorId`**：回调无用户上下文，创建人语义由 `agent_restock_plan` 的确认人记录承担。

**F. `X-Agent-Region` 当前不注入（已知偏差）**：`tb_emp` 无 `user_id` 列、`sys_dept` 无 `region_id`，当前 schema 中**不存在 sys_user → 区域 的映射**（`DefaultAgentRegionResolver` 因此恒返回 null）。影响仅限审计上下文（Python 侧 `region_id=None`）；1-8 按**设备** `region_id` 匹配接单人不受影响。若要补全，需先定义管理端用户与区域的关联（数据模型变更）。

**G. 冒烟示例**

```bash
# 1) 登录取 JWT
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' http://127.0.0.1:8080/login \
  | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
# 2) SSE 透传（应逐帧到达，X-Request-Id 回带）
curl -sN -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"ping","scene":2}' http://127.0.0.1:8080/agent/chat
# 3) 回调拒绝路径（无密钥应 401）
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' \
  -d '{"innerCode":"x","productTypeId":2,"userId":1}' http://127.0.0.1:8080/agent/callback/task
```

### 3.4 鉴权与安全设计

| 链路 | 鉴权方式 | 说明 |
| --- | --- | --- |
| 前端 → Java 网关 → Python | 复用现有 JWT（`token.header: Authorization`） | 网关解析后转发，Python 不解析 JWT，只信任网关注入的用户头 |
| Python → Java 回调（写操作） | 服务间密钥（HMAC 签名或固定 Secret Header） | 回调接口在白名单路径内，仅限内网 |
| Python → MySQL | **两级授权账号** `dkd_agent`（建权脚本 `docs/ddl/create_agent_db_user.sql`）：业务表 `tb_*` 仅白名单内 SELECT；自有表 `agent_*` 可 SELECT/INSERT/UPDATE，**无 DELETE（软删除）/DDL**；禁用跨库（本机另有 8 个其他项目库） | 物理隔离写风险；验收须同时覆盖「**读非白名单表被拒**」「**写业务表被拒**」「**DELETE 被拒**」——只验「写被拒」不够（MySQL 只读账号默认仍可读全库表结构与其他库）；NL2SQL 场景再叠加表白名单 + SQL 校验 |
| 写操作二次确认 | 前端确认卡片（工单创建前展示完整参数预览） | 首期所有写操作必须人工点击确认 |

> **安全整改现状（2026-09-21 实测核对，V1 描述已过时）：**
> - **源码已改造完成**：`dkd-parent/dkd-admin/src/main/resources/application.yml`、`application-druid.yml` 与 `dkd-app/src/main/resources/application-dev.yml` 中的 OSS AccessKey/SecretKey、DeepSeek API Key、DB 密码、Redis 密码、JWT secret **均已改为 `${ENV}` 占位符**，并新增了 `.env.example`；
> - **整改进展（2026-09-21 复核修正）**：① 配置外置改造**已提交**（初始提交重写为 `3e1f4a8`，`main`/`origin/main` 可达历史已无明文）；② 旧提交对象以 dangling 形式残留本地对象库（`git gc` 后消失），且凭据在重写前已进入过历史；③ 本机开发凭据已集中写入 `.env`（已 gitignore，含新生成的 JWT secret 与 Druid 控制台口令）；
> - **仍待完成**：**凭据轮换**。实测旧 DeepSeek Key **已失效（HTTP 401）**，OSS AK/SK、MySQL/Redis 密码（当前均为纯数字弱口令）待轮换；JWT secret 轮换会使在线用户全部掉线，需协调值班窗口。

---

## 四、数据交互链路设计

### 4.1 读写分离原则（数据链路的核心设计）

| 数据方向 | 通道 | 理由 |
| --- | --- | --- |
| **读（分析、批量聚合）** | Python 直连 MySQL 只读账号 | 补货分析需聚合近 30 天 Order × Inventory × Task，走 REST 会导致 N 次调用与 Java 侧接口膨胀；只读账号 + 白名单保证安全 |
| **读（单实体、实时详情）** | 调 Java REST（如 `/manage/vm/{innerCode}`） | 复用现有接口与权限语义，工具层天然与页面数据一致 |
| **写（创建工单、回写结果）** | 只走 Java REST（`POST /manage/task` 等） | 复用 `TaskServiceImpl` 的业务校验（设备状态/防重/区域匹配）与操作日志 |
| **触发（定时任务）** | dkd-quartz 定时 Job → HTTP 调 Python webhook | 复用现有调度体系，无需引入新中间件；**Job 必须带失败重试与告警**（AGENTS §6.4：不允许半夜静默失败），并保证重入安全 |
| **流式（对话）** | 前端 → Java 网关 → Python SSE 透传 | 逐 token 输出，体验关键；前端需自建 SSE 客户端（不用 axios，见 §3.3） |

> **共库风险提示（V1 未评估）**：`application-druid.yml` 中 slave 数据源 `enabled: false`，当前只有**单主库**，Agent 的分析类查询会与业务查询争抢同一 MySQL 实例；且仓库 `sql/` 下**没有业务表 DDL**（仅有 RuoYi 系统表、quartz、`tb_report.sql`），数据量与索引状况未经评估。因此：Phase 0 必须先做数据量与索引勘查并归档 `docs/ddl/`；Phase 1 的分析查询限定凌晨窗口 + 按 vm 分批 + 结果缓存；Phase 3 前把「只读实例/从库」列入决策。
>
> **聚合 SQL 写法约束**：`OrderMapper.xml:44` 现有写法 `date_format(create_time,'%y%m%d') >= date_format(?, '%y%m%d')` 会让 `create_time` 索引失效；Agent 侧工具**必须**改用范围比较 `create_time >= ? AND create_time < ?`，并把 `EXPLAIN` 结果留档。

### 4.2 智能体侧新增数据表（挂在 dkd 库，由 Java/DBA 建表）

| 表 | 用途 | 关键字段 |
| --- | --- | --- |
| `agent_conversation` | 会话元数据 | id、user_id、scene、created_at |
| `agent_message` | 消息明细（LangGraph checkpointer 的业务投影 + **成本计量数据源**） | conversation_id、**user_id**、seq、role、content、tool_calls(JSON)、model、**tokens_in/tokens_out**、latency_ms、create_time |
| `agent_decision_log` | **决策留痕（审计核心表）** | id、scene、input_context(JSON)、llm_output(JSON)、action、target_id、result、confidence、created_at |
| `agent_restock_plan` | 补货计划（建议→确认→执行全生命周期） | id、vm_id、inner_code、items(JSON)、status(建议/已确认/已建单/已复盘)、task_id、review_metrics(JSON) |

> 建表 DDL 与存量 RuoYi 表风格保持一致（create_time/update_time/by 等审计列），DDL 脚本在 Phase 0 交付并归档至 `docs/ddl/`（附回滚语句）。
> 存量业务表（`tb_*`）DDL 目前**不在仓库中**，需从生产/测试库导出后归档，否则智能体侧开发者无法本地建库、无法评审索引与容量。

### 4.3 典型时序（补货场景全链路）

```
06:00 dkd-quartz Job（失败重试 + 告警）
  → POST http://python:8090/agents/restock/analyze  (服务间密钥)
     → [读] MySQL 只读账号: 运营中设备的 tb_inventory + 近30天 tb_order 聚合
          （范围比较 create_time >= ? AND < ?，按 vm 分批，查询超时熔断）
        + 在途补货工单 tb_task / tb_task_details
     → [算] 基线需求(统计) + LLM 校准(点位/节假日/异常)
     → [校] 货道容量(tb_inventory.max_stock)/最小预警值/未完成工单去重
     → [选] 按设备 region_id 匹配 tb_emp 选定接单人（区域不符则标记为“待指派”，不得建单）
     → [写] agent_restock_plan(status=建议) + agent_decision_log
  → 前端「补货工作台」展示待确认清单（含每条建议的依据）
运营人员 点击「确认建单」
  → POST /agent/restock/{planId}/confirm (用户JWT)
     → Java 网关转发 → Python 校验计划归属 + 幂等预检(plan.status 必须=建议/已调整)
     → Python 回调 Java: POST /agent/callback/task (AgentCallbackController, 服务间密钥)
        → TaskServiceImpl.insertTaskDto: 设备状态/防重/区域校验 → 创建工单(tb_task)+明细(tb_task_details)
        （失败原因原样返回前端：设备有未完成工单 / 员工区域不一致 / 售货机不存在）
     → [写] agent_restock_plan(status=已建单, task_id) + 留痕（幂等：重复确认直接返回已建单）
运维人员(dkd-app) 接单 → 补货完成 → 库存回写
7日后 Python 复盘 Job → 对比建议量 vs 实际消耗 → review_metrics → 迭代参数
```

---

## 五、技术实现路径（Python 侧详细设计）

### 5.1 技术选型

| 组件 | 选型 | 版本基线 | 理由 |
| --- | --- | --- | --- |
| 服务框架 | FastAPI + Uvicorn | ≥0.110 | 原生 async、SSE、Pydantic 结构化输出 |
| 编排框架 | **LangGraph**（+ LangChain 核心包） | **锁定精确版本三元组**（langgraph / langgraph-checkpoint / 下游 saver），禁止用 `≥x.y` 开放区间 | 有状态多 Agent 编排、checkpointer、human-in-the-loop 中断恢复。注意：`interrupt` 字段在 0.6 已被移除、1.0 又有变更，且 checkpointer 模块会绑定 langgraph 主版本——开放区间会导致 1-6 `interrupt` 节点返工 |
| LLM 接入 | `langchain-openai`（OpenAI 兼容模式指向 DeepSeek） | — | 与现有 `ai.api-url` 同一供应商账号，零迁移成本；可切通义千问 |
| 会话持久化 | **`langgraph-checkpoint-sqlite`（AsyncSqliteSaver）**；备选 fallback：Postgres saver（需多实例时） | 锁定版本 | ① 本机 Redis 3.2.100 不支持 RedisJSON/RediSearch → `langgraph-checkpoint-redis` **不可用**；② 当前为单机部署，SQLite 零新增基础设施；③ 需同步备份 `*.db` 文件（写入 runbook）。**迁移触发条件：出现多实例部署或需跨机共享会话** |
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
- **结构化输出**：补货清单用 Pydantic 模型约束（字段对齐现有 `RestockSuggestionDto`：vmId/skuId/channelId/currentQuantity/suggestedQuantity/priority/reason），前端可直接复用现有补货建议弹窗的渲染逻辑；
- **state schema 冻结（V1 遗漏）**：LangGraph 会把**最新图代码立即应用于所有历史 checkpoint**，即每次发版本质上都是相对已有会话状态的兼容性变更。因此 `restock_graph` 的 state 字段必须在 Phase 0 评审冻结（0-13），后续变更须走「兼容性评审 + 存量会话处理方案」（新增字段给默认值、禁止删改已持久化字段）。

### 5.3 Python 工程结构（Phase 0 交付骨架）

```
dkd-agent/
├─ app/main.py                 # FastAPI 入口
├─ app/config.py               # env: LLM/API/DB/Agent密钥(+ saver路径)
├─ app/api/                    # /chat(SSE) /agents/restock/** /agents/diagnose/**
├─ app/graphs/
│   ├─ supervisor.py           # 总路由图
│   ├─ restock_graph.py        # 补货子图(取数→测算→校验→interrupt→建单→复盘)
│   └─ diagnose_graph.py       # 诊断子图(问诊→工具循环→结论→工单卡片)
├─ app/tools/                  # Java REST/MySQL 只读 工具封装(带鉴权/超时/重试)
├─ app/models/                 # Pydantic: 补货计划/诊断报告/对话消息
├─ app/db/                     # SQLAlchemy 只读引擎 + SQL 白名单校验器 + 聚合工具(范围比较/分批)
├─ app/checkpoint.py           # AsyncSqliteSaver 初始化与生命周期管理
├─ app/security.py             # 服务间密钥校验(回调) + 用户头解析(网关透传)
└─ tests/                      # 单测 + 工具层集成测试(打桩Java接口)
```

### 5.4 前端改造点（dkd-vue，Phase 1）

1. 全局 **AI 助手侧边栏**（右侧抽屉，SSE 流式渲染，支持 Markdown + 操作卡片）；
2. `inventory/index.vue` 现有「补货建议」按钮升级 → 跳转**补货工作台**（确认式建单）；
3. `vm/index.vue` 的 AI 诊断弹窗升级为多轮对话框（入口 URL 不变，内部改调 `/agent/**`）；
4. **新增独立 SSE 客户端（不能复用 axios）**：`src/utils/sse.js` 封装 `fetch` + `ReadableStream`，手动携带 `Authorization` 头（`EventSource` 不支持自定义头）、支持 `AbortController` 取消、解析 SSE 帧、含心跳超时；普通 `/agent/**` REST 调用仍走 axios。

### 5.5 补货工单的接单人分配与幂等设计（V1 遗漏，M2 验收前置）

**问题来源**：`TaskServiceImpl.insertTaskDto` 的校验链决定了两件事——

```java
// dkd-parent/dkd-manage/.../TaskServiceImpl.java:147-158
// 查询同设备 + 同工单类型 + 进行中的工单，存在则拒绝
taskParam.setInnerCode(taskDto.getInnerCode());
taskParam.setProductTypeId(taskDto.getProductTypeId());
taskParam.setTaskStatus(DkdContants.TASK_STATUS_PROGRESS);
if (taskList != null && taskList.size() > 0) {
    throw new ServiceException("设备有未完成工单，请勿重复创建工单");
}
// :160-167  接单人必须存在，且区域必须与设备区域一致
if (!emp.getRegionId().equals(vm.getRegionId())) {
    throw new ServiceException("员工区域与设备区域不一致，请勿创建工单");
}
```

**设计要点**：

| 项 | 设计 | 理由 |
| --- | --- | --- |
| 建单粒度 | **按设备整单**，`details[]` 携带多货道明细 | 防重校验是设备级，按货道拆单必然撞"设备有未完成工单" |
| 接单人选择 | Agent 侧按设备 `region_id` 查询可用运维员工，优先「同区域 + 在岗」；按区域分组批量建单 | 避免"员工区域与设备区域不一致"失败；不同区域设备不能共用同一接单人 |
| 无可用接单人 | 计划标记为「待指派」并进入人工处理队列，**不调建单接口** | 宁可让人来拍，也不要抛异常给运营 |
| 幂等保护 | ① `agent_restock_plan.status` 状态机卡口（仅「建议/已调整」可确认）；② 建单前先行校验同设备是否已有进行中 补货工单；③ 乐观锁/版本号防并发双击 | 阻止重复点击、定时任务重跑、多人并发确认三种场景下的重复建单 |
| 失败反馈 | 把 `ServiceException` 的 message 原样透传到前端（按设备聚合展示） | 让运营看到"哪台设备为什么没建成"，而不是失败弹窗一律吞掉 |
| 留痕 | 每次建单尝试（含失败）写入 `agent_decision_log` | AGENTS §6.3：写操作决策必留痕 |

> 对排期的影响：新增任务 1-8（区域分配 + 幂等，工作量 1.5 人日），并作为 M2 验收的前置——用 10 台**跨区域**设备预演，而非同区域设备。

---

## 六、分阶段实施计划

> 原则：每阶段都有可独立演示的闭环交付物；**总周期 14 周（2026-09-21 ~ 2026-12-25）**。
> 人力前提：1 Python（全职）+ 1 Java（**W1~W3 需 ≥80%**，之后 30%）+ 前端（W1~W7 需 ≥50%，之后 30%）。
> **排期依据**：2026 年法定假期（中秋 09-25~27、国庆 10-01~07）扣减后，09-21~12-25 内的实际可用工日为 **64 天**（非 70 天）；V1 的「10~12 周」在 Phase 0 即出现 Java 侧约 **3.3 倍**负载超载（7 人日 vs 7 个可用工日 × 30%），不可执行。明细见排期计划 V2 §二/§六。

### Phase 0：基础设施与集成底座（W1~W3，09-21 ~ 10-09，9 个可用工日）

- Python 工程骨架 + FastAPI 服务 + 健康检查；DeepSeek 连通（复用现有账号）；依赖锁定（**精确版本三元组**）；
- Java 侧 `AgentGatewayController/AgentCallbackController/AgentProperties` + 单测；SSE 逐块透传验证（`StreamingResponseBody`）；
- MySQL 只读账号 + **`tb_` 真实表白名单** + 数据量与索引勘查；建表 DDL（agent_conversation/message/decision_log/restock_plan）；
- **checkpointer：SQLite `AsyncSqliteSaver`**（Redis 3.2.100 不可用的实测依据见 §5.1）；
- 前端 SSE 客户端（绕开 axios）+ AI 助手侧边栏壳（纯流式 echo 验证全链路）；
- `request_id` 贯穿 + 决策留痕 + **LLM 成本计量与限额**；LangGraph 版本锁定与 state schema 评审；
- 安全整改四项（提交 / 轮换 / Git 历史清理 / 生产注入校验）；
- **中间门 G1（09-30）**：前端→网关→Python echo 流式打通；
- **验收标准（10-09）**：前端侧边栏发起对话，经 Java 网关到 Python，LLM 流式返回；`agent.enabled=false` 一键降级回原 `/manage/ai` 逻辑（侧边栏入口隐藏，不伪造会话能力）；回调无密钥/伪造密钥均 401；代码库与 Git 历史无明文密钥。

### Phase 1：智能补货 Agent MVP（W4~W7，10-12 ~ 11-06，20 个可用工日）

- 工具层：库存/订单聚合/在途工单查询（MySQL 只读，**范围比较 + 分批 + 超时熔断**）+ 工单创建（Java 回调）；
- 补货子图：统计基线（7/14/30 天分位销量 + 货道容量约束）→ LLM 校准与理由生成 → `interrupt` 人工确认 → 建单 → 留痕；
- **接单人分配与幂等保护（§5.5，V1 遗漏）**；
- dkd-quartz 每日 06:00 触发任务（失败重试 + 告警）；
- 前端补货工作台（清单渲染 + 确认建单 + 失败原因按设备展示）；
- **验收标准**：对 ≥10 台 **跨区域**真实设备跑通「夜间分析 → 早晨确认 → 批量建单 → 工单正常流转」；与规则版 `generateRestockSuggestions` 并行对比 **3 周**，输出效果对比报告。

### Phase 2：设备故障诊断 Agent（W8~W10，11-09 ~ 11-27，15 个可用工日）

- 诊断子图 + 设备/库存/工单历史工具；多轮问诊；设备实体消歧（`tb_vending_machine.addr` 模糊匹配）；
- 诊断结论结构化（原因排序/处置建议/置信度）→ 维修工单创建卡片（结论写入工单备注）；
- `vm/index.vue`、`node/index.vue` 入口切换至 `/agent/**`；
- 故障案例库冷启动（**先做 0.5d 数据质量抽样**，不达标则降级为规则库；RAG 可选）；
- 兜底与降级：Agent 不可用时回落 `/manage/ai/diagnose` 单轮逻辑；
- **验收标准**：运营人员在无培训情况下完成 5 个真实故障的诊断→建单闭环；诊断信息量显著优于现有 100 字单轮建议。

### Phase 3：运营分析 Copilot + 体系化收尾（W11~W13，11-30 ~ 12-18，15 个可用工日）

- 预置分析模板（点位排行/SKU 动销/区域对比/合作商分润）+ **评测集构建（50~100 条，作为 3-3/3-4 的前置）** + 参数抽取；受控 NL2SQL（只读 + 白名单 + 静态校验器）；
- 补货复盘自动化（7 日回看指标迭代）；
- 可观测看板：`agent_decision_log` → 决策审计页面（RuoYi 标准列表页，含 JSON 详情与回放）；
- 压测与故障演练（含 **SQLite 文件损坏恢复**、MySQL 慢查询、定时任务重入）；
- **验收标准**：运营高频取数问题 80% 由 Copilot 直接回答（按评测集抽样验证，而非主观断言）；全部写操作可审计追溯。

### Phase 4：上线收尾（W14，12-21 ~ 12-25，5 个可用工日）

- 文档收尾（接口文档 / 部署 runbook / 运维手册 / 用户操作指引 / SQLite 备份恢复手册）；
- 上线评审与灰度（`agent.enabled` 按环境/角色开关）+ 回滚预案演练；
- 上线值守与首日观测（trace/错误率/token 消耗/留痕完整性）；1 天延期缓冲。

### Phase 5（后续规划，暂不承诺）

- 按置信度分级自动建单（放量前需 Phase 3 复盘数据支撑）；
- 点位选址 Agent、货道配置优化；
- MQ 事件化改造、多实例部署（**伴随 checkpointer 迁移**）、Redis 版本升级、模型灰度切换。

---

## 七、风险与对策

| 风险 | 等级 | 对策 |
| --- | --- | --- |
| LLM 建议错误导致错补/漏补 | 高 | 首期全部人工确认；复盘指标闭环；与规则版并行 3 周对比 |
| NL2SQL 越权/慢查询拖垮库 | 高 | 只读账号仅授权白名单表 + SQL 静态校验 + 超时熔断；**当前无从库（`slave.enabled=false`）**，分析限于凌晨窗口 + 分批 + 缓存；Phase 3 前决策只读实例/从库 |
| **补货建单反复失败（区域不匹配 / 重复建单）** | 高 | §5.5 接单人分配 + 「待指派」队列 + 幂等保护；M2 验收改用**跨区域**设备预演 |
| **checkpointer 向后兼容（state schema 变更）** | 中 | Phase 0 冻结 schema；变更走兼容性评审 + 存量会话处理方案；SQLite 文件纳入定期备份与恢复演练 |
| **业务表 DDL/数据量未知（仓库无 DDL）** | 中 | Phase 0 数据量与索引勘查，导出归档 `docs/ddl/`；聚合限制分批 + 超时 + EXPLAIN 留档 |
| Python 服务故障影响主业务 | 中 | 旁路架构 + `agent.enabled` 开关 + 现有 IAiService 兜底，主业务零依赖；降级时隐藏对话入口 |
| 服务间回调被滥用 | 中 | 内网限定 + 密钥鉴权 + 路径白名单 + 速率限制 |
| Prompt 注入（用户输入操纵工具调用） | 中 | 系统提示隔离、工具入参白名单化、写操作二次确认不受对话影响 |
| 成本失控（LLM token 消耗） | 低 | 会话长度上限、缓存取数结果、按用户/场景限额与计量报表；**计量与限额在 Phase 0 就绪**（V1 排在 W10，太晚） |
| **技术债：本机 Redis 3.2.100**（无模块/无 Streams/`appendonly no`/与 Java 共用 db0） | 低 | 本期不依赖 Redis；登记技术债，触发条件：多实例部署或需跨机共享会话 |
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

> **基线采集要求（V1 遗漏）**：上述 5 项指标必须在 Phase 0~1 期间**先把基线值落库固定**（缺货率可从 `tb_inventory` 预警数据回溯计算，排单耗时需人工计时抽样 2 周，故障响应时长为 `tb_task` 创建间隔统计），否则 3 个月后无法得出可信结论；Phase 1 的规则版双跑（1-14）同时承担基线采集职责。
> Copilot 的 80% 自助回答口径需按 3-2 的评测集抽样验证，不接受主观统计。

---

## 九、待评审决策点（请重点确认）

1. **写操作边界**：首期「全部人工确认」是否符合预期？还是允许低风险场景（如库存盘点修正）直接执行？
2. ~~补货建单粒度~~ → **已收敛为陈述句（仅需确认）**：`insertTaskDto` 的防重校验是「同设备+同工单类型+进行中」，因此只能是**按设备整单创建 + 明细多货道**，无法按货道拆分。请确认该约束符合业务预期（若必须按货道拆分，则需改造 Java 侧防重逻辑，属另一项需求）。
3. **LLM 供应商**：继续 DeepSeek，还是评估通义千问（均为 OpenAI 兼容，切换成本≈0）？**补充**：DeepSeek 能满足 function calling，但结构化输出（补货校准、参数抽取、NL2SQL）精度需用评测集（3-2）实测后再定，建议先建评测集、再定模型。
4. **部署位置**：dkd-agent 与 dkd-parent 同机部署（当前单机形态）还是独立服务器？（**同时决定** SQLite checkpointer 是否够用，见下条）
5. **Phase 1 对比期**：与规则版补货建议并行跑 **3 周**（V1 为 2 周，因 Phase 1 后移而顺延）是否可接受（涉及双入口并存的 UI 处理）？
6. **安全整改（描述已按实际进展修正）**：配置外置**已提交**、`main`/`origin` 可达历史已无明文（初始提交已重写为 `3e1f4a8`），本地 `.env` 已就绪 → 剩余动作是**凭据轮换 + 确认生产环境已注入环境变量**（旧 DeepSeek Key 实测 401 已失效，OSS AK/SK 与 DB/Redis 弱口令待轮换）。注：JWT secret 轮换会使在线用户全部掉线，需运营确认窗口。
7. **checkpointer 选型（新增）**：本机 Redis 3.2.100 不支持 RedisJSON/RediSearch → 已默认选用 **SQLite `AsyncSqliteSaver`**（单机部署）。请确认：是否接受单机会话存储？（若计划年内多实例部署，建议直接上 Postgres saver，避免二次迁移）
8. **接单人分配策略（新增）**：机器人自动按「设备区域 + 在岗」选运维员工（不足则进「待指派」队列），还是由产品指定固定接单人？
9. **分析读路径（新增）**：何时上只读实例/从库？当前单主库 + 无从库，Phase 3 的 NL2SQL 上线前需给出结论。
10. **评测集与准确率目标（新增）**：是否接受「先建 50~100 条评测集，再定准确率目标」的路径（V1 直接写死 ≥90%，无前置评测任务）？

---

*本方案基于对 dkd-parent/dkd-app/dkd-vue 源码与现场环境的实际核对编写（证据索引见下）；评审通过后输出：① Phase 0 详细技术设计（含 DDL）；② 只读账号授权 SQL 与表白名单。原型见 `docs/prototypes/dkd-agent-prototype.html`（V2）。*

---

## 十、证据索引（V1.1 修订依据，均为一手核对）

| 结论 | 证据位置 |
| --- | --- |
| 业务表均为 `tb_` 前缀 | `dkd-parent/dkd-manage/src/main/resources/mapper/manage/*.xml`（如 `InventoryMapper.xml:25-30`、`OrderMapper.xml:36`） |
| 工单防重（同设备+同类型+进行中） | `TaskServiceImpl.java:147-158` |
| 接单人必须存在且区域=设备区域 | `TaskServiceImpl.java:159-167` |
| 补货工单明细写入 | `TaskServiceImpl.java:177-191` |
| 规则版补货建议现状（作为对比基线） | `InventoryServiceImpl.java:414-535` |
| 订单聚合时间字段与索引失效写法 | `OrderMapper.xml:31-32,44-47` |
| 库存字段实际列名（current_stock/min_stock/max_stock） | `InventoryMapper.xml:12-13,25` |
| 现有 AI 链路（Hutool 直连 DeepSeek） | `dkd-common/.../ai/service/impl/AiServiceImpl.java:344`、`AiController`、`manage/controller/AiOperationController.java` |
| 前端 axios 无法承载 SSE | `dkd-vue/src/utils/request.js:18,20,75-84` |
| 单主库、无从库 | `dkd-parent/dkd-admin/src/main/resources/application-druid.yml`（`slave.enabled: false`） |
| Redis 配置（db 0、共用） | `application.yml:66-90` |
| 本机 Redis 3.2.100 能力边界 | `E:\Redis-x64-3.2.100`：`redis-server --version` = 3.2.100；`redis.windows.conf:194-196,582`（`appendonly no`）；无 `maxmemory` 配置项 |
| 配置外置已完成但未提交 | `git status`（`application.yml`/`application-druid.yml`/`dkd-app application-dev.yml` 为 M，`.env.example` 为 ??） |
| Git 历史与密钥现状（2026-09-21 复核） | `git log --all -p -- dkd-parent/dkd-admin/src/main/resources/application.yml` 已无明文；`git fsck` 可见 dangling `5d56572`（旧提交，含明文）；旧 DeepSeek Key 探测返回 HTTP 401 |
| 业务表 DDL 未入库 | `dkd-parent/sql/` 下仅有 RuoYi 系统表、`quartz.sql`、`tb_report.sql` |
| 2026 年假期 | 国务院办公厅关于 2026 年部分节假日安排的通知（中秋 09-25~27、国庆 10-01~07，09-20/10-10 上班） |
