# 智能体取数评测集 · 格式与采集标注流程（阶段 1 / 任务 1-4）

> **状态：🟡 格式与流程已定义；真实样本未采集（50~100 条待外部输入）**
> 现有 `docs/agent-eval-set/starter-cases.json` 只有 **8 条示例（非基线，需替换）**，用于验证格式与流水线；
> **不得把它当作基线、不得用它计算准确率对外汇报**。
> **日期：2026-11-04** · 归属：计划表 1-4（P0）→ M1（W1 周三~周五）；消费方：5-1 评测流水线、5-2 取数调优
> 依据：旧排期 3-2「50~100 条真实取数问题 + 期望结果，作为参数抽取与 NL2SQL 回归基准」；
> `docs/agent-metrics-dictionary.md` §2.3（取数准确率口径）；`docs/DKD业务规范-清晰版.md` §4.5（业务口径）。
> **不是**哪个评测集：与 `docs/ai-code-eval-set-design.md` 的「代码生成质量评测集」是两个不同评测集，**不可合并**（该文档自己已写明此提醒）。

---

## 0. 这份评测集要回答什么

| 要回答 | 不要回答 |
| --- | --- |
| 助手/运营分析**取数是否正确**：选对工具与表、抽对时间窗与维度、算出的数字与人工口径一致 | 代码写得好不好（那是代码生成评测集） |
| 换 prompt / 换模型 / 换配置后有没有**退化**（5-1 保存前护栏） | “感觉变聪明了”（无样本无口径） |
| 哪一类问题最容易错（时间窗 / 区域维度 / 多指标组合 / 拒答边界） | 业务效果（缺货率降多少 → 6-8 效果评估，另一套口径） |

**闭环位置**：1-4 产出评测集 → 5-1 接入 CI（版本保存前自动跑）→ 5-2 按错误类型调优 → 目标 **≥90%**（M7）。

---

## 1. 评测集文件格式

**一个版本 = 一个 JSON 文件**（建议命名 `cases-v1.json`，与 `starter-cases.json` 分开放），结构：

```json
{
  "meta": {
    "name": "dkd-agent-data-retrieval-eval",
    "version": "v1",
    "schemaVersion": 1,
    "createdAt": "YYYY-MM-DD",
    "status": "verified | draft | example-only",
    "note": "本版说明（数据来源、标注批次、已知缺口）",
    "count": 0,
    "baseline": { "accuracy": null, "n": null, "runAt": null, "configVersion": null, "model": null }
  },
  "cases": []
}
```

### 1.1 单条 case 字段

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `id` | ✅ | string | `EV-001` 递增；**发布后不复用、不改号** |
| `question` | ✅ | string | 用户原话（脱敏后），保留口语/省略，不要改写成标准问句（否则测不出理解能力） |
| `scene` | ✅ | int | 场景码：1 通用问答 / 2 补货 / 3 诊断 / 4 运营分析；自定义 ≥101 |
| `expected_metric` | ✅ | string | 期望指标口径：`revenue` / `order_count` / `sku_sales_rank` / `stockout_channels` / `restock_task_duration` / `diagnosis_evidence` / `refuse` 等（与业务规范 §4.5 指标名对齐） |
| `expected_tables` | ✅ | string[] | 期望访问的业务表（`tb_*`），用于判“工具/表选对没” |
| `expected_params` | ✅ | object | 期望参数：`window_days` 或 `start`/`end`（半开区间）、`region`、`category`、`inner_code`、`top_n`、`granularity` |
| `expected_answer_type` | ✅ | enum | `value` / `rank` / `ratio` / `evidence` / `refuse` |
| `expected_value` | 🟡 | number \| null | 期望数值；`null` = 待标注或“排名类只校验集合”（必须在 `notes` 写原因） |
| `tolerance` | 🟡 | number \| null | 数值容差（计数类 0；金额/比例类按业务约定，默认 0.01） |
| `unit` | 🟡 | string | `元` / `单` / `件` / `分钟` / `%` |
| `must_refuse` | ✅ | bool | 是否“必须拒答/必须说明不可查”的负样本 |
| `refuse_reason` | 🟡 | string | `越权` / `数据不存在` / `超出时间范围` / `写操作需人工确认` / `无可靠依据` |
| `tags` | ✅ | string[] | 见 §1.2 |
| `source` | ✅ | string | 来源：`运营提问` / `工单` / `工作台` / `会话抽查` / `业务规范条目` |
| `risk_level` | ✅ | enum | `low` / `medium` / `high`（高风险=涉及区域维度/写操作/金额） |
| `annotator` / `annotate_date` | ✅ | string | 标注人 + 日期 |
| `reviewer` / `review_date` | 🟡 | string | 复核人 + 日期（发布前必须双人） |
| `status` | ✅ | enum | `draft` / `verified` / `retired`；**只有 `verified` 进准确率分母** |
| `notes` | 🟡 | string | 标注依据、口径确认记录、已知争议 |

