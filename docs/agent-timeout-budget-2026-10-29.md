# 智能体超时预算核对（P2-30）· 2026-10-29

> 任务：P2-30（见 `docs/P2治理与一致性详细计划-2026-10-29.md:158-162`）。
> 性质：**分析与文档。本文没有改动任何运行时行为**——`agent.read-timeout`、LLM 超时、回调超时、
> 前端空闲超时全部保持原值（是否参数化/要否拆超时属后续独立评审，见 §5）。
> 证据基线：工作区 `feature/p2-governance`，2026-10-29。凡结论都带 `文件:行`；
> 凡没有实测过的，一律写「未验证」，不按印象下结论（AGENTS §9.3）。
> 配套运维口径：`docs/agent-operations-runbook.md` §八「超时预算」（速查表 + 排障顺序）。

---

## 0. 摘要（先看这 7 条）

| 编号 | 结论 | 证据 |
| --- | --- | --- |
| D1 | **报告原文的「120s 网关 vs 10s 回调」不构成倒挂**：回调（≤10s）发生在网关一次等待（120s）**内部**，10s < 120s 是正确嵌套。真正的量级差异是「一次 Java 等待上限」vs「Python 一整张图的工作量」 | `task_tools.py:132-135`、`application.yml:166-169` |
| D2 | **chat（SSE）上的 120s 不是总时长上限，而是「帧间空闲上限」**（JDK socket 读超时）。`application.yml:168` 的「同时作为 SSE 空闲心跳上限」**成立**，但本链路**没有心跳帧**——所谓心跳实际是 LLM/工具持续产出的正文帧 | `AgentUpstreamClient.java:121-141,183-200,344-348`；`chat.py:672-702,772-792` |
| D3 | **chat 上最先掐断的不是 Java，是前端**：前端空闲 60s（`sse.js:21-22`）< Java 120s，而且前端把「空闲超时」也当成「用户取消」处理（`aborted` 分支不续传）——用户看到「（已取消）」 | `sse.js:63-70,168-176`；`AgentAssistant/index.vue:446-450` |
| D4 | **真正倒挂的是 06:00 补货分析（非 SSE）**：Python 在 `calibrate` 节点对 N 台设备**串行**各做一次 LLM 校准（每次上限 60s，SDK 最多 3 次尝试=180s），而 Java 侧 `forward` 的 120s 在这里近似「总时长上限」 | `restock_calibration.py:345-376`、`llm.py:57-64`、`config.py:47` |
| D5 | 越界的后果不是「数据错」，而是**状态自相矛盾 + 成本放大**：Java 记 `sys_job_log` 失败并最多重试 3 次（每次重跑整轮分析、重复调 LLM），Python 侧不中断、计划照样落库 → 「任务报失败，但工作台有数据」 | `AgentTask.java:103-137`、`restock_analyzer.py:147-154` |
| D6 | **事实纠正**：`IpPolicyServiceImpl` 全仓不存在（唯一命中是梳理报告自身那句）。真正存在的是 `PolicyServiceImpl`（纯 CRUD，见梳理报告 P2-25）。P2-30 里「未见其 fail-closed 设计」这半句前提不成立 | §6.1 的扫描输出 |
| D7 | 建议清单见 §5：**1 条无需改代码（只是别单改默认值）** + **2 条低风险可先做（参数化/注释口径）** + **4 条需单独评审（异步化、校准预算、SSE 心跳、前端超时语义）** | §5 |

一句话：**「120s vs 10s」问错了对象；超时预算的真实缺口在「一次等待 vs N 次串行 LLM」，以及 chat 链路上「前端 60s < Java 120s」的层层收窄。**

---

## 1. 参数证据表（配置项 → 消费点 → 语义）

### 1.1 Java 侧（dkd-parent）

