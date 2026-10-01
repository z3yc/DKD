# 智能体服务 · 接口文档（排期任务 4-1）

> 版本：V1.0（2026-10-01）
> 覆盖：Java 网关 `/agent/**`、Java 回调、Python 补货/运维/审计接口
> 相关：`docs/agent-operations-runbook.md`、`docs/DKD智能体接入方案-LangChain-LangGraph.md` V1.3

---

## 一、总体约定

### 1.1 两条链路、两套鉴权（不得混用，AGENTS §7.5）

| 链路 | 调用方 → 被调方 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| **用户链路** | 前端 → Java 网关 `/agent/**` → Python | **用户 JWT**（RuoYi `Authorization: Bearer`） | 网关校验 JWT 后**注入** `X-Agent-User` / `X-Agent-Username`，并**剥掉**客户端自带的同名头 |
| **服务间链路** | Java（定时任务）→ Python `/agents/**`；Python → Java `/agent/callback/**` | **服务间密钥**（`X-Agent-Secret` / `X-Agent-Secret`） | 与用户 JWT 完全独立 |

⚠️ Python 只监听回环，**不解析 JWT**，只信任网关注入的身份头。
因此**本地直连 Python 是写不进业务数据的**：写操作要求 `X-Agent-User`，缺了直接 401
（等于给「写操作必须经网关携带用户身份」这条红线加了一道锁）。

### 1.2 错误信封：两套，别混

| 来源 | 形态 | 判读要点 |
| --- | --- | --- |
| **Java 业务失败** | **HTTP 200 + `{"code":500,...}`** | RuoYi 全局异常处理器把 `RuntimeException` 包成 `AjaxResult.error` —— **不会返回 HTTP 5xx**。只看 HTTP 状态码会把业务失败当成功（前端 0-11 踩过同款坑） |
| **Java 认证失败** | **HTTP 200 + `{"code":401,...}`** | 登录过期与“服务不可用”必须靠信封 code 区分 |
| **Java 过滤器级失败** | HTTP 401 / 404 / 503 | 回调过滤器在安全链之前（order=-200），所以非白名单路径是 **404** 而不是 401 |
| **Python 错误** | **真实 HTTP 状态码** + `{"code":<同状态码>,"msg":...,"data":null}` | 409/400/422/502/503 的 `msg` 是**可行动文案**，前端必须原样展示（见 §3.2） |

### 1.3 全链路追踪

`X-Request-Id`：网关生成（或净化后沿用客户端值）并贯穿 Java→Python→工具→回调→留痕。
Python 侧日志与 `agent_decision_log.request_id` 都带它。**排障时先要这个号**。

---

## 二、用户链路接口（前端调用）

