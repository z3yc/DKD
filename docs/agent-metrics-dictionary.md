# 智能体效果指标字典 V1（阶段 1 / 任务 1-3）

> **状态：🟡 口径已定义并冻结；4 项数据源未就绪**（降级率/校准率待 **5-6**、取数准确率待 **1-4 真实评测集 + 5-1 流水线**、业务效果指标待 **生产基线**、金额成本待 **单价表**）。
> **日期：2026-11-04** · 归属：计划表 1-3 → M1（W1 周二~周三）
> **唯一口径声明**：S7 驾驶舱（6-1）、评测流水线（5-1）、效果评估（6-8）**共用本文件**；其他文档不得另立公式。
> 依据：`docs/智能体实现详细计划表-2026-11-03.md` 1-3/5-1~5-6/6-1/6-8、`docs/DKD业务规范-清晰版.md` §4.5/§4.7/§8-6、
> `docs/ddl/agent_tables.sql`（`agent_restock_plan`/`agent_decision_log`/`agent_message`）、
> `docs/ddl/agent_run_step.sql`（`agent_run`/`agent_step`）、`docs/ddl/agent_config.sql`（`agent_log`）。
> **纪律：没有数据源的指标显示「—（数据源待 X）」，不填演示数字**（对齐旧排期对驾驶舱前身页面的同一要求）。

---

## 0. 全局规则（所有指标共用）

| # | 规则 | 说明 |
| --- | --- | --- |
| R-1 | **时间窗一律半开区间** `[begin, end)` | 与业务规范 §4.5 一致；`plan_date` 按日期、`create_time`/`started_at` 按时间点 |
| R-2 | **计数按决策时点归属窗口**（`agent_decision_log.create_time`），不按 `plan_date` | 昨日计划今日处理很常见；按 plan_date 会把“今日处理量”算到昨日。两种口径不可混用，本文件统一用决策时点 |
| R-3 | **必须带 n**，n<100 不作对外口径（§4） | 驾驶舱每卡脚注显示 `n=…`、窗口、口径版本 |
| R-4 | **分场景统计**，不跨场景合并 | `chat` / `restock` / `fault` 的“运行”语义不同（chat 不写 `agent_run`） |
| R-5 | **每个数字可对账** | 附 SQL（§2）或评测报告；对不上就说明口径有问题，先修口径再报数 |
| R-6 | 状态图例：✅ 可算 ｜ 🟡 部分可算/需注意 ｜ ❌ 数据源未就绪 | 未就绪项不得在 S7 上显示数字 |

---

## 1. 指标总表

| ID | 指标 | 公式（简） | 主数据源 | 分母 | 状态 | 对应任务 |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | 补充建议**采纳率** | 直接确认建单的计划 / 已终结决策计划 | `agent_decision_log`（action/result）+ `agent_restock_plan.status` | 窗口内已终结（status 3/4/5）的计划数 | ✅ 可算（本机 n 会 <100） | 6-1 |
| M2 | 补充建议**调整率** | 调整后建单的计划 / 同上 | 同上（`restock.adjust` 事件） | 同上 | ✅ 可算 | 6-1 |
| M3 | 补充建议**跳过率** | 最终跳过的计划 / 同上 | 同上 + `status=3` | 同上 | ✅ 可算 | 6-1 |
| M4 | 补充建议**待指派率**（附加，不入三率） | `status=6` 计划 / 已尝试决策计划 | `agent_restock_plan.status` | status ∈ (3,4,5,6) | ✅ 可算 | 6-1 |
| M5 | **LLM 校准率** | 校准成功的计划行 / 计划行总数 | **待落** `agent_restock_plan.llm_calibrated`（5-6） | 窗口内生成的计划行 | ❌ 数据源未就绪 | 5-6 |
| M6 | **LLM 降级率** | 降级的计划行 / 计划行总数（= 1 − M5） | 同上 | 同上 | ❌ 数据源未就绪 | 5-6 |
| M7 | **取数准确率** | 取数正确的评测样本 / 有效样本 | 5-1 评测报告（评测集 1-4） | 有效样本（排除 BLOCKED） | ❌ 待真实评测集 | 5-2 / 1-4 |
| M8 | **单次运行 token 成本** | token 合计 / 成功运行数 | `agent_step.input_tokens/output_tokens`（restock/fault）、`agent_message.tokens_in/tokens_out`（chat） | 成功运行数（status=`done`） | 🟡 token 可算，**金额不可算** | 5-5 / 6-1 |
| M9 | **失败运行率** | 失败运行 / 终态运行 | `agent_run.status/stop_reason/started_at` | 终态运行（done/failed/interrupted） | 🟡 restock/fault 可算；**chat 不可算** | 6-2 / 6-1 |
| M10 | 业务效果：**缺货率** | 见业务规范 §4.5（本文件不重定义） | `tb_inventory.current_stock <= min_stock` / `tb_channel` | 有库存档案的货道数 | ❌ 基线待生产数据 | 6-8 |
| M11 | 业务效果：**排单耗时** | 见旧排期 M5 口径（本文件不重定义） | `tb_task` 建单→完成时间差 | 已完成补货工单 | ❌ 基线待生产数据 | 6-8 |