| 参数 / 常量 | 值与定义位置 | 代码消费点 | 语义 |
| --- | --- | --- | --- |
| `agent.connect-timeout` | `2000` ms，`dkd-parent/dkd-admin/src/main/resources/application.yml:166-167` | `AgentUpstreamClient.java:78-84`（构造时建 RestTemplate）；`:344-348` | 建连超时；连不上就快速降级 |
| `agent.read-timeout` | `120000` ms，`application.yml:168-169` | 同上 | **两条通道语义不同**，见 §2 |
| 探活超时 | 固定 `1000`/`1000` ms，`AgentUpstreamClient.java:65-68` | `pingHealth()` `:94-107` | 不受上面两个配置影响；只用于 `/agent/status` |
| SSE 中继通道 | 逐块读→逐块写→flush，`AgentUpstreamClient.java:121-141`、`copyStream:183-200`、`writeChunk:212-224` | `AgentGatewayController.chat():195-229` | 长连接，不缓冲整体响应 |
| 非 SSE 转发通道 | 先读完整响应体（上限 4MB）再返回，`AgentUpstreamClient.java:154-175,233-248` | `AgentGatewayController.proxy():241-305`、`AgentTask.restockAnalyze():111` | 缓冲式，近似「总时长」 |
| 降级/异常归因 | 传输失败→`AgentUpstreamException(STATUS_TRANSPORT_FAILURE)`，`:323-327` | 网关 SSE 写降级帧 `AgentGatewayController:223-228`；定时任务重试 `AgentTask:124-127` | 超时会被当作「上游不可达」处理 |
| 定时分析重试次数 | `analyzeRetryMax = 2`（共 3 次），`dkd-parent/dkd-common/src/main/java/com/dkd/common/agent/config/AgentProperties.java:54-61` | `AgentTask.java:103` | 只对 5xx / 传输失败重试；4xx 立即跳出（`:118-122`） |
| 定时分析退避 | `analyzeRetryBackoffMs = 5000`，`AgentProperties.java:63-69` | `AgentTask.java:221-247` | 线性退避：第 1 次等 5s、第 2 次等 10s |
| Tomcat 异步超时 | 不适用 | `AgentGatewayController.java:50-53` | SSE 用**同步**写 Servlet 输出流，刻意绕开 `asyncTimeout`（默认 30s）会掐长回复的问题 |
| 单次主动耗时上限（估算） | 3×120s + 5s + 10s ≈ **375s** | 由上面三行组合得出 | 06:00 定时任务的 Java 侧最坏等待 |

### 1.2 Python 侧（dkd-agent）

| 参数 / 常量 | 值与定义位置 | 代码消费点 | 语义 |
| --- | --- | --- | --- |
| `llm_timeout_s` | `60.0`，`dkd-agent/app/config.py:47` | `llm.py:57-64`（`ChatOpenAI(timeout=...)`） | 单次 LLM 调用超时 |
| `max_retries` | `2`，`llm.py:62` | 同上 | SDK 层重试：单次调用最坏 **3 次尝试**（超时是否被 SDK 重试，本仓未验证，见 §7） |
| `read_query_timeout_s` | `5.0`，`config.py:120-125` | `app/tools/read_tools.py:195-230`（`asyncio.wait_for` `:216-218`） | 单条只读 SQL 熔断（单主库，禁止无上限慢查询，AGENTS §1 约束 5） |
| `read_batch_size` / `read_row_limit` | `50` / `2000`，`config.py:126-127` | `read_tools.py:190-192,384,518,...` | 设备按 50 一批查，单批一次 SQL、一次 5s 熔断 |
| `callback_timeout_s` | `10.0`，`config.py:132-135` | `app/tools/task_tools.py:68-73,159-185` | 单次「Python → Java 建单回调」超时；**非幂等，刻意不自动重试** |
| `create_task_concurrency` | `4`，`config.py:137-143` | `app/graphs/restock_graph.py:547`（`Semaphore`）、`:559`（`gather`） | P1-13：批量建单回调并发度 |
| `MAX_TOOL_ROUNDS` | `3`，`app/graphs/chat_graph.py:34-37` | `:236-250`（达上限不再 bind_tools）、`:385-399`（图侧硬拦） | **每句问话**的工具轮次上限（跨会话累计是 FIX-29 的坑，已修为按本轮推导 `:170-180`） |
| 回调失败三分类 + 不重试 | `task_tools.py:7-16`（docstring）、`:41-58`（`CallbackAuthError`/`CallbackUnavailableError`/`CallbackBusinessError`）、`:76-111`（按 HTTP 码 + 信封 code 分类） | 上层按类型处置 | 重试决策交给「计划状态机 + 幂等键」，传输层不偷着重试（非幂等写） |

