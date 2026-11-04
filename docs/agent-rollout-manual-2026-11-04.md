# Agent 灰度执行手册（排期 6-3 / 6-4）

> 状态：**手册已交付**；真实放量与观察需项目负责人批准并至少观察 3 个工作日/级（6-4 🧑）。
> 原则：**每级可独立回退**、**先影子后放量**、**没有评测/告警证据不升级**。主业务永远有原规则链路兜底。

## 0. 进入灰度前必须满足（G0）

- [ ] M2 配置台、3-x 工具/知识库/MCP、4-x 会话治理、5-1 评测护栏、6-1 驾驶舱、6-2 告警检查均已上线；
- [ ] `pytest` / `ruff` / `mvn` / 前端构建全绿（证据见 CI 与提交记录）；
- [ ] 生产备份：MySQL `agent_*` + 业务库、PostgreSQL checkpoint 库；`.env` 已备份到受控位置；
- [ ] 回退人/值班表已确定；回退命令在本手册 §7，已在预发演练一次；
- [ ] 告警 webhook 已接真实值班群，并完成一次注入演练（6-2 第 3 节）；
- [ ] 评测：有 verified 评测集并产出最近一次报告；无 verified 集时**不得**放开 prompt 编辑（Step 3 阻塞）。

## 1. 五级灰度顺序

| 级 | 放开什么 | 不改什么（保护区） | 通过条件（每级观察 ≥3 工作日） | 回退 |
| --- | --- | --- | --- | --- |
| S0 | 部署代码，**总开关关闭** | 全部 | 主业务零影响；页面可打开、接口 401/403 正确；`agent.enabled=false` 下无 Agent 调用 | 回退版本 |
| S1 | 开配置但只改**非保护区**参数（temperature/maxTokens/timeout/重试） | prompt/tools/scene/runtime/载体 | 驾驶舱无 critical；成本/失败率不高于基线；配置保存/回滚成功 | 版本回滚（配置台） |
| S2 | 放开 **prompt 编辑**（评测护栏开启） | tools/scene/自定义智能体 | 评测报告 scored 且 ≥基线；保存被拦时走 force 留痕；回退旧版本后旧会话不被改写 | 版本回滚 + `evalGateEnabled=false` |
| S3 | **自定义智能体试点 1 个场景**（scene ≥101） | 其他场景/工具 | 该场景 3 天成功率、引用回退率、人工干预率在阈值内；无越权工具 | 停用该配置（enabled=0） |
| S4 | **知识库 + MCP 试点**（各 1 个） | 其他 server/文档 | MCP 仅只读工具且探测成功；KB 回退率 <15%；无写工具进入模型 | 停用 server/停用文档 + `DKD_AGENT_MCP_ENABLED=0` 重启（紧急） |

> 每级只在**上一级观察期满且指标达标**后进入；不满足就停在当前级，不允许“先放开再补证据”。

## 2. 每级操作命令

### S0 总开关关闭验证

```powershell
# Java 侧（RuoYi 配置或环境变量，按部署方式二选一）
$env:AGENT_ENABLED='false'
# Python 侧回到代码默认（不读 DB 配置）
$env:DKD_AGENT_CONFIG_FROM_DB='false'
# 重启 Python 后验证
Invoke-RestMethod http://127.0.0.1:8090/health
# 预期：Java 网关 /agent/status enabled=false；对话入口提示不可用/降级；主业务下单补货正常
```

### S1 开配置、只改非保护区参数

1. 配置台编辑目标智能体：只改 temperature/maxTokens/timeoutS/maxRetries；
2. 保存后 `GET /manage/agent/config/{id}/history` 能看到新版本；
3. 观察 3 天：驾驶舱成本/失败率、日志页 ERROR、会话详情。

### S2 放开 prompt 编辑（护栏开启）

```powershell
# 预发先开护栏冒烟：无 verified 报告时保存应 409
$env:AGENT_EVAL_GATE_ENABLED='true'   # 重启 Java
# 保存被拦：先跑评测并上报
$env:DKD_EVAL_JWT='<admin token>'
python docs/scripts/run-agent-eval.py --mode live --config-code chat `
  --post-report http://127.0.0.1:8080/manage/agent/eval/report
# 若确认风险：配置台 force=true+原因≥10 字（change_note 留痕）
```

### S3 自定义智能体试点 1 个场景

- 新建 scene ≥101 配置，tools 仅选只读工具；enabled=1；
- 观察：驾驶舱失败率、该场景会话详情工具明细、引用回退率；
- 回退：`PUT /manage/agent/config/{id}/enabled {"enabled":0}`（原因必填）。

### S4 知识库/MCP 试点

- KB：入库 1~2 份已授权语料 → 检索测试 → 回退率观察；停用文档立即生效；
- MCP：新增 server（默认停用）→ 安全复核（只读/endpoint/env 名）→ 启用 → 探测/同步 → 观察；
  紧急回退：停用 server + `DKD_AGENT_MCP_ENABLED=0` 重启 Python。

## 3. 观察期与指标（G1~G4）

每级观察 ≥3 个工作日，每日 09:30 看驾驶舱并记录到 `docs/agent-rollout-record-template.md`：

| 指标 | 口径 | 阈值（建议） |
| --- | --- | --- |
| 采纳率 | 已建单计划 / 计划总数 | 不低于试点前基线（无基线先记录不设阈值） |
| 未校准率 | `llm_calibrated=0` / 已知标记计划 | <50%，且 LLM 探测无连续失败 |
| 失败率 | failed run / 总 run | <20%，无连续 3 失败 |
| 成本 | 单次平均 token（agent_message） | 不高于试点前基线；无基线只记录 |
| KB 回退率 | fallback / retrievals | <15% |
| 待指派超时 | >24h 待指派数 | 0（或已有人工跟进记录） |
| 人工干预率 | adjust/skip 计划 / 计划总数 | 记录趋势，不设硬阈值 |

**回退演练**：至少做一次真实回退（任一级），记录耗时与影响；只在文档里写“可回退”不算通过。

## 4. 每级回退命令

| 场景 | 命令/操作 |
| --- | --- |
| 配置改坏 | 配置台「版本历史 → 回滚」到上一版；或 `POST /manage/agent/config/{id}/rollback` |
| 新智能体异常 | `PUT /manage/agent/config/{id}/enabled` → 0（原因必填） |
| 工具越权/误用 | 工具中心停用对应工具（≤30s 生效） |
| MCP 异常 | MCP 页停用 server；紧急 `DKD_AGENT_MCP_ENABLED=0` 重启 |
| 知识库错误 | KB 页停用文档（立即不参与检索） |
| 整体止损 | Java `agent.enabled=false`（主业务走原规则链路）；Python 可停进程 |

## 5. 放量签字

| 级别 | 日期 | 批准人 | 观察结论 | 是否进入下一级 |
| --- | --- | --- | --- | --- |
| S0 |  |  |  |  |
| S1 |  |  |  |  |
| S2 |  |  |  |  |
| S3 |  |  |  |  |
| S4 |  |  |  |  |

> 签字与观察记录是 6-4 的验收证据；本模板不含预填“通过”。
