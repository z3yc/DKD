# Agent 运维手册与培训大纲（排期 6-5）

> 状态：手册 V1 已交付；**培训/考核需真人完成（🧑）**，签到与考核题模板见 §6。
> 适用对象：值班运维、运营管理员、后端值班。配套：告警 Runbook、灰度手册、安全复核报告。

## 1. 服务拓扑与端口（本机/单机部署）

| 组件 | 端口 | 启停 | 健康检查 |
| --- | --- | --- | --- |
| Java 管理后台 | 8080 | `powershell -File .run/launch/backend.ps1`（或服务） | `POST /login` 200；`/agent/status` |
| Python Agent | 8090 | `cd dkd-agent; .venv\Scripts\python.exe -m app` | `GET /health`（含 checkpoint/tool_registry/log 状态） |
| 前端 Vite（开发） | 80 | `cd dkd-vue; npm run dev` | 打开登录页 |
| MySQL | 3306 | 系统服务 | 业务库/`agent_*` 可连 |
| PostgreSQL（checkpoint） | 5432 | 系统服务 | `/health.checkpoint_open=true` |
| Redis | 6379 | 系统服务 | 主业务依赖 |

## 2. 启动/停止检查清单

**启动 Python 前**

1. `.env` 存在且与部署环境一致（密钥只通过环境变量，**不要把值写进命令历史**）；
2. MySQL/Postgres/Redis 已就绪；
3. `GET /health`：`status=ok`、`checkpoint_open=true`、`tool_registry_source=db`（或 default 且 degraded 符合预期）；
4. 若启用了 MCP server：`/health` 正常且日志无「MCP server 启动探测失败」（失败会**拒绝启动**，先停用 server）。

**停止/重启**

```powershell
# 停止：优先停上游 Java 网关开关，再停 Python；避免“前端还能点、后端已死”
# 重启 Agent 后必须做：
Invoke-RestMethod http://127.0.0.1:8090/health | ConvertTo-Json -Depth 3
# 看日志：.run/logs/agent*.log（或控制台 JSON 日志），重点看 startup/失败原因
```

## 3. 常用配置台操作

| 目标 | 页面 | 注意事项 |
| --- | --- | --- |
| 改温度/上限/重试 | 智能体配置 → 编辑 | 非保护区参数，保存即生效（下一轮） |
| 改 prompt/tools | 智能体配置 → 编辑 | 5-1 护栏开启时需评测报告；强制保存需原因≥10 字并留痕 |
| 回滚 | 版本历史 → 回滚 | 回滚不回写旧会话；旧会话 configVersion 保留 |
| 停用智能体 | 配置行 enabled=0 | 原因必填；主业务走原链路 |
| 工具启停 | 工具中心 | ≤30s 生效；停用后提示词工具名单同步 |
| 模型档案 | 模型档案 → 测试连通 | 密钥只存环境变量名；连通记录可查 |
| 知识库 | 知识库 | 入库/检索测试/停用；停用立即不参与检索 |
| MCP | MCP 接入 | 默认停用；只读工具；探测失败会阻止启动（先停用） |
| 日志/会话/驾驶舱 | 执行日志/历史会话/驾驶舱 | 用 requestId 串联；驾驶舱 n=0 不代表指标好 |

## 4. 常见故障处置

| 现象 | 先看 | 处置 |
| --- | --- | --- |
| 对话 502/超时 | 执行日志 ERROR + `agentRun` 失败原因 | 区分 LLM 上游/DB/工具；必要时 `agent.enabled=false` 止损；保留 requestId |
| 429 限额 | 对话提示含已用/限额/重置时间 | 等次日 00:00 重置或调整限额（需评估成本） |
| 配置保存 409 | 评测护栏 | 先跑评测并上报；确需强制走配置台原因留痕 |
| 工具「调用了但没结果」 | 工具中心启停 + 日志 | 先确认工具 enabled/read_only；知识库空时回答应回退“未覆盖” |
| MCP 启动失败 | Python 启动日志「MCP server 探测失败」 | 停用该 server 或修复网络/密钥 env；不要绕过启动校验 |
| KB 回退率高 | KB 页回退率 + 检索测试 | 补语料/调阈值（5-3），不要直接改回答 |
| 会话无法恢复 | `/health.checkpoint_open` | 检查 PG 服务/连接串；重启会做 running→interrupted 收尾 |
| 日志表增长 | `agent_log` 保留期 | 用 `docs/scripts/cleanup-agent-data.py` 先 dry-run，再 `--execute --confirm` |

## 5. 安全与数据纪律（必须记住）

- 密钥只在 `.env`/环境变量；**禁止**在页面、SQL、日志、工单里粘贴密钥值；
- Python 账号无 DELETE：清理走运维脚本/Java；
- 所有写操作仍走 Java 人工确认链路（本期无自动建单）；
- 生产操作先备份，再变更；每步记录到灰度记录模板。

## 6. 培训大纲（1 场，约 2 小时）+ 考核

| 环节 | 内容 | 时长 |
| --- | --- | --- |
| 1 | 架构与兜底：Java 业务事实、Python 旁路、开关/回退 | 20 min |
| 2 | 配置台：编辑/评测护栏/回滚/停用；现场演示 409 强制流程 | 30 min |
| 3 | 工具/模型/知识库/MCP：只读边界、探测失败处置 | 30 min |
| 4 | 日志/会话/驾驶舱/告警 Runbook 演练 | 30 min |
| 5 | 答疑与考核 | 10 min |

**考核题（≥80 分通过）**

1. 配置保存返回 409 evalGate，正确处置顺序是什么？强制保存必须满足什么条件？
2. MCP server 探测失败导致 Python 起不来，第一动作是什么？为什么不能跳过启动校验？
3. 对话 429 时，向用户解释哪三件事？重置时间是什么？
4. 如何在不改代码的情况下整体停用智能体？主业务是否受影响？
5. 知识库回退率 30% 时，先做什么、不能做什么？
6. 生产清理 `agent_log` 前必须做哪两件事（dry-run/备份）？

**签到模板**

| 日期 | 姓名 | 角色 | 签到 | 考核分数 | 备注 |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |

> 培训签到、考核分数与录屏/照片是 6-5 的验收证据；本文件只是模板（🧑）。