### 1.3 链路上其它计时器（容易被漏掉的「第三个 60s」）

| 环节 | 值 | 位置 | 说明 |
| --- | --- | --- | --- |
| 前端 SSE 空闲超时 | `60000` ms | `dkd-vue/src/utils/sse.js:21-22`（常量）、`:63-70`（计时器）、`:147-176`（每读到一块就重新计时） | 收不到**任何字节**即 `abort()`，报「响应超时，已中止」（`aborted=true`） |
| 前端对 aborted 的处理 | 当「用户取消」，**不续传** | `dkd-vue/src/components/AgentAssistant/index.vue:446-450` | 与 `tryResume()`（`:474-489`，只在 `retryable` 时走）相反 |
| nginx 反代读/写超时 | `3600s` | `deploy/docker/nginx/default.conf:64-71` | 为 SSE 放大；不构成当前瓶颈 |
| 部署健康检查的 SSE 检查 | `curl -sN -m 60`（总时长 60s） | `deploy/docker/scripts/healthcheck.sh:57-61` | 只发一句 `deploy healthcheck`；真 LLM 慢时会把这个部署门禁判成 FAIL |

---

## 2. 语义辨析：同一个 `read-timeout`，两条通道是两种量

`AgentUpstreamClient` 用同一个 `RestTemplate`（`AgentUpstreamClient.java:78-84`）服务两条语义完全不同的通道，
`SimpleClientHttpRequestFactory.setReadTimeout(...)` 透传到 JDK `HttpURLConnection`（`:344-348`）：

1. **`relayStream`（SSE，`/agent/chat`）**：一边读一边写，`:183-200` 的循环里每次 `in.read(...)` 是一次 socket 读。
   读超时的语义是 **SO_TIMEOUT：两次数据到达之间的最大间隔**，不是整个请求的时长上限。
   → 因此「120s 是 SSE 空闲心跳上限」**成立**（`application.yml:168` 的说法对这条路是对的）；
   但要说清：**本链路并没有心跳帧**——Python 只发 `meta`/`delta`/`done`/`error`（`chat.py:672-702,716-720,772-792`），
   没有 `ping`/注释帧。所谓「心跳」实际是「LLM/工具持续产出的正文帧」。这一点是后面 D3 的根因。
2. **`forward`（非 SSE，补货清单/确认/分析/故障诊断）**：先把整个响应体缓冲完再返回（`:154-175`）。
   对端是 Starlette 的普通 JSON 响应——handler 返回后才开始发响应体（框架语义，**本仓未实测分块行为**），
   所以首个字节到达前阻塞的时间 ≈ 整张图的耗时。
   → 这条路 `read-timeout` 近似**总时长上限**（严格说：若服务端把响应体分多次写出且每次间隔 <120s，则不受 120s 总时长限制）。

**结论**：P2-30 讨论「120s」必须先指明是哪条通道；把它当成 chat 的总预算，会得出「chat 超过 120s 就失败」的错误结论。

---

## 3. 最坏耗时推算

> 记号：N = 一轮分析涉及的设备数；「LLM 单次」= `llm_timeout_s`；「上限」= 每次都恰好卡到超时（这是上界，不是常态）。

### 3.1 chat（SSE，`POST /agent/chat`）

- **LLM 调用次数上限 = 1 + MAX_TOOL_ROUNDS = 4 次**：入口 `respond` 一次，之后每个工具轮次后回到 `respond` 一次；
  本轮 ToolMessage 达到 3 条后不再 bind 工具、图侧也硬拦（`chat_graph.py:236-250,385-399`，计数推导 `:170-180`）。
- 单次 LLM 上限：`60s`（`config.py:47`）；SDK 还有 `max_retries=2`（`llm.py:62`）→ 单次调用最坏 **3×60s = 180s**。
- 本轮总上限：不重试 **4×60s = 240s**；重试也全打满 **4×180s = 720s**。
- **帧间静默**（决定「谁先掐」）：流式只下发非空正文（`chat.py:469-474` 对空 text 直接 `continue`）。
  当模型某一轮只返回 `tool_calls` 而没有正文时，**这一轮没有任何 delta 帧**；连续几个工具轮次的静默时间 ≈
  3×(60s~180s) + 工具执行（每次只读查询 ≤5s，一个工具通常 1~数个查询）。