### 1.2 标签（`tags`）约定

| 标签 | 含义 | 为什么单独统计 |
| --- | --- | --- |
| `time-window` | 涉及相对时间（今天/近 7 天/上月） | FIX-28 类问题（“近五年”被当成 90 天）最集中 |
| `dimension-region` | 区域/点位维度 | 业务规范 §8-4 区域权限未做，易越权 |
| `dimension-category` | SKU/品类维度 | — |
| `multi-turn` | 需要上下文（“那上个月呢？”） | FIX-29 会话级 vs 本轮级陷阱 |
| `negative` | 必须拒答/必须说明不可查 | 幻觉高发区，单列“幻觉率” |
| `no-cause` | 必须不产病因（诊断证据类） | FIX-22：无知识库不得编病因 |
| `ranking` | Top N 类（只校验集合不校验顺序时须注明） | 排序口径易漂 |
| `aggregation` | 跨设备/跨区聚合 | 单主库护栏（时间窗+分批）相关 |
| `dimension-vm` | 设备维度（按 inner_code/设备编号） | 设备编号口径与 join 陷阱（FIX-25 collation） |
| `stockout` | 缺货/低库存类 | 补货触发条件，口径在业务规范 §4.4 |

> 以上为**必选标签集**；允许按指标补充业务标签（如 `sku`、`replenish`），但上述标签的统计口径不得变（5-2 的归因依赖它们）。

### 1.3 `starter-cases.json` 的定位（必须写清楚）

- 位置：`docs/agent-eval-set/starter-cases.json`；
- 条数：**8 条**（≤10），`meta.status = "example-only"`、`meta.note` 明写「示例（非基线，需替换）」；
- 用途：**只验证格式、标注字段与流水线可解析**；`expected_value` 多为 `null`（待人工 SQL 核对）；
- **禁止**：把它当基线准确率、用它的结果对外汇报、在 5-1 里把它当“必须通过”的门禁样本。

---

## 2. 采集与标注流程（50~100 条，可执行）

> 目标：**50~100 条 `verified`**（下限 50 条才能满足“n≥30 可内部看、n<100 不作对外”的底线；目标 100 条进入可对外区间）。
> 责任人：产品/运营（采集 + 业务口径）+ Python（标注工具与校验）+ 测试（复核）。

### 步骤 1：采集来源（1~2 天）

| 来源 | 取样方式 | 注意 |
| --- | --- | --- |
| 运营群/日常提问 | 近 4 周真实提问，导出脱敏 | 保留原话，不改写成标准句 |
| 工作台/会话抽查 | `agent_conversation`/`agent_message`（只读）抽样 | 手机号/订单号按 AGENTS §7.8 脱敏 |
| 工单与故障记录 | 维修/补货工单描述 | 只取“取数类”问题，不取流程类 |
| 业务规范 §4.5 指标条目 | 每个指标至少 1 条 | 保证覆盖报表口径 |

### 步骤 2：初筛（0.5 天）

- 保留：能用只读工具 + 业务口径给出**确定答案**的问题；
- 剔除：纯主观、纯寒暄、无数据源、重复（相似度去重）、需要写操作（除非作为 `negative` 负样本保留 3~5 条）；
- 预计留存率 60%~70%：采 150 条 → 约 100 条。

### 步骤 3：标注（2~3 天，核心工序）

| 动作 | 要求 |
| --- | --- |
| 填 `expected_metric` / `expected_tables` | 必须与业务规范/表白名单一致（`tb_*` 前缀） |
| 填 `expected_params` | 时间窗写**半开区间**语义（如“近 7 天”= 不含今天，按业务规范口径；有歧义的在 `notes` 写清） |
| 填 `expected_value` | **必须由人跑只读 SQL 核对后填写**，并留下核对人和日期；**禁止用模型输出当期望值**（循环论证） |
| 负样本 | 写明 `refuse_reason`；正确拒答 = 计 1 分，编造 = 0 分并计入幻觉 |
| 诊断类 | `expected_answer_type=evidence`，只要求证据/未知项/平台动作，**不得出现病因字段**（FIX-22） |

### 步骤 4：双人复核（1 天）

- 复核人逐条看 `expected_*` 与口径；不一致 → 打回标注，**不发布**；
- 复核通过 → `status=verified`、填 `reviewer/review_date`；
- 冲突记录在 `notes`（保留争议，不要静默改口径）。

### 步骤 5：试跑与基线（0.5 天，与 5-1 联动）

