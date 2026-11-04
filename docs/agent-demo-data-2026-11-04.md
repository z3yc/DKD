# Agent 效果演示数据（2026-11-04，本机开发库）

> 用途：给验收人直观看到「知识库检索/引用、补货分析、人工调整/跳过/确认建单、驾驶舱、告警」的真实链路数据。
> **注意：本页数据为本机演示数据，不是生产业务结论**；其中确认建单产生了 1 张演示工单（taskId=580 / taskCode=202611040001），
> 已在下方给出复核/清理建议。

## 1. 知识库（S11）演示

- 文档：`demo_fault_e777`《演示：故障码 E-777 处置手册》（category=fault_code，1 个切片）
- 检索测试：query `E-777 压缩机过载 怎么处理` → 命中 1 条，score **0.395**，retrievalId=6
- 真实 LLM（scene=3）回答摘要：
  > 知识库里有关于 E-777 的收录，依据如下……**来源：厂商故障码手册演示语料 `[KB:demo_fault_e777]`**……
  > 处置步骤：断电 3 分钟复电、检查冷凝器风扇、测启动电容（>10% 更换）、仍过载查制冷剂泄漏并联系售后。
  > 并说明该片段相似度较低（0.16）、文档为演示语料，不具备厂商正式手册效力。
- 会话：`conv-6607aa12cf354b42`（scene=3，2 条消息，configVersion=1）
- KB stats：documents=1 / chunks=1 / retrievals=3 / fallbackRate=0.333（低样本，仅演示）

## 2. 补货分析（S2/S3）演示

触发 `POST /agents/restock/analyze`（服务间密钥）→ `status=analyzed`，deviceCount=3，channelCount=3，totalQuantity=20。

| planId | 设备 | 状态 | 数量 | llmCalibrated | 演示动作 |
| --- | --- | --- | --- | --- | --- |
| 146 | A1000001 | 2 已调整 | 6 | 0（未校准） | 人工调整：现场不缺货，按 6 件调整 |
| 147 | aim5xu4I | 4 已建单 | 8 | 0（未校准） | 确认建单 → **taskId=580 / taskCode=202611040001** |
| 148 | tCeiyxLx | 3 已跳过 | 5 | 0（未校准） | 跳过：设备离线 |

- 三台计划的理由均为「降级·规则兜底（近 30 天无订单，无法估计需求）」，前端应显示「未校准」；
- 真实 LLM（scene=2）回答摘要：今天 3 台设备需补货共 20 件；列出 A1000001/aim5xu4I/tCeiyxLx 建议量与接单人，
  并提醒 aim5xu4I 无销量依据、需确认设备在线；明确建单需人工确认。会话 `conv-960757824b974cbf`。
- 工作台页：`AI 智能体 → 智能补货工作台`，日期切到 **2026-11-04** 可看到「已调整/已建单/已跳过」与「未校准」徽标。

## 3. 驾驶舱（S7）演示（近 1 天）

```json
{
  "adoption": {"totalPlans": 2, "orderedPlans": 1, "adjustedPlans": 0, "adoptionRate": 0.5, "adjustRate": 0.0},
  "degradation": {"totalPlans": 3, "unknownPlans": 0, "uncalibratedPlans": 3, "uncalibratedRate": 1.0},
  "cost": {"calls": 4, "tokensIn": 9069, "tokensOut": 799, "tokensTotal": 9868, "avgTokensPerCall": 2467.0},
  "failure": {"runs": 0, "failedRuns": 0, "failureRate": null, "errorLogs": 0},
  "pending": {"count": 1, "overThreshold": 0, "thresholdHours": 24, "oldestWaitMinutes": 0},
  "compare": {"available": false, "n": 0, "note": "样本不足（n=0），不画结论"},
  "alerts": [
    {"level": "warning", "code": "llm_uncalibrated", "msg": "LLM 未校准率 100.0%（阈值 50.0%，未校准 3 / 已知 3 行）"}
  ]
}
```

- 成本报告（近 1 天）：calls=4 / tokens=9868；`.run/cost-demo.json`
- 告警检查（dry-run）：2 条 warning —— 未校准率 100%、KB 回退率 33%（`.run/alerts-demo.json`，KB 回退率因样本仅 3 次，不代表目标不达标）
- 页面：`AI 智能体 → 运营驾驶舱`（菜单 9035），时间窗可选 7/14/30 天。

## 4. MCP（S12）演示

- 新增 server `demo_mock`《演示：外部只读手册（mock）》，transport=http，endpoint=`http://127.0.0.1:19090/mcp`，
  **enabled=0（默认停用，避免 mock 不在时 Agent 启动失败）**；
- 探测：ok=true，延迟约 1.2s，只读工具 1 个（`read_machine_manual`），拒绝 2 个（写工具/无 readOnlyHint）；
- 同步：`agent_tool` 中出现 `mcp.demo_mock.read_machine_manual`（source=mcp，只读）；
- mock server 当前在本机 19090 运行；停掉 mock 不影响 Agent（server 停用，不参与启动探测）。
- 页面：`AI 智能体 → MCP 接入`。

## 5. 会话持久化（S13）演示

- `GET /manage/agent/conversation/list` 当前 total=2：
  - `conv-6607aa12cf354b42` scene=3，messageCount=2，configVersion=1（知识库问答）
  - `conv-960757824b974cbf` scene=2，messageCount=2，configVersion=1（补货问答）
- 页面：`AI 智能体 → 历史会话`，可看消息时间线、工具调用明细、requestId 跳日志页。
- 助手抽屉按 user+scene 保存会话：刷新后重新打开可恢复。

## 6. 复核/清理建议

- **演示工单**：`taskCode=202611040001`（补货工单）由 plan 147 确认生成；如需清理请在工单管理按业务流程取消/关闭，
  **不要直接删业务表**（AGENTS §7.4）。
- 演示计划：`UPDATE agent_restock_plan SET del_flag='1' WHERE id IN (146,147,148);`（软删，若不需要保留演示数据）。
- 演示知识库文档：`PUT /manage/agent/kb/documents/demo_fault_e777/enabled {"enabled":0}` 可停用（不删数据）。
- MCP `demo_mock` 保持停用即可；若不再需要可停用/删除（无 DELETE API，走 SQL 清理时先确认无引用）。
- mock MCP server：本机后台进程，可直接停掉；Agent 不会因此启动失败（demo_mock 停用）。

> 以上数字均来自本机真实接口/数据库（2026-11-04 13:44 前后），未手工编造；但样本量很小，只用于看链路与页面效果。