- **谁先掐断**（同一个请求上三层计时器同时生效，最先到的那层决定用户看到什么）：

| 顺序 | 计时器 | 值 | 触发后表现 |
| --- | --- | --- | --- |
| 1 | 前端空闲 | 60s（`sse.js:22`） | abort → 显示「（已取消）」，**不续传**（`index.vue:446-450`） |
| 2 | Java 网关帧间读超时 | 120s（`application.yml:169`） | 写 `error{degrade:true}` + `done` 帧（`AgentGatewayController:223-228`）；前端把入口置为 degraded |
| 3 | nginx | 3600s（`nginx/default.conf:68`） | 不是当前瓶颈 |

- **结论**：chat 当前的实际瓶颈是**前端 60s**，不是 Java 120s；而 60s 触发时被呈现成「用户取消」——
  把「上游慢」误报成「用户操作」，属于可观测性缺陷（D3）。

### 3.2 06:00 补货分析（非 SSE，Java quartz → `POST /agents/restock/analyze`）

链路：`AgentTask.restockAnalyze()`（`AgentTask.java:79-137`）→ `forward("POST", "/agents/restock/analyze")`（`:111`）
→ `app/api/ops.py:75-94` → `RestockAnalyzer.analyze()`（`restock_analyzer.py:102-225`）→ `graph.ainvoke(...)`（`:149`）。

**Java 侧预算**：单次 120s（`application.yml:169`）× 3 次尝试（`AgentProperties:61, AgentTask:103`）+ 退避 5s + 10s ≈ **375s**。

**Python 侧工作量**（两项相加）：
1. `analyze` 节点（`restock_graph.py:371-427`）：
   - 只读：`list_operating_machines` 1 次 + `list_channel_stock` ⌈N/50⌉ 次 + `aggregate_channel_daily_sales` ⌈N/50⌉ 次，
     每次 ≤5s（`read_tools.py:384-407,518-543,216-218`）；
   - 逐设备 `resolve_assignee`（缓存后仅有少量查询）+ **逐设备 upsert**（`save_plan`，`:402-407`）。
2. `calibrate` 节点（`_wrap_calibration:610-634` → `calibrate_state_node:345-376`）：
   - **对 N 份计划串行**调用 LLM（`for plan in plans: await calibrate_plan(...)`），每份一次 `client.ainvoke(...)`
     （`restock_calibration.py:287-310`）；失败降级为统计基线、不抛异常；
   - 校准后又逐设备 upsert 一次（`restock_graph.py:630-631`）。

**对照表**（只算 LLM 部分，其余查询最多几十秒）：

| N（设备数） | 典型 5s/次（见注） | 上限 60s/次 | 含 SDK 重试 180s/次 | Java 等待 120s 是否越界 |
| --- | --- | --- | --- | --- |
| 1 | 5s | 60s | 180s | 只有含 SDK 重试（180s）时越界 |
| 10 | 50s | 600s | 1800s | 上限时越界 |
| 24 | **120s**（临界） | 1440s | 4320s | 上限时越界 |
| 50 | 250s | 3000s | 9000s | 越界 |
| 100 | 500s | 6000s | 18000s | 越界 |

> 「典型 5s/次」的来源是前端注释里的一条实测记录（「实测 DeepSeek 单轮 5s+」，`dkd-vue/src/utils/sse.js:21`），
> **不是本任务实测**（见 §7）。它只用来给「N 多大时会越过 120s」一个量级感：**N≈24 起临界，N 越大越必然越界**。
> 上表也说明：即使 N=1，只要 LLM 卡到超时并触发 SDK 重试（180s），120s 就已越界。