> M10/M11 只引用业务规范与旧排期口径，**不在本文件重新发明公式**；G4 报告（6-8）必须写基线来源、样本量、窗口。

---

## 2. 逐指标定义与对账 SQL

### 2.1 M1 采纳率 / M2 调整率 / M3 跳过率（补货建议）

**业务含义**：运营看到建议后的三种去向——原样接受建单、改数量后建单、直接跳过。三率互斥，同一 cohort 内之和 = 100%。

**分子 / 分母**：

| 指标 | 分子 | 分母 |
| --- | --- | --- |
| 采纳率 | 最终 status ∈ {4 已建单, 5 已复盘} 且**没有任何成功的 `restock.adjust` 事件**的计划 | 窗口内被人工决策过、且最终 status ∈ {3,4,5} 的计划数 n |
| 调整率 | 最终 status ∈ {4,5} 且**存在成功的 `restock.adjust` 事件**的计划 | 同上 |
| 跳过率 | 最终 status = 3（已跳过）的计划 | 同上 |

**⚠️ 口径坑（必须按此实现，否则数字是错的）**：
`agent_restock_plan.adjust_reason/adjusted_time` **不能用来算调整率**——`app/services/restock_service.py` 的 `save_decision()` 在所有动作（含确认建单）都会写 `adjust_reason=decision.reason`，而确认动作的 `decision.reason` 为 `None`，会把之前调整留下的原因**覆盖为 NULL**。因此“调整后再建单”的行在 plan 表里看起来像“从未调整过”。**必须以 append-only 的 `agent_decision_log` 为判据**。

**对账 SQL（MySQL 8）**：

```sql
-- 参数 :begin / :end = 半开区间（按决策时点归属，R-2）
WITH decided AS (
  SELECT l.target_id AS plan_id,
         MAX(CASE WHEN l.action = 'restock.adjust' THEN 1 ELSE 0 END) AS has_adjust
  FROM agent_decision_log l
  WHERE l.del_flag = '0'
    AND l.target_type = 'plan'
    AND l.action IN ('restock.confirm','restock.adjust','restock.skip')
    AND l.result = 1                                   -- 1=成功（拒绝/失败不算决策）
    AND l.create_time >= :begin AND l.create_time < :end
  GROUP BY l.target_id
),
final_state AS (
  SELECT p.id AS plan_id, p.status
  FROM agent_restock_plan p
  WHERE p.del_flag = '0' AND p.status IN (3,4,5)        -- 已终结决策
)
SELECT
  COUNT(*)                                              AS n,
  SUM(f.status IN (4,5) AND d.has_adjust = 0)           AS adopted,
  SUM(f.status IN (4,5) AND d.has_adjust = 1)           AS adjusted,
  SUM(f.status = 3)                                     AS skipped,
  ROUND(SUM(f.status IN (4,5) AND d.has_adjust = 0) / COUNT(*), 4) AS adopt_rate,
  ROUND(SUM(f.status IN (4,5) AND d.has_adjust = 1) / COUNT(*), 4) AS adjust_rate,
  ROUND(SUM(f.status = 3) / COUNT(*), 4)                AS skip_rate
FROM decided d
JOIN final_state f ON f.plan_id = CAST(d.plan_id AS UNSIGNED);
```