- 用 5-1 流水线跑一遍 → 报告字段见指标字典 §2.3；
- **基线**：首次 `verified` 版本跑出的准确率写入 `meta.baseline`（含 `configVersion`/`model`/`n`/`runAt`）；
- 基线未记录前，5-1 不允许“低于基线阻断保存”（否则等于拦死所有改动）。

### 步骤 6：版本冻结与变更（长期）

- 发布后**只追加、不改已发布 case**（防止“改题刷分”）；需要修正 → 标 `retired` + 新增一条，并在 `meta.note` 记变更；
- 每次新增/退役后 `meta.count` 与 `meta.version` 递增；准确率报告必须带版本号。

### 2.1 配额分配（100 条版建议）

| 维度 | 条数 | 说明 |
| --- | --- | --- |
| 场景 scene=1 通用问答 | 55 | 主力入口 |
| 场景 scene=4 运营分析 | 25 | 取数密集 |
| 场景 scene=2 补货 / scene=3 诊断 | 各 5 | 防回归 |
| 自定义场景 ≥101 | 5 | 3-2 上线后补 |
| 其中 `negative` 负样本 | 15 | 越权/无数据/超范围/写操作/无依据 |
| 其中 `multi-turn` | 10 | 上下文追问 |
| 其中 `time-window` | 30 | 相对时间最易错 |

> 分布理由：FIX-27/28/29 三类真实缺陷都在“时间窗/参数跟随/跨轮”上，评测集必须重点压这三类。

---

## 3. 与 5-1 评测流水线的接口

| 项 | 约定 |
| --- | --- |
| 输入 | 评测集 JSON（`cases` 中仅 `status=verified` 生效） |
| 输出（报告） | `evalSetVersion` / `configVersion` / `model` / `n` / `accuracy` / `tool_accuracy` / `param_accuracy` / `value_accuracy` / `hallucination_count` / `blocked_count` / `runAt` |
| 判定 | 严格准确率（工具+参数+数值全对）；分项只用于归因（指标字典 M7） |
| BLOCKED | 环境类拦截（服务未起/配额 429/依赖缺失）单列，**不算失败也不算通过**，退出码约定 0/1/2（AGENTS §10） |
| 门禁 | 低于基线阻断保存（可强制但留痕进 `agent_config_history`）——基线就绪后启用 |
| 存档 | 报告随版本历史保存（5-1 交付物） |

---

## 4. 1-4 验收清单（做到这些才算完成）

- [ ] 评测集 JSON 通过 schema 校验（字段齐全、`id` 唯一、`status=verified` 数 ≥50）
- [ ] 场景/标签分布达到 §2.1 的配额（允许 ±20%，但 `negative` 与 `time-window` 不得少配）
- [ ] 每条 `verified` 都有 `annotator` + `reviewer` + 日期；`expected_value` 为 `null` 的都有 `notes` 说明
- [ ] 期望值均为人工 SQL 核对（可抽查 10 条复现）
- [ ] 首次基线记录进 `meta.baseline`，并注明 `configVersion`/`model`/`n`
- [ ] 5-1 能复跑同一版本两次，`accuracy` 一致（同模型同配置下）
- [ ] `starter-cases.json` 仍标 `example-only`，**未被当作基线**
- [ ] 报告与页面标注 n 与口径版本（n<100 标“不作对外口径”）

**当前状态（2026-11-04）**：仅完成 §1 格式 + §2 流程 + starter 8 条示例；
**未完成**：50~100 条真实样本采集与标注（外部输入 E-1）、基线记录、5-1 流水线（5-1 属阶段 5）。
在完成前，1-4 只能标 🟡，**不得写“评测集已建立/基线已记录”**。

---

## 5. 风险与规避

| 风险 | 规避 |
| --- | --- |
| 标注人凭印象填期望值 | 强制人工 SQL 核对 + 双人复核 + 抽查复现 |
| 评测集泄露到 prompt（过拟合） | 评测集与 prompt 分仓管理；prompt 里禁止出现 case 原文；5-1 只读 |
| 题目被“改简单”刷准确率 | 发布后只追加不改；退役要留痕并重新计 n |
| 开发库数据太少导致期望值不可靠 | 期望值来源标注（开发库/生产脱敏），开发库样本标“示例数据”；真实基线待 E-5 |
| 把 BLOCKED 当 FAIL 导致“调高配额变绿” | 报告单列 BLOCKED + 独立退出码（AGENTS §10 纪律） |

---

## 6. 附件

- `docs/agent-eval-set/starter-cases.json`：8 条示例（`example-only`，非基线，需替换）。
- 指标口径：`docs/agent-metrics-dictionary.md` §2.3。
- 业务口径：`docs/DKD业务规范-清晰版.md` §4.5。

---

*版本：V1（2026-11-04）· 状态：格式/流程已冻结，样本待采集。*
