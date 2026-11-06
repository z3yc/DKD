# 智能体监控与告警 Runbook（排期 6-2）

> 状态：**检查脚本已实现并实测**；真实值班通道（钉钉/飞书/企微 webhook）需运维配置后演练触达（🧑）。
> 检查脚本：`docs/scripts/agent-alert-check.py`；只读 `agent_log/agent_run/agent_llm_probe/agent_mcp_probe/agent_restock_plan/agent_kb_retrieval`。

## 1. 检查项、阈值与分级

| code | 级别 | 条件（默认阈值） | 含义 / 第一动作 |
| --- | --- | --- | --- |
| `ERROR_LOG_BURST` | critical | 近 15 分钟 `ERROR`/`status=error` 日志 ≥5 | 先看 `agentLog` 页同一 requestId；区分上游 LLM/DB/工具异常 |
| `CONSECUTIVE_FAILURES` | critical | 最近连续失败 run ≥3 | 看 `agentRun` 页失败原因；确认是否同一场景/同一设备 |
| `RESTOCK_0600_FAILED` | critical | 当日 05~07 点 restock run failed ≥1 | 检查 06:00 定时任务、Java 回调、LLM 配额 |
| `LLM_PROBE_FAILED` | warning | 默认模型档案最近一次探测 `ok=0` | 模型档案页「测试连通」；检查密钥 env/余额/网络 |
| `MCP_PROBE_FAILED` | warning | 启用中的 MCP server 最近探测失败 | MCP 页看 failure 原因；必要时先停用（**停用比带病调用安全**） |
| `UNCALIBRATED_RATE_HIGH` | warning | 当日计划未校准率 ≥50% | 看模型是否超时/配额；补货仍可用纯统计基线 |
| `KB_FALLBACK_RATE_HIGH` | warning | 近 24h 知识库回退率 ≥15% | 看 KB 页回退率与检索测试；补语料或调阈值（5-3） |
| `PENDING_OVERDUE` | warning | 待指派计划超 24h 数量 >0 | 补货工作台派单；确认接单人区域匹配是否失败 |

阈值均可用参数覆盖（`--error-threshold` 等）；生产建议在值班系统按环境配置，不要改代码默认值。

## 2. 执行方式与退出码

```powershell
# 本机/巡检机（只读账号即可，脚本默认使用 DKD_CLEANUP_* 管理账号）
$env:DKD_CLEANUP_PASSWORD='***'
python docs/scripts/agent-alert-check.py --json --out .run/alerts.json

# 有告警时触达（先演练：只打印 payload，不发送）
$env:DKD_ALERT_WEBHOOK='https://example.invalid/webhook'
python docs/scripts/agent-alert-check.py --dry-run-notify --out .run/alerts.json
# 确认 payload 后去掉 --dry-run-notify
```

- 退出码：`0` 无告警；`1` 有告警（应触达）；`2` 检查本身失败（DB 不可达/配置错误，**不要**当成“无告警”）。
- 建议调度：每 5 分钟一次（ERROR/连续失败）；每日 06:10、09:00 各一次（补货/未校准/KB）。

## 3. 触达演练步骤（人为注入故障验证）

1. **注入（低风险）**：模拟“MCP 探测失败”——把某台测试 MCP server 的 endpoint 改为不可达地址，执行探测；
   或直接向 `agent_log` 写入一条测试 ERROR（仅测试环境）。
2. 运行 `agent-alert-check.py --dry-run-notify`，确认出现 `MCP_PROBE_FAILED` / `ERROR_LOG_BURST`。
3. 配置 `DKD_ALERT_WEBHOOK` 后重跑（去掉 dry-run），在值班群确认收到消息。
4. 恢复配置/清理测试日志，再跑一次确认 **告警消失**（避免“只会响不会停”）。
5. 把演练时间、告警 code、触达截图/消息 ID 记入 `docs/agent-rollout-record-template.md`。

**没有第 3 步的真实触达，6-2 只能算“检查已实现”，不能算“告警演练通过”（🧑）。**

## 4. 值班响应 SLA（建议）

| 级别 | 确认时限 | 止损时限 | 升级 |
| --- | --- | --- | --- |
| critical | 15 分钟 | 30 分钟（可先 `agent.enabled=false` 停用智能体，主业务不受影响） | 值班负责人 |
| warning | 2 小时 | 当日 | 次日例会 |

## 5. 一键止损（不依赖 Agent 服务）

| 目标 | 操作 |
| --- | --- |
| 关闭智能体入口 | Java 配置 `agent.enabled=false`（或前端开关），主业务走原规则链路 |
| 回退配置化 | `DKD_AGENT_CONFIG_FROM_DB=false` + 重启 Python（回到代码默认） |
| 停用 MCP | MCP 页停用 server（Agent 重启前不生效；紧急时可同时 `DKD_AGENT_MCP_ENABLED=0` 重启） |
| 停用工具 | 工具中心停用对应工具（TTL ≤30s 生效） |
| 清理/保留 | 见 `docs/scripts/cleanup-agent-data.py`（默认 dry-run） |

## 6. 已知限制

- 当前 webhook 通道未接线（无真实值班群地址），6-2 的“触达演练记录”为 🧑；
- `agent_log` 查询按 15 分钟窗口，日志量极大时需确认 `create_time` 索引（已有）；
- 告警检查脚本不替代外部监控（进程存活/端口探活仍由现有监控负责）。