**越界后的实际形态**：
1. Java：socket 读超时 → `ResourceAccessException` → `AgentUpstreamException(STATUS_TRANSPORT_FAILURE)`
   （`AgentUpstreamClient:323-327`）→ `AgentTask` 记 WARN 并重试（`:124-129`）→ 3 次都失败后抛
   `IllegalStateException` 写 `sys_job_log` 失败（`:134-136`）。
2. Python：非流式响应在 handler 返回后才发送，客户端断开不会取消已进入的图执行（框架语义，**未实测**）——
   分析会继续跑完并 upsert。
3. 于是出现自相矛盾的状态：**`sys_job_log` 显示「已尝试 3/3 次失败」，而工作台能看到当天计划**
   （`agent_restock_plan` 已写入）。运维会怀疑是不是重复触发/数据错乱，实际只是超时口径不同。
4. 成本放大：3 次尝试 = **最多 3 轮完整分析**，每轮都要 N 次 LLM 校准；前一轮的 Python 执行通常还没结束。
   upsert 按 `(vm_id, plan_date)` 幂等（`restock_analyzer.py:16-20`），所以**数据不会重复，钱会重复花**。

### 3.3 人工确认/调整（非 SSE，`/agent/restock/plans/{id}/{confirm|adjust|skip|restore}`）

- 生产路径是**单计划**：`RestockService._execute()`（`restock_service.py:419-529`）→ CAS 认领 → **1 次**回调（≤10s，
  `task_tools.py:159-185`）→ 落库 + 审计。
- 上界 ≈ 10s + 若干 DB 写 + 审计写，**远小于 120s**（余量 ≥ 10×；真实耗时未实测）。
- 注意：**不经过**图的 `create_tasks` 节点（见 §3.5），所以「N 台批量确认」在这条路上不存在。

### 3.4 故障诊断（非 SSE，`POST /agent/fault/runs`）

- 同步执行（`app/api/fault.py:73-92`），图内**不调 LLM**（`fault_graph.py:14-22`：结论由证据规则算出）。
- 只读工具 6 次（诊断）+ 2 次（派单）逐次 ≤5s（`read_tools.py:216-218`）→ 上界 ≈ **8×5s = 40s** + 写
  `agent_tool_call`/`step`；
- 代码注释有实测记录：「单台设备诊断实测 1~2 秒（6 次只读工具）」（`fault.py:77`）。
- 结论：**无倒挂**，但慢库场景下上界对 120s 只有约 2.4× 余量（未压测）。

### 3.5 补货图的 `create_tasks` 批量并发（**当前生产不可达**）

- P1-13 后最坏 = **⌈N/4⌉ × 10s**（`restock_graph.py:547,559`）：N=48 才到 120s。
- `restock_graph.py:517-520` 的注释仍按**改并发之前**的串行口径写「N 台最坏 N×10s……N>12 时逼近上限」；
  并发化后同一句里的 N>12 应改为 **N>48**（表述滞后；`dkd-agent/**` 不在本任务写范围，登记待后续）。
- **可达性证据**：全仓 `Command(resume=` 只出现在 `docs/scripts/verify-1-6-restock-plan.py:134`、
  `docs/scripts/verify-1-7-restock-api.py:79` 与 `dkd-agent/tests/test_restock_graph.py`；
  `app/` 内没有任何「恢复图」的调用，`RestockService` 也只装配状态机/存储模块、不装配图（`restock_service.py:33-54`）。
  → 生产 HTTP 路径走的是 §3.3 的「单计划单回调」。
- 结论：P2-30 原文把「批量回调 N×10s」当成主要风险点，**与当前生产路径不符**；若将来新增批量确认接口并恢复图执行，
  必须重新核算（且要同时考虑 120s）。

### 3.6 一页汇总：谁先掐断

| 链路 | Python 侧工作量 | 链路上的硬上限 | 最先触发者 | 是否倒挂 |
| --- | --- | --- | --- | --- |
| chat（SSE） | 最多 4 次 LLM（单次 ≤60s/180s）+ 工具 | 前端 60s / Java 120s（帧间）/ nginx 3600s | **前端 60s** | 是（D3，且被误报为「用户取消」） |
| analyze（非 SSE） | analyze 取数 + **N 次串行 LLM** | Java 120s（近似总时长）×3 次 | **Java 120s** | **是（D4，真实缺口）** |
| confirm/adjust（非 SSE） | 1 次回调 ≤10s | Java 120s | Python 回调 10s | 否 |
| fault runs（非 SSE） | 8 次只读 ≤5s | Java 120s | — | 否（余量约 2.4×） |
| plans list（非 SSE） | 数条只读查询 | Java 120s | — | 否 |
| 批量 create_tasks（脚本/测试直连图） | ⌈N/4⌉ × 10s | 无 Java 等待 | — | 生产不可达 |