**边界规则**：
1. 跳过→恢复（`restock.restore`）→再决策：按**最后一次**终结状态计（恢复只改状态不改历史，日志都在，口径不受影响）；
2. 多次调整后建单：`has_adjust=1`，计一次“调整”，不按次数重复计；
3. 计划在窗口内被决策但仍是 status 1/2/6/7（未终结）：**不进分母**（还在流程里，早晚会变）；
4. 建单失败（`result=2`）或状态机拒绝（`result=3`）：不是人工决策结果，不进分子分母，单列“操作失败率”排障用。

**M4 待指派率（单列，不并入三率）**：

```sql
-- 分母：窗口内已尝试决策的计划（status 3/4/5/6）
SELECT ROUND(SUM(status = 6) / COUNT(*), 4) AS unassigned_rate, COUNT(*) AS n
FROM agent_restock_plan
WHERE del_flag = '0' AND status IN (3,4,5,6)
  AND plan_date >= :begin_date AND plan_date < :end_date;
```
> M4 按 `plan_date` 归属（它是计划本身的属性，不是处置时点）；与 M1~M3 的归属口径不同，驾驶舱需分开标注窗口。

### 2.2 M5 LLM 校准率 / M6 LLM 降级率

**定义（目标口径）**：以**计划行**（`agent_restock_plan` 一行 = 一台设备当日建议）为单位：

- 校准率 = 该行建议“LLM 校准成功应用”的行数 / 窗口内生成的全部计划行数
- 降级率 = 校准失败、退化为纯统计基线的行数 / 同一分母（= 1 − 校准率）
- 分母**包含**被跳过/待指派的行（校准发生在建议生成阶段，是否跳过是之后的事）

**数据源现状（2026-11-04 核对，不得含糊）**：

| 候选 | 结论 |
| --- | --- |
| `agent_restock_plan` 现有列 | **没有** `llm_calibrated` 字段（5-6 才加）；原型/前端 grep 也没有 calibrated 字段 |
| `app/graphs/restock_calibration.py` 的 `outcome.applied` / `calibration_notes[vm].applied` | 语义正确，但 `calibration_notes` **只存在 LangGraph state**，未落库（全仓 `calibration_notes` 无 DB 写入方） |
| `agent_decision_log.input_context.calibrated`（`restock.analyze` 动作） | **不能用**：它等于 `bool(calibration_notes)`，而 notes 对每台设备都会写（失败也写 issues），所以只要有计划就为 `true`。拿它算会得出“校准率≈100%”的假象 |

**结论**：M5/M6 **当前不可计算**。在 5-6 落地前，S7 降级卡显示「—（数据源待 5-6）」，告警条不得据此报警。

**5-6 落地时的最小数据契约（建议，DDL 变更按 AGENTS §2.4 / §8 评审并附回滚）**：

```sql
-- 建议新增列（5-6 实现时评审；不在此文件执行）
ALTER TABLE agent_restock_plan
  ADD COLUMN llm_calibrated TINYINT DEFAULT NULL
  COMMENT '1=LLM 校准已应用 0=降级为统计基线 NULL=本期未接入/未产生';
```