### 2.1 网关

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/agent/status` | 就绪探测。`{"enabled":bool,"upstream":"up"/"down"}`；`enabled=false` 表示一键降级开关关闭 |
| POST | `/agent/chat` | SSE 流式对话（转发到 Python，**不缓冲**逐帧下发） |
| ANY | `/agent/**` | 通用转发（补货工作台接口走这里） |
| POST | `/agent/callback/task` | 服务间回调（不是给前端用的，见 §四） |

### 2.2 SSE 对话 `POST /agent/chat`

请求体：

```json
{ "message": "帮我看看可乐要不要补", "conversation_id": "可选，续用同一会话", "scene": 1 }
```

`scene`：1-通用问答 2-补货 3-诊断 4-运营分析。

**帧类型**（`event: <type>` + `data: <json>`）：

| event | data | 说明 |
| --- | --- | --- |
| `meta` | `conversation_id / request_id / scene / user / mode / model / history_len` | 首帧。`mode` 为 `llm` 或 `echo`；`model` 在 done 帧补全（按**服务端实际返回**的模型计费，不按配置猜） |
| `delta` | `{"text": "..."}` | token 级分片，带 `id:` 供断点续传 |
| `done` | 最终统计 | 正常结束 |
| `error` | `{"msg","code","retryable"}` | **失败也以 error 帧收尾**（不留悬挂气泡）；存储类异常 `code=503` 且文案指向“会话存储”，其它 `code=500`，均带 `retryable=true` |

**断点续传**：请求头带 `Last-Event-ID: <最后一个 delta 的 id>` → 服务端**不重跑本轮**，只补发未收到的帧。
⚠️ 前端正常新一轮**不要**带这个头（会被当成续传请求，实测会导致“会话不存在”直接断掉——见 `api/manage/agent.js` 注释）。

### 2.3 补货工作台 `GET /agent/restock/**`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/agent/restock/plans?plan_date=YYYY-MM-DD` | 清单（缺省今天）。返回 `{planDate, stats:{pending,urgent,totalQuantity}, plans[]}` |
| POST | `/agent/restock/plans/{id}/confirm` | 确认建单（幂等、可重试，见 §3.2） |
| POST | `/agent/restock/plans/{id}/adjust` | 调整（见下） |
| POST | `/agent/restock/plans/{id}/skip` | 跳过（原因必填，终态） |
| POST | `/agent/restock/plans/{id}/restore` | 恢复建议（原因必填，**仅限当天**） |
| GET | `/agent/restock/pause` | 自动分析开关状态 |
| POST | `/agent/restock/pause` / `/resume` | 暂停 / 恢复自动分析（原因必填） |

**写操作统一要求** `X-Agent-User`（网关注入）；缺了返回 401。

**adjust 的两种模式互斥**（同时给会被 400 拒绝：分不清人工意图）：

```json
// 模式一：人工直接给数量（channel_code 缺省取该计划第一个货道）
{ "reason": "销量预估偏高", "quantity": 5, "channel_code": "1-1" }

// 模式二：按新参数重算（服务端按新窗口/服务水平重算，数据源与 06:00 分析同源）
{ "reason": "现场陈列空间受限", "windowDays": 7, "serviceLevel": 0.95 }
```

⚠️ 字段名坑：`windowDays` / `serviceLevel` / `coverageDays` 是后端显式 alias，
但 **`channel_code` 没有 alias**，必须发 snake_case——发成 `channelCode` 会被 Pydantic 当未知字段丢弃，
表现为“我选了 A 货道却改了 B 货道”。

**计划状态机**（对齐 `docs/ddl/agent_tables.sql` 列注释）：

```
1-建议 ──┬─→ 2-已调整 ──┬─→ 4-已建单 ──→ 5-已复盘(终态)
         │              │
         └─→ 3-已跳过 ──┘   └─→ 6-待指派（无匹配接单人）
3-已跳过 → 1-建议（仅“恢复建议”动作，且仅限当天）
1/2/6 → 7-建单中（CAS 占位，非业务状态）→ 4-已建单
```

---

## 二·补、助手的能力边界与只读工具（2026-10-01 新增；同日升级为**按场景**挂载）

`/agent/chat` 在 `DKD_AGENT_USE_LLM=1` 时会挂**只读工具**，助手因此能真取数再回答。
**写操作一律不在对话里**（AGENTS §7.9：首期所有写操作必须 human-in-the-loop）。

### 场景 → 工具矩阵（**场景决定能力面**）

| 场景（`scene`） | 可用工具 | 说明 |
| --- | --- | --- |
| 1 通用问答 | **无** | 提示词如实说明“本场景查不了”，让用户切到 2/3 场景；不许凭印象编数 |
| 2 补货 Agent | `list_restock_suggestions` / `get_machine_inventory` / `list_inflight_work_orders` / `get_sales_summary` | 补货数据类问题 |
| 3 诊断 Agent | `find_devices` / `list_device_anomalies` / `search_device_history` + `get_machine_inventory` / `list_inflight_work_orders` | 设备消歧 + 异常检测 + 历史维修单（见下） |
| 4 分析 Copilot | **无** | 未接数据分析工具；只能解释口径/指出该去哪个页面看 |

> 为什么按场景挂、而不是一次给全部：多挂一个工具就多一份 schema（每轮都进 prompt），
> 还会把模型注意力引到无关问题上。**绑定名单与提示词名单来自同一处配置**
> （`app/prompts/assistant_chat.TOOL_NAMES_BY_SCENE`），并有单测断言两边一致——
> “提示与能力脱节”这个 bug 本项目已经踩过两次。

#### 场景 2（补货）

| 工具 | 能回答什么 | 返回要点 |
| --- | --- | --- |
| `list_restock_suggestions` | 某日（默认今天）补货建议：待处理/紧急条数、各设备货道建议与理由、接单人、自动分析是否暂停 | 带 `caliber`（紧急=priority 4，只含未完成计划）；**默认只回未完成计划**，明细只给含紧急货道的计划（`detail=true` 才全给）；无计划时给 `hint`（暂停 or 定时任务未跑） |
| `get_machine_inventory` | 设备档案（点位/区域/运行状态）+ 各货道当前库存与容量 | 带 `dataConsistent`：库存表与货道档案不一致时如实标记（FIX-5），不静默选边 |
| `list_inflight_work_orders` | 在途工单（1-待接单 / 2-进行中） | 带口径说明：在途工单会让新建单被 Java 防重拒绝 |
| `get_sales_summary` | 近 N 天（默认 7，上限 30）出货成功销量与金额，按货道汇总 | 带 `caliber`：只统计 `status=2`；不可归因货道的订单不计入 byChannel |

#### 场景 3（诊断）

| 工具 | 能回答什么 | 返回要点 / 诚实标记 |
| --- | --- | --- |
| `find_devices` | 按**口述线索**定位设备：编号精确 → 编号前缀 → 点位名 → 地址（如“五道口”） | 带 `matchReason`；多台命中时给 `hint` 要求用户确认；**一条都没有时明确禁止模型自己编号** |
| `list_device_anomalies` | 某设备近 N 天（默认 90，上限 3650）的异常：卡单（`status=1` 超阈值）、出货失败未退款（`status=3` 且 `pay_status∈{1,2}`）、库存无单扣减/账实不符 | 带 `byKind` 分组计数、`caliber`、`dataWarning`（`tb_inventory_log` 为空时“0 条 ≠ 没问题”）；窗口被夹取时给 `windowClampedFromDays` |
| `search_device_history` | 历史维修工单检索（**规则库**，不是语义检索）：`same_device → same_model → same_region` 三级降级 | 每条带 `phenomenonUsable`（描述能不能当故障现象）与 `confidence`+`confidenceBasis`；无可用案例时给 `noEvidenceHint` + `mustSay`（提示词要求必须照做） |
| `build_device_diagnosis` | **结构化诊断结论**（2-4）：证据清单 + 未知项 + 可执行的平台动作 + 由证据强度算出的 `confidence` | **刻意没有病因字段**（本平台无结构化故障知识库，FIX-22）：`evidence[]` 每条带来源表与口径、`unknowns[]` 显式列出平台观测不到的部分（如库存日志未采集）、`recommendedActions[]` 只指向平台真实入口；`confidenceBasis` 说明置信度源自哪几条证据。附带 `mustSay` 要求模型不得自行补病因/处置步骤 |

### `done` 帧的 `conclusion`（2-4 的前端契约）

当本轮真的调用了 `build_device_diagnosis` 时，`done` 帧带一个 **`conclusion` 投影**（否则严格为 `null`）：

| 字段 | 说明 |
| --- | --- |
| `innerCode` / `confidence` / `confidenceBasis` / `cannotConclude` | 设备与置信度档位（+**依据**，前端必须一起显示） |
| `unknowns[]` / `recommendedActions[]` | 未知项与平台动作（各≤10 条） |
| `evidenceCount` / `evidenceKinds[]` | **只给条数与类型**：逐条证据（含 `detail`）是给模型看的，不进帧 |
| `caliber` | 口径 |

> 为什么发**投影**而不是原样转发：实测原始结论约 5~6KB（10 条证据各带 detail），
> 初版设的 4000 字符护栏把整条结论丢掉 → **卡片静默不渲染**（接口 200、回复正常、只差卡片）。
> done 帧还要经 Java 网关逐字节转发，帧越大越容易被拖慢/截断。
> 单测锁了两条：**投影里不得出现 `detail`**、**投影 < 2000 字符**。

> ⚠️ **为什么结论里没有“病因/可能原因”**：本平台维修工单只有备注字段（FIX-22 实测：5 条维修单描述是
> `111`/`后台工作人员取消`），**没有任何可检索的故障原因/处置知识**。在这种数据上给“病因排序”就是编领域知识。
> 因此 `build_device_diagnosis` 只输出**可复核的三样**（证据 / 未知 / 平台动作）+ 由规则算出的 `confidence`；
> 等 FIX-22 落地（结构化现象/原因/处置或厂商故障码表）之后，才会在这里按证据匹配案例。
> ⚠️ `confidence` 的档位对应前端进度条刻度（high/medium/low/none），**它是证据强度的刻度，不是概率**——
> 所以前端必须同时展示 `confidenceBasis`，否则“55%”会被读成“55% 的概率是这个原因”。

> ⚠️ 诊断场景的**诚实契约比能力本身重要**：本平台维修工单只有备注字段（FIX-22），
> 实测 5 条维修单的描述是 `111`×4 + `后台工作人员取消`×1——**没有一条是故障现象**。
> 因此 `phenomenonUsable=false` 的描述只能当“这台机器修过”的线索，**不得当故障现象引用**；
> 助手必须主动说出“没有可用的历史案例”。实测回复样例：
> 「…查到 2 条历史维修工单，但**没有一条能当故障现象用**，所以答案是：**没有可参考的历史案例**。」

**行为契约（有测试锁定）**

- **轮次上限 3 轮**：超过则不再放行工具，并如实收尾（“我连续查询了 3 轮仍未能得出结论…”），
  不会死循环也不会给空回复。
- **工具失败**：以 `{"error": "..."}` 形式喂回模型（不回显原始异常文本，避免连接串进 prompt），
  模型必须如实说“查询失败”，不得猜数。
- **模型不支持 function calling**：服务**启动即拒绝**（不是每条消息 502）。
- **模式可见**：`meta.mode=echo` 表示服务跑在链路自检模式（不调模型），前端会显示提示条；
  `mode=llm` 时前端显示服务端实际返回的模型名（实测配置 `deepseek-chat` 会路由到 `deepseek-flash`）。
- **成本**：带工具的一轮 ≈ **3.5k input token**（system + 工具 schema + 工具返回值）。
  2026-10-01 已做一轮优化（只回未完成计划 / 按需给货道明细 / reason 截断 / 提示词瘦身），
  **实测同一句「先看紧急的」input token 3934 → 3470（-11.8%）**；按场景挂工具后，
  单场景只承担自己那份 schema。
- **诊断场景的独立性**：`scene=3` 的工具集与 `scene=2` **不共享**（除库存/在途两个通用件），
  所以“补货场景问诊断问题”不会悄悄去查故障案例——那份 schema 根本没给它。

## 二·补二、决策审计页接口（Java manage，只读）

审计页（3-7）由 Java 侧提供三个**只读** GET，都在 `/manage/agent/decision-log` 下，
权限分别是 `manage:agentAudit:list` / `manage:agentAudit:query`：

| 接口 | 参数 | 说明 |
| --- | --- | --- |
| `GET /list` | `scene` / `triggerType` / `action` / `targetType` / `targetId` / `requestId` / `result` / `params.beginTime` / `params.endTime` + 分页 | 分页列表；**不返回** `input_context` / `llm_output` 两个 JSON 列（该表随每次对话增长，列表塞大 JSON 会让分页越来越慢） |
| `GET /{id}` | 主键 | 单条详情，**含**两个 JSON 列（审计回放用） |
| `GET /trace` | `requestId`（必填） | **按请求号串联同一次请求的全部留痕**，`order by id asc` + `limit 50`，复用详情列片段（因此带 JSON 上下文） |

**为什么 `trace` 要单独一个接口，而不是“列表按 requestId 过滤”**：列表是 `order by id desc` 且分页，
把同一条链路的时间顺序**打反了**，而审计要回答的恰恰是先后顺序（状态机放行 → 回调建单 → 回写状态）。
另外 `requestId` 为空时接口**直接报错**（`code=500` + 中文原因），而不是退化成“最近 50 条留痕”——
那种结果看起来像一条链路，实际与本次请求无关，比报错更危险。

> 时间过滤一律用**范围比较**（`create_time >= ... and < DATE_ADD(..., INTERVAL 1 DAY)`），
> 禁止 `date_format()` 包裹索引列（AGENTS §10）。该约定由 `AgentDecisionLogMapperSqlTest` 锁死
> （含「列表不带 JSON / 详情与链路带 JSON」这一列集合差异）。

## 三、状态码语义（前端必须按这个提示，不能一律说“操作失败”）

### 3.1 对照表

| 码 | 含义 | 运营看到的应该是 |
| --- | --- | --- |
| 200 | 成功 | — |
| 400 | 业务规则拒绝（原因留空 / 服务水平越界 / 两种模式同时给） | **原样展示 `msg`** |
| 401 | 缺 `X-Agent-User`（未经网关或登录过期） | 提示重新登录 / 报告问题 |
| 404 | 计划不存在或不属于该日期 | 刷新清单 |
| 409 | 状态机拒绝 / 并发冲突（如“已建单的计划不能重复确认”“正在建单中”） | **原样展示 `msg`** |
| 422 | 字段校验（Pydantic） | 展示字段名 |
| 502 | 回调 Java 建单失败 | **原样展示 `msg` + 告诉运营可原样重试** |
| 503 | 补货服务未初始化 / 会话存储不可用 | 提示稍后重试并报障 |

### 3.2 「建单失败可原样重试」的契约（FIX-15）

`POST .../confirm` 的结果形态：

| 结果 | 状态码 | 计划状态 | 运营下一步 |
| --- | --- | --- | --- |
| 建单成功 | 200 | → 4-已建单（无接单人时 → 6-待指派） | 无需操作 |
| **建单失败** | **502** | **不变** | **原样再点一次「确认建单」**（无需改库、无需等次日 06:00） |
| 重复确认 | 200 | 保持 4 | 无需操作（幂等，不会建出第二张工单） |
| 状态机/并发拒绝 | 409 | 不变 | 刷新后按提示处理 |

**为什么失败时状态不变**：“改回建议”会让运营以为没点过而重复点，也可能把已建出的工单状态改乱。
代价是重试路径变成隐式的，因此写进契约并由测试锁定
（`tests/test_api_restock.py::test_confirm_retry_after_502_succeeds_without_manual_state_fix`）。

**为什么不做传输层自动重试**：建单是**非幂等**动作，网络抖动时自动重试会重复建单；
重试决策交给调用方（人工或前端）。

---

## 四、服务间接口

### 4.1 Python → Java：建单回调

```
POST /agent/callback/task        Header: X-Agent-Secret: <DKD_AGENT_SERVICE_SECRET>
                                 Header: X-Request-Id: <全链路 rid>
Body: TaskDto（见 Python `RestockPlan.to_task_dto`）
```

响应（成功）：`HTTP 200 + {"code":200,"data":{"taskId":...,"taskCode":...}}`

拒绝路径（均不写库）：无密钥 **401** / 伪造 **401** / 未配置密钥 **503** / 非白名单路径 **404** /
业务校验（设备状态、防重、员工区域）→ `HTTP 200 + code=500` 且 **message 原样回传**。

**客户端失败分类**（`app/tools/task_tools.py`）——三者上层反应完全不同，不能合并成一个异常：

| 类 | 触发 | 上层动作 |
| --- | --- | --- |
| `CallbackAuthError` | 401/403 | 运维问题，**不重试**（密钥不一致） |
| `CallbackUnavailableError` | 超时 / 连不上 / 5xx / 503 | 可稍后重试；**超时文案含下一步动作与可能原因**（FIX-16 产出） |
| `CallbackBusinessError` | HTTP 200 + code≠200 且带 message | **原文给运营看**（如“设备有未完成工单”） |

### 4.2 Java → Python：定时分析（1-9）

```
POST /agents/restock/analyze     Header: X-Agent-Secret: <密钥>
```

返回 `data.status`：`analyzed`（已生成建议并中断等确认）/ `skipped`（**开关关闭或被暂停**）。
⚠️ 被暂停返回的是 **HTTP 200 + status=skipped**，不是错误——Java 侧**不重试、不告警**
（AGENTS §6.4：把“按计划停用”报成失败会让团队对告警脱敏）。

Java 定时任务的重试约定：**4xx 不重试，5xx 与传输失败才重试**
（`agent.analyzeRetryMax` 默认 2 + 线性退避）；最终失败**抛异常**交给 `sys_job_log`。

### 4.3 Python → 运维报表

```
GET /agents/usage/summary?days=1     Header: X-Agent-Secret: <密钥>
```

### 4.4 Java：决策审计（3-7）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/manage/agent/decision-log/list` | `manage:agentAudit:list` | 分页；过滤 `requestId/scene/userId/triggerType/action/targetType/targetId/result` + `params.beginTime`/`params.endTime`。**不返回** `input_context`/`llm_output` |
| GET | `/manage/agent/decision-log/{id}` | `manage:agentAudit:query` | 详情，**含**两个 JSON 上下文列（审计回放用） |

**只读**：没有任何写/删接口。留痕是审计证据（AGENTS §6.3），
Mapper 只暴露 `select*`（有单测断言），前端也没有写操作按钮（有浏览器断言）。

---

## 五、时间与时间窗约定（容易出错）

1. **半开区间**：所有查询用 `create_time >= start AND create_time < end`。
   上界写成 `<=` 会把那一刻的数据同时算进相邻两期。
2. **禁用 `date_format()` 包裹索引列**（AGENTS §10）。实测（`scene = ? AND 时间范围`，
   FORCE INDEX 隔离变量）：范围比较 `key_len=7, rows=1`；`date_format()` 只能 `key_len=1, rows=55`
   ——只有前者能把 `create_time` 真正推进索引。
   上界若需要“含当天”，用 **对参数** 做 `DATE_ADD(#{params.endTime}, INTERVAL 1 DAY)`（函数不作用于索引列）。
3. **销量口径固定 `status = 2`（出货成功）**，可配置 `DKD_AGENT_SALES_ORDER_STATUS`。
   口径依据：`VmSystemConstant.java:83-102`（0 创建/1 支付/2 出货成功/3 出货失败/4 失效）
   ——`status >= 1` 会把“出货失败/失效”算进营收（FIX-3 修的正是这个）。
4. **不可归因订单**：`tb_order.channel_code` 为空的订单**不进**按货道聚合（无法归因），
   但对账脚本用守恒断言保证它们不被静默丢弃（可归因 + 不可归因 == 全局）。

---

## 六、接口变更义务（AGENTS §4.3）

新增/修改接口时必须同步：
1. 本文档 + Swagger 注解；
2. 若涉及时间口径或状态语义变化 → 同步 `docs/DKD智能体接入项目排期计划.md` 的 FIX 清单；
3. 若涉及 Agent 读写边界变化 → **先过评审**（AGENTS §7.3 是红线：读走 MySQL 只读白名单、
   写一律回调 Java；`agent_*` 自有表可 SELECT/INSERT/UPDATE，不授 DELETE）。