---

## 4. 结论：预算倒挂清单

- **INV-1（真倒挂，建议 P1 处置）**：`/agents/restock/analyze` 的「一次 Java 等待 120s」vs「Python 串行 N 次 LLM 校准」。
  只有 N 很小（<约 2，且无重试）时才安全；N≈24（典型 5s/次）起必然临界。后果是**假失败 + LLM 成本 ×3**，不是数据损坏。
- **INV-2（真倒挂，呈现层问题）**：chat 路线上「前端空闲 60s」<「Java 帧间 120s」<「最坏静默（3 个无正文工具轮）≈180s+」，
  且前端把空闲超时报成「（已取消）」。（前端 `dkd-vue/**` 不在本任务写范围，只出结论与建议。）
- **INV-3（潜在）**：单次 LLM 的 60s 上限经 SDK `max_retries=2` 放大到 180s，本身即大于 Java 的 120s 帧间上限与前端 60s——
  只要 LLM 出现「hang 到超时再重试」，任何一条链路都会先被判失联，而 Python 侧还在重试。
- **NB-1（不是倒挂）**：回调 10s 嵌套在 120s 内，方向正确；`task_tools` 的非幂等「不自动重试」也是正确取舍
  （`task_tools.py:7-16`）。
- **NB-2（不是倒挂，前提有误）**：批量 `create_tasks` 的 ⌈N/4⌉×10s 当前生产不可达（§3.5）。

---

## 5. 建议（每条都注明「改不改运行时」与「是否需单独评审」）

| 编号 | 建议 | 改动面 | 是否需要评审 | 本次是否执行 |
| --- | --- | --- | --- | --- |
| R1 | **给 analyze 单独放宽等待**：按端点区分超时（analyze 用更长的 `forward` 超时，或新增独立 RestTemplate），或把 `AgentTask` 等待上限提到 N×60s 量级 | `AgentUpstreamClient` + `AgentProperties` + `application.yml` | **需要**（超时策略变更，且要考虑 4xx/5xx 重试语义不变） | 否（仅建议） |
| R2 | **analyze 异步化**（最彻底）：Java 触发后立即返回，Python 后台跑 + 结果回写/轮询；同时取消 Java 侧「整轮重跑」重试 | Java 定时任务 + Python ops 接口 + 状态载体 | **需要**（接口契约变更，涉及 AGENTS §6.4 告警口径） | 否（仅建议） |
| R3 | **校准节点加总预算**（止血、最小改动）：给 `calibrate` 设例如 `DKD_AGENT_CALIBRATION_BUDGET_S`，超预算的设备降级为统计基线并写 `issues`（校准失败本来就不算业务失败，`restock_calibration.py:374-375`） | `dkd-agent/app/graphs/restock_graph.py`（或 calibration） | **需要**（属行为变更：可能少校准几台） | 否（仅建议） |
| R4 | **SSE 心跳帧**：图执行期间每 10~15s 发一个注释帧/`ping` 帧，可同时解除前端 60s 与 Java 120s 的「帧间」误判 | Python SSE 流 + 前端解析容忍 | **需要**（协议帧类型变更，前后端都要动） | 否（仅建议） |
| R5 | **前端区分「空闲超时」与「用户取消」**：`idleTimedOut` 已存在（`sse.js:54,66-69,96,170`），但 `index.vue:446-450` 的 `aborted` 分支没区分；空闲超时应走 `tryResume()`（Last-Event-ID 续传已具备） | `dkd-vue/**`（**本任务禁改**） | **需要**（前端交互语义变更） | 否（仅建议） |
| R6 | **`read-timeout` 环境变量化**（低风险，但单做不解决问题）：`agent.read-timeout: ${DKD_AGENT_READ_TIMEOUT_MS:120000}` + `.env.example` 登记 | `application.yml` + `.env.example` | 低风险，可不评审；但真正解法是 R1/R2/R3 | **否**（本次未做，理由见下） |
| R7 | **注释/观测口径修正**：`application.yml:168` 与 `AgentProperties.java:44` 的「SSE 空闲心跳」补上「本链路无心跳帧、实际是正文帧间隔」；`restock_graph.py:519-520` 的 N>12 改 N>48；`AgentTask` 记录本轮耗时 | 注释/日志 | 低风险 | 仅本文件记录，未改他人文件 |