- 写入时机：校准节点结束、计划落库前（`save_plan` / 幂等 upsert 路径）；
- 口径 SQL：`校准率 = SUM(llm_calibrated=1)/COUNT(*)`；`降级率 = SUM(llm_calibrated=0)/COUNT(*)`；`NULL` 不计入分母并在脚注显示 `unknown=…`；
- 验收：构造“LLM 超时”用例，断言该行为 `0` 且前端显示「未校准」；构造成功用例断言 `1`。

### 2.3 M7 取数准确率

| 项 | 定义 |
| --- | --- |
| 样本 | 1-4 评测集的一条 case（`docs/agent-eval-set-2026-11-04.md` 定义格式） |
| 正确判定 | 三要素**全部**满足才计 1：① 选对工具/指标（`expected_metric`/`expected_tables`）② 参数正确（时间窗/维度，`expected_params`）③ 结果数值在容差内（`expected_value` ± `tolerance`，计数类容差 0） |
| 主口径 | 严格准确率 = 全对条数 / 有效样本数（不做部分得分加权，避免“看起来 90% 其实全错时间窗”） |
| 归因分项 | 工具选择准确率 / 参数准确率 / 数值准确率（三项各自独立统计，用于 5-2 定位问题） |
| 有效样本 | 排除环境类拦截：`BLOCKED`（服务未起、配额 429、依赖缺失）既不算通过也不算失败；报告必须单列 BLOCKED 条数 |
| 负样本 | 评测集须含“必须拒答”样本（越权/超范围/无数据）：正确拒答计 1，编造数据计 0 且单独统计“幻觉率” |
| 目标 | **≥90%**（5-2，对齐旧排期 3-3 验收口径） |
| 数据源 | 5-1 评测流水线报告（评测集版本 + 代码/配置版本 + 模型名 + n）；**不是生产库统计** |

**报告最小字段**（5-1 落地时照此输出）：`evalSetVersion` / `configVersion` / `model` / `n` / `accuracy` / `tool_accuracy` / `param_accuracy` / `value_accuracy` / `hallucination_count` / `blocked_count` / `runAt`。

### 2.4 M8 单次运行 token 成本

**单位是 token，不是金额**——仓库内**没有模型单价表**，金额换算需业务提供单价（待外部输入），在此之前只报 token，不得自拟价格。

| 场景 | 单次口径 | 数据源 |
| --- | --- | --- |
| chat（对话） | 一次 SSE 请求的 token 合计：同一 `request_id` 取**最后一条**助手消息（`msg_role=2`）的 `tokens_in + tokens_out`（避免同请求多行重复累计） | `agent_message.tokens_in/tokens_out/request_id/msg_role` |
| restock / fault | 一次 run 的 token 合计：`SUM(input_tokens + output_tokens)` | `agent_step.input_tokens/output_tokens`（按 `run_id` 分组） |
| 平均单次成本 | token 合计 / **成功运行数**（`status='done'`） | 上面两路 |
| 失败成本（单列） | `status IN ('failed','interrupted')` 的运行 token 合计 | `agent_run` + `agent_step` |

```sql
-- restock / fault：按 run 成本 + 平均值（成功运行）
WITH run_cost AS (
  SELECT r.run_id, r.status,
         COALESCE(SUM(s.input_tokens),0) + COALESCE(SUM(s.output_tokens),0) AS tokens
  FROM agent_run r
  LEFT JOIN agent_step s ON s.run_id = r.run_id
  WHERE r.del_flag = '0'
    AND r.started_at >= :begin AND r.started_at < :end
  GROUP BY r.run_id, r.status
)
SELECT
  SUM(tokens)                                                   AS total_tokens,
  SUM(status = 'done')                                          AS n_done,
  ROUND(SUM(CASE WHEN status='done' THEN tokens END)
        / NULLIF(SUM(status='done'),0), 1)                      AS avg_tokens_per_success,
  SUM(CASE WHEN status IN ('failed','interrupted') THEN tokens END) AS wasted_tokens
FROM run_cost;
```