**R6 为什么本次没做**（三条理由，全部可核对）：
1. 本任务要求「默认只做文档与分析、不改运行时行为」；`application.yml` 也不在本任务写范围（写范围只含 `dkd-parent/Dockerfile`）；
2. 单独把默认值改成环境变量，**不会**修好 INV-1——analyze 越界是因为「一次等待 vs N 次串行 LLM」，
   把 120s 改成 300s 只是把越界点往后推，仍不可解释；
3. 已确认「没有测试断言 120000」，所以将来要做时风险很低：仓库源码里字面量 `120000` 只出现在
   `application.yml:169` 与 `AgentProperties.java:46` 两处（`AgentUpstreamClient.java:81` 只是读属性
   `getReadTimeout()`，不含字面量）；`AgentUpstreamClientTest.java:179-180` 是测试内自行
   `setReadTimeout(5000)`，不依赖默认值。

---

## 6. 事实纠正记录

### 6.1 `IpPolicyServiceImpl` 全仓不存在（P2-30 的前提有误）

扫描（覆盖 `.java/.xml/.yml/.md/.py/.js/.vue/.sql/.sh/.json/.properties/.txt` 与无扩展名文本文件，跳过 `.git`）：

```
files containing 'IpPolicy': 2
F:\DKD\docs\DKD系统性梳理-2026-10-28.md          ← P2-30 原文自身（第 282 行）
F:\DKD\docs\P2治理与一致性详细计划-2026-10-29.md  ← 计划里引用该结论（第 31 行）
文件名匹配 *IpPolicy* 的文件数：0
java 文件名匹配 *Policy*.java 的实际类：
  dkd-parent/dkd-manage/.../controller/PolicyController.java
  dkd-parent/dkd-manage/.../domain/Policy.java
  dkd-parent/dkd-manage/.../mapper/PolicyMapper.java
  dkd-parent/dkd-manage/.../service/IPolicyService.java
  dkd-parent/dkd-manage/.../service/impl/PolicyServiceImpl.java
```

（`.run/` 下是构建暂存副本，按 AGENTS §3 不作为依据，未计入。）

结论：
- 全仓**只有** `PolicyServiceImpl`（点位政策/分成策略的纯 CRUD，梳理报告 P2-25 已指出「抽成/分成计算完全缺失」）；
- **没有** `IpPolicyServiceImpl`、也没有任何「IP 策略 fail-closed」相关实现；
- 所以 P2-30 原文「`task_tools` 刻意不重试 + `IpPolicyServiceImpl` 未见的 fail-closed 设计」中，
  **前半句成立**（证据：`app/tools/task_tools.py:7-10` 明确写了「为什么不在工具里做重试」），
  **后半句的前提不成立**（对象不存在，无法确认其设计）。
- P2-30 真正可执行的内容 = 本文的超时预算核对；事实以本文为准。

### 6.2 顺带发现的同类偏差（记录，不在本任务写范围，未改）

1. `docs/DKD系统性梳理-2026-10-28.md:280` 说漂移在 `import-business-dump.sh:11`；修复前实际漂移行是 `:10`
   （2026-10-29 已在本任务修正该脚本注释，见 §9 改动清单）。梳理报告属禁改范围，此处仅记录。