> **注意（易错）**：`GET /agents/usage/summary` **只聚合 `agent_message`**（对话链路），不含 `agent_step`。所以：
> 用 summary 报“整体成本”会漏掉补货/诊断成本；驾驶舱成本卡必须按场景分别取数，并标注来源表。
> 另外 `agent_message.model` 记的是**服务端实际返回**的模型（实测 `deepseek-chat` 会路由到 `deepseek-flash`），按模型分摊成本时以该字段为准。

### 2.5 M9 失败运行率

| 项 | 定义 |
| --- | --- |
| 失败 | `agent_run.status IN ('failed','interrupted')`；其中 `interrupted`（服务重启/人工中断）**单列**，因为它可解释性不同（不是能力失败） |
| 分母 | 窗口内 `started_at` 落在窗口的**终态运行**：`status IN ('done','failed','interrupted')` |
| 排除 | `running`（运行中）与 `waiting_confirm`（等人工确认，非终态）——它们还没结果，进分母会把“慢”算成“错” |
| 不算失败 | `agent.enabled=false` 或补货暂停开关（`agent_restock_pause`）导致的跳过：记 WARN/INFO 正常返回（AGENTS §6.4 四条约定），**既不进分子也不进分母**，单列 `skipped` 次数 |
| 补充口径 | 定时任务失败率 = `sys_job_log.status=1` 的行 / 同任务执行总行（业务规范 §4.7）；ERROR 告警以 `agent_log`（2-4 后）为准 |

```sql
SELECT
  r.scene_code,
  COUNT(*)                                              AS n_terminal,
  SUM(r.status = 'failed')                              AS failed,
  SUM(r.status = 'interrupted')                         AS interrupted,
  ROUND(SUM(r.status IN ('failed','interrupted')) / COUNT(*), 4) AS fail_rate
FROM agent_run r
WHERE r.del_flag = '0'
  AND r.started_at >= :begin AND r.started_at < :end
  AND r.status IN ('done','failed','interrupted')
GROUP BY r.scene_code;
```

**现状限制（如实登记）**：
- `restock` / `fault` 有 `agent_run` 数据，可算；
- **`chat` 不写 `agent_run`**（契约 §5.2：现有链路只写 decision_log/message），所以 chat 的“失败运行率”当前**不可算**；
  2-4 落地后用 `agent_log`（`status='error'` 按 `request_id` 计数）补 chat 口径，届时应新增 M9b 而不是改 M9 定义（避免历史数字不可比）。

---

## 3. S7 驾驶舱四卡映射（页面数字只允许来自本字典）

| 卡 | 展示指标 | 数据源就绪 | 未就绪时的页面表现 |
| --- | --- | --- | --- |
| 采纳/效果卡 | M1 采纳率 + M2 调整率 + M3 跳过率（合计 100%）+ n | ✅（数据量可能 n<100） | 正常显示 + `n=…` + “n<100 不作对外口径”脚注 |
| 降级卡 | M5 校准率 / M6 降级率 + 告警条联动 | ❌ 待 5-6 | 显示「—（数据源待 5-6）」；告警条不据此报警 |
| 成本卡 | M8 单次运行平均 token + 分场景 + 日环比 | 🟡 token 可算；金额不可 | 显示 token；金额位显示「—（待单价表）」 |
| 失败卡 | M9 失败运行率 + 分场景 + interrupted 单列 | 🟡 restock/fault 可算；chat 待 2-4 | chat 行显示「—（待 2-4 日志口径）」 |
| 对比卡（P1） | M1 vs 规则版建议（5-4 双跑） | ❌ 待 5-4 | 显示「—（待 5-4）」 |
| 待我处理 | 等待时长 / 超时阈值 | 🟡 依赖三处待办来源 | 无数据源行不显示假数 |

---

## 4. 样本量规则（n<100）