2. `docs/scripts/g1-gateway-smoke.sh:19,110` 的「重启 Python」命令是 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8090`，
   缺少 `--loop none`——与 AGENTS §1 约束 5 / runbook §1.1 的已知强制约束冲突（Windows 上会启动失败）。
   该文件不在本任务写范围，登记待后续修正。
3. `dkd-agent/app/graphs/restock_graph.py:519-520` 的「N>12」口径属 P1-13 并发化之前的表述（应为 N>48），
   `dkd-agent/**` 禁改，登记待后续。

---

## 7. 未验证项与风险（不隐瞒）

1. **未做真实计时压测**：本文所有「最坏」都是按超时上限推算的上界，不是测量值。需要压测时请按 R1/R3 的验收口径复跑。
2. **LLM 单次/单轮实际耗时未测**：只用了一条既有实测注记（`sse.js:21`「DeepSeek 单轮 5s+」）做量级参考。
3. **SDK 是否对「读超时」重试未验证**：`llm.py:62` 只写了 `max_retries=2`；OpenAI SDK 的重试规则按其实现，
   本仓没有针对「timeout 是否重试」的测试。若实际不重试，则单次上限是 60s 而非 180s（结论方向不变，数字变小）。
4. **「Java 超时后 Python 仍会跑完」未实测**：依据是「非流式响应在 handler 返回后才发送」的框架语义，
   未在本机做「客户端先断开、看 Python 是否继续跑完」的实验。
5. **分块响应下 `forward` 是否真的等于总时长上限**未实测（若响应体分多块写出且块间隔 <120s，则总时长可超过 120s）。
6. **前端 60s 空闲在真实深查询链路上是否会被触发未实测**：取决于模型是否在工具轮次输出正文（未验证）。
7. **本文未改任何运行时行为**；`read-timeout` 未参数化，理由见 R6 的三条。
8. **风险提示**：如果按 R6 单独调大 `read-timeout` 默认值而不做 R1/R2/R3，会让 analyze 的失败更晚发生、
   单次占用更长，但**不会**让「任务报失败 vs 工作台有数据」的自相矛盾消失，反而更难从日志判断。

---

## 8. 附录：复现用命令

```bash
# 1) 确认 Java 侧超时配置与消费点
rg -n "read-timeout|connect-timeout" dkd-parent/dkd-admin/src/main/resources/application.yml
rg -n "setReadTimeout|setConnectTimeout|buildRestTemplate" dkd-parent/dkd-common/src/main/java/com/dkd/common/agent/support/AgentUpstreamClient.java

# 2) 确认 Python 侧四个超时与图结构
rg -n "llm_timeout_s|callback_timeout_s|read_query_timeout_s|create_task_concurrency" dkd-agent/app/config.py
rg -n "MAX_TOOL_ROUNDS" dkd-agent/app/graphs/chat_graph.py
rg -n "Semaphore|create_task_concurrency" dkd-agent/app/graphs/restock_graph.py

# 3) 确认回调不重试与三分类
sed -n '1,20p' dkd-agent/app/tools/task_tools.py

# 4) 确认批量 create_tasks 的生产可达性（应为 0 命中 app/）
rg -n "Command\(resume=" dkd-agent/app || echo "app/ 内无恢复图调用"

# 5) 确认没有测试断言 120000（做 R6 前置检查）
rg -n "120000|120_000|getReadTimeout" dkd-parent --glob '!**/target/**'

# 6) 前端空闲超时与「取消」语义
rg -n "DEFAULT_IDLE_TIMEOUT|idleTimedOut" dkd-vue/src/utils/sse.js
rg -n "aborted|tryResume" dkd-vue/src/components/AgentAssistant/index.vue
```

---

## 9. 本次工作区的改动范围（便于评审）

| 文件 | 改动 | 性质 |
| --- | --- | --- |
| `docs/agent-timeout-budget-2026-10-29.md` | 新建（本文） | 文档 |
| `docs/agent-operations-runbook.md` | 追加 §八「超时预算」+ 文档头「相关」补一条 + 版本号 V1.0→V1.1 | 文档 |
| （P2-28，同批）`deploy/docker/scripts/import-business-dump.sh`、`dkd-parent/Dockerfile`、`deploy/docker/README.md` | 注释/文档漂移修正 | 注释，零运行时影响 |

> 行为保持不变：`application.yml`、Java 代码、Python 代码、前端代码、compose 挂载与日志路径**均未改**。