| 规则 | 内容 |
| --- | --- |
| N-1 | 每个指标必须同时给出 **n（分母有效样本数）**、统计窗口、口径版本；缺一不展示 |
| N-2 | **n < 30**：只允许内部调试查看，页面标「样本不足」，**不得写进任何报告/对外材料** |
| N-3 | **30 ≤ n < 100**：页面正常显示，但必须标注「n<100，不作对外口径」；**不得作为 KPI 承诺或验收结论** |
| N-4 | **n ≥ 100**：可作内部验收依据；对外（效果评估报告/汇报）还要求**连续 2 个同口径窗口**数值稳定（波动 ≤ ±5 个百分点） |
| N-5 | 不得把不同窗口、不同评测集版本、不同口径版本的数字拼成一条趋势线；换版本必须断开并注明 |
| N-6 | 本机/开发库样本极少（业务规范 §8-6），**任何基于开发库的数字都只能标“开发库示例”** |

---

## 5. 告警阈值建议（⚠ 待业务拍板，决策纪要 B-4）

| 告警 | 触发建议 | 说明 |
| --- | --- | --- |
| 降级率告警 | 24h 降级率 > 20%（n≥100） | 5-6 落地后才可启用；样本不足时只看“连续降级次数” |
| 失败运行告警 | 24h 失败运行率 > 10%（n≥30）或同一场景连续失败 ≥3 次 | 与 6-2 告警项对齐 |
| 成本告警 | 单次运行平均 token 日环比 +50% | 只看 token；金额阈值待单价表 |
| 采纳率提示 | 采纳率 < 40%（n≥100）连续 2 个窗口 | 提示运营复核建议质量，不自动报警 |
| 配额提示 | token 日限额 429 触发 | 展示已用/限额/重置时间（5-5） |

> 以上均为**建议值**，未拍板前只作默认配置写入驾驶舱，不作为对外 SLA。

---

## 6. 每日对账 / 发版前自检清单（可执行）

- [ ] M1+M2+M3 之和 = 100%（同一 cohort、同一窗口；不等于即口径 bug）
- [ ] M1/M2/M3 的 `n` 与「窗口内已终结计划数」一致（SQL 二次核对）
- [ ] M8 的 chat 部分与 `GET /agents/usage/summary?days=1` 的 `tokens_in+tokens_out` 对得上（同一窗口；差值为 0 或可解释）
- [ ] M8 的 restock/fault 部分：`agent_run` 成功运行数 = 有 `agent_step` 行的 run 数（缺 step 的 run 要能解释，如 0 步早退）
- [ ] M9 分母不含 `running`/`waiting_confirm`；`interrupted` 已单列
- [ ] M5/M6/M7 若显示数字，必须确认 5-6 / 5-1 已上线且脚注有版本；否则必须是「—」
- [ ] 所有卡片都有 `n` 标注与口径版本号
- [ ] 抽样 5 条采纳/跳过计划，人工对照 `agent_decision_log` 与页面数字一致

---

## 7. 变更记录与回写

| 日期 | 变更 | 影响面 |
| --- | --- | --- |
| 2026-11-04 | V1 初版：定义 M1~M11；标注 M5/M6/M7 数据源未就绪；记录 `calibration_notes` 未落库与 `adjust_reason` 被覆盖两个口径坑 | S7（6-1）、5-1、5-2、6-8 |

**回写义务**：
1. 任何任务改变上述字段/动作名（如 5-6 加列、2-4 加日志状态）→ 先改本文件，再改代码；
2. 驾驶舱 S7 上线（6-1）后，本文件版本号必须出现在页面脚注；
3. 业务效果指标（M10/M11）口径变更 → 先改 `docs/DKD业务规范-清晰版.md` §4.5，再回写本文件（业务规范是业务口径唯一来源）。

---

*版本：V1（2026-11-04）。本文件是 S7 与评测的唯一口径；与代码不一致时以“能对账”的 SQL 为准，并回写本文件。*
