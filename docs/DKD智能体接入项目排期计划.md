# DKD 智能体接入项目 · 开发排期计划清单（V2 修订版）

> 依据：《DKD智能体接入方案-LangChain-LangGraph.md》**V1.1** + 原型 V2（docs/prototypes/dkd-agent-prototype.html）
> 基准日：2026-09-21（周一）起排
> 团队配置：1 Python 工程师（全职）+ 1 Java 工程师（W1~W3 需 ≥80%，之后 30%）+ 前端（W1~W3 与 W4~W7 需 ≥50%，之后 30%）+ 产品/项目（兼职验收）
> 关键路径：**P0-1 集成底座 → P0-2 补货 Agent → P0-3 诊断 Agent**；并行线：前端改造、安全整改
>
> **V2 相对 V1 的三处硬修正（详见 §六 修订记录）**：
> ① 排期按 2026 年法定假期实际扣减（V1 把中秋/国庆假期区间算错了，Phase 0 声称 10 天实际只有 7 天）；
> ② Phase 0 的 Java 侧工作量（7 人日）在 30% 投入下需要约 23 个工作日，V1 只给了 7 天 → 本版提投入 + 拉长 + 拆两个验收 gate；
> ③ 补入 V1 漏掉的 6 个必要任务（checkpointer 选型落地、state schema 评审、接单人分配与幂等、评测集、成本计量前移、Git 历史密钥清理）。

---

## 一、里程碑总览

| 里程碑 | 交付物 | 截止周 | 验收标志 |
| --- | --- | --- | --- |
| **G1 链路中间验收**（Phase 0 内部门） | Python echo + 前端 SSE 侧边栏打通 | W2 末（09-30） | 前端侧边栏逐 token 渲染 Python 回显内容；Java 网关转发可用 |
| **M1 集成底座通** | Python 服务 + Java 网关 + 回调鉴权 + SSE 全链路 + 安全整改 | W3 末（10-09） | 前端发起对话经网关到 LLM 流式返回；`agent.enabled=false` 一键降级；密钥轮换与历史清理完成 |
| **M2 补货 Agent MVP** | 补货工作台（对齐原型 V2）+ 人工干预闭环 + 接单人分配 | W7 末（11-06） | ≥10 台真实设备跑通"夜间分析→早晨确认→批量建单→工单流转"，与规则版并行对比启动 |
| **M3 诊断 Agent** | 多轮诊断对话 + 维修工单卡片 | W10 末（11-27） | 无培训完成 5 个真实故障的诊断→建单闭环 |
| **M4 Copilot + 收尾** | 运营分析（4 图表+筛选联动）+ 审计页 + 安全复查 | W13 末（12-18） | 高频取数问题 80% 自助回答；写操作 100% 留痕 |
| **上线** | 上线评审 + 灰度 + runbook | W14（12-25） | 上线检查单全过 |
| **M5 效果评估** | 3 个月运营指标评估报告 | 观察期 13 周 | 缺货率降 30%+ / 排单耗时降 60%+ 等方案指标 |

> 总周期 **14 周（2026-09-21 ~ 2026-12-25）**，其中约 1.2 周为法定假期损失。观察期自 2026-12-28 起算 13 周，评估报告落在 2027-03 末。

---

## 二、可用工作日基线（排期计算依据）

2026 年法定假期（国务院办公厅通知）：**中秋 09-25（五）~09-27**；**国庆 10-01（四）~10-07（三）**；调休上班日 **09-20（日）、10-10（六）**。

| 周 | 日期区间 | 可用工日 | 备注 |
| --- | --- | --- | --- |
| W1 | 09-21 ~ 09-25 | **4** | 09-25 中秋 |
| W2 | 09-28 ~ 10-02 | **3** | 10-01、10-02 国庆 |
| W3 | 10-05 ~ 10-09 | **2** | 10-05~10-07 国庆 |
| W4~W7 | 10-12 ~ 11-06 | **20** | 无假期（Phase 1） |
| W8~W10 | 11-09 ~ 11-27 | **15** | 无假期（Phase 2） |
| W11~W13 | 11-30 ~ 12-18 | **15** | 无假期（Phase 3） |
| W14 | 12-21 ~ 12-25 | **5** | 收尾上线 |
| **合计** | 09-21 ~ 12-25 | **64** | 名义 14 周 × 5 = 70 天，扣假期后实际 64 天（少 6 天） |

> 调休上班日（09-20 周日、10-10 周六）**未计入**可用工日，作为额外缓冲；如团队安排上班，可把 W2/W3 的联调工作前移。

---

## 三、详细任务清单（含排期、依赖、负责人）

### Phase 0：基础设施与集成底座（W1~W3，09-21 ~ 10-09，9 个可用工日）

> 本阶段是**关键路径且最拥挤**：V1 把 Phase 0 排在 W1~W2（声称 10 个工作日，扣假期后实际只有 7 个），却堆了 7 人日 Java 工作量——按 30% 投入需约 23 个工作日，超载约 3.3 倍。
> 本版处理：Phase 0 拉长到 W1~W3（可用 9 天）；把 DDL 起草、checkpointer、留痕/计量的主责移到 Python 侧；Java 侧只保留不可替代的工作（0-4 只读账号授权 / 0-6 网关 / 0-7 回调 / 0-8 SSE 联调 / 0-14 安全整改，合计约 **7.0 人日 ≤ 9 天 × 80% = 7.2 人日**），并在 W1~W3 提至 ≥80% 投入。

| # | 任务 | 工作量 | 排期 | 负责 | 交付物 / 完成标准 | 依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| 0-1 | 方案评审会（决策点见方案 §9，本版已把决策点 2 改为陈述句，新增 3 个决策点） | 0.5d | W1 周一 09-21 | 全员 | 评审纪要，全部决策点闭环；未决项按方案默认值先行 | 无 |
| 0-2 | Python 环境：venv + 依赖锁定（**langgraph / langgraph-checkpoint / fastapi / uvicorn / sqlalchemy[asyncio] / aiomysql / aiosqlite**）；锁定**精确版本三元组**并 `--frozen` 安装 | 1d | 09-21 ~ 09-22 | Python | requirements.txt 版本精确到 patch；`uvicorn` 启动 /health | 0-1 |
| 0-3 | LLM 连通：OpenAI 兼容客户端指向 DeepSeek，连通性单测 | 0.5d | 09-22 | Python | 单测：一句话请求返回 200 + 内容 | 0-2 |
| 0-4 | MySQL 只读账号 + **真实表白名单**（`tb_` 前缀，含 `tb_inventory_log`/`tb_task_details`/`tb_emp`/`tb_channel`）+ `tb_order`/`tb_inventory` **数据量与索引勘查** | 1d | 09-21 ~ 09-22 | Java/DBA | ① 只读账号仅白名单表 SELECT，**读非白名单表被拒**（V1 只验了写被拒）；② 越权写被拒；③ 数据量/索引勘查记录入 docs/ddl/ | 0-1 |
| 0-5 | 建 `agent_*` 四表 DDL（conversation/message/decision_log/restock_plan，对齐 RuoYi 审计列） | 1d | 09-23 | **Python 起草 + Java 评审** | DDL + 回滚语句归档 docs/ddl/；测试库执行通过 | 0-1 |
| 0-6 | Java 网关：AgentGatewayController（`/agent/**` 转发 + 用户头注入）+ AgentTokenFilter + AgentProperties + **SSE 不缓冲转发（StreamingResponseBody/ResponseBodyEmitter）** | 2.5d | 09-23 ~ 09-29 | Java | Postman 打通转发；单测覆盖头注入；SSE 逐帧不缓冲 | 0-1 |
| 0-7 | Java 回调：AgentCallbackController（服务间密钥 + 路径白名单 + 速率限制） | 1.5d | 09-29 ~ 09-30 | Java | 无密钥 401；密钥伪造 401；限流生效 | 0-6 |
| 0-8 | **G1 中间验收**：SSE 端到端透传（前端→网关→Python echo） | 1d | 09-30 | Java+前端 | 前端逐 token 渲染；Python 停机时返回降级提示 | 0-6,0-11 |
| 0-9 | Python 服务骨架：FastAPI 入口 / config / security（用户头解析 + 服务间密钥）/ 统一异常 + **request_id 贯穿网关→Python→工具→回调** | 1.5d | 09-23 ~ 09-24 | Python | /health、/agent/chat echo 可调；日志含 request_id | 0-2,0-7 |
| 0-10 | **checkpointer 落地（选型已定，见下）**：SQLite `AsyncSqliteSaver` 接入 + 会话跨请求恢复 + 断线重连续接 + 文件持久化/备份/迁移路径成文 | 1d | 09-28 | Python | 会话跨请求恢复；重启后会话可续；迁移路径写入 runbook | 0-9 |
| 0-11 | 前端 SSE 客户端（**绕开 axios**：fetch + ReadableStream + AbortController + 心跳）+ AI 助手侧边栏壳 | 2.5d | 09-23 ~ 09-28 | 前端 | 与原型交互一致：流式气泡、快捷问题条、**可取消** | 0-6 |
| 0-12 | 结构化日志 + 决策留痕写入 agent_decision_log + **LLM 成本计量与限额（按用户/场景 token 统计 + 上限告警，自 V1 3-9 前移）** | 1.5d | 09-29 ~ 09-30 | Python | 一次对话的留痕可查；计量报表可查；超额熔断 | 0-10 |
| 0-13 | LangGraph 版本锁定复核 + **补货 state schema 设计评审**（LangGraph 持久化语义：新代码立刻作用于历史 checkpoint，schema 不可随意变更） | 0.5d | 10-08 | 全员 | state 字段冻结文档 + 变更兼容规则 | 0-10 |
| 0-14 | 安全整改（剩余部分）：**14a ✅ 已完成**（配置外置已随初始提交重写入库，`main`/`origin` 可达历史无明文）；**14b** 凭据轮换（OSS AK/SK、DeepSeek Key、MySQL/Redis 弱口令）含值班窗口；**14c** 清除 dangling 旧提交对象（`git reflog expire --expire=now --all && git gc --prune=now`） | 1d | 09-28 ~ 10-08 | Java | 旧凭据全部失效（或轮换完毕）；`git fsck` 无含密钥的 dangling 对象；生产环境已注入环境变量 | 0-1 |
| 0-15 | **M1 验收（G2）**：全链路演示（前端→网关→Python→LLM→流式返回）+ 降级演练 + 安全复查 | 1d | 10-09 | 全员 | 验收记录签字；`agent.enabled=false` 降级回原 `/manage/ai/diagnose` 通过 | 全部 |

**checkpointer 选型结论（0-10 的前置调查已完成，不需要再花时间探索）**：
本机 Redis 为 `E:\Redis-x64-3.2.100`（**v3.2.100**，2016-07 Microsoft 移植版），核对结果：

| 检查项 | 实测 | 影响 |
| --- | --- | --- |
| `MODULE` 支持 | 3.2 无模块系统 | ❌ `langgraph-checkpoint-redis` 依赖 RedisJSON/RediSearch（需 Redis 8.0+ 或 Redis Stack），**不可用** |
| Redis Streams | 内核 3.2 < 5.0 | ❌ 方案中"预留 Redis Stream 升级路径"在本实例上不成立 |
| `maxmemory` / `maxmemory-policy` | 未设置（无上限 / 默认 noeviction） | ⚠️ 与 Java 会话共用 db0，存在互相挤占风险 |
| 持久化 | `appendonly no`，仅 `save 900/300/60` | ⚠️ 崩溃最多丢 15 分钟数据，不适合承载会话状态 |

→ **决策：Phase 0~3 使用 SQLite saver（单实例部署形态匹配，零新增基础设施）**；迁移触发条件写入 runbook：**出现多实例部署或需跨机共享会话时**，再升级 Redis（8.0+ / Stack）或换 Postgres saver。
→ Redis 版本升级本身**不阻塞本期**，但作为独立技术债登记（见 §四）。

### Phase 1：智能补货 Agent MVP（W4~W7，10-12 ~ 11-06，20 个可用工日）

> 工作量估算**已按 AGENTS §8 计入测试时间（约 +25%）**，不再另行列"测试"任务；每项的完成标准含拒绝路径用例。

| # | 任务 | 工作量 | 排期 | 负责 | 交付物 / 完成标准 | 依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| 1-1 | 工具层-读：库存 / 货道档案 / 设备 / 点位查询（SQLAlchemy 只读 + 白名单） | 2d | W4 周一~二 | Python | 工具单测（真实库数据抽样核对）；拒绝非白名单表 | M1 |
| 1-2 | 工具层-读：近 30 天订单聚合 + 在途补货工单查询。**必须用范围比较 `create_time >= ? AND < ?`（禁用 `date_format()` 包裹列，`OrderMapper.xml:44` 现有写法会索引失效）+ 按 vm 分批 + 查询超时熔断** | 2.5d | W4 周二~三 | Python | 聚合结果与 Java 侧对账一致；EXPLAIN 走索引并留档 | 1-1 |
| 1-3 | 工具层-写：工单创建回调封装（AgentCallback 调 `POST /manage/task`，参数映射 TaskDto） | 1.5d | W5 周一 | Python+Java | 建单成功；`TaskServiceImpl` 拒绝路径覆盖（设备状态/防重/区域不匹配） | 0-7 |
| 1-4 | 补货子图-A：统计基线引擎（7/14/30 天分位销量 + 货道容量约束 + 预计撑至日期） | 2.5d | W5 周一~二 | Python | 对历史数据回测：建议量与人工经验偏差 ≤±20% | 1-1 |
| 1-5 | 补货子图-B：LLM 校准节点（点位画像/节假日/异常波动因子）+ 理由生成 | 2d | W5 周三~W6 周一 | Python | 每条建议输出结构化理由（对齐原型"依据"字段） | 1-4 |
| 1-6 | 补货子图-C：`interrupt` 人工确认节点 + 计划状态机（建议→已调整→已跳过→已建单，对齐原型状态标签） | 2d | W6 周一~二 | Python | 调整/跳过/恢复走状态机并留痕 agent_restock_plan；**中断-恢复在服务重启后仍可续（依赖 0-10）** | 1-5,0-13 |
| 1-7 | 人工干预 API：调整（数量/原因/预测窗口/服务水平）、跳过（必选原因）、恢复、暂停/恢复计划 | 1.5d | W6 周三 | Python | 原型 V2 全部干预操作后端化；原因必填校验（拒绝路径有测试） | 1-6 |
| 1-8 | **接单人分配策略与幂等防护（V1 缺失）**：按设备 `region_id` 匹配 `tb_emp` 选接单人（`insertTaskDto` 要求 `emp.regionId == vm.regionId`）；同一计划重复确认/多人并发确认的幂等保护 | 1.5d | W6 周三~四 | Python+Java | ① 10 台设备跨区域可正确分配；② 重复确认第二次返回"已建单"而非"设备有未完成工单"；③ 并发确认用例通过 | 1-3 |
| 1-9 | dkd-quartz 定时任务：每日 06:00 触发 `/agents/restock/analyze`（含失败重试 + 告警，AGENTS §6.4 不允许静默失败） | 1d | W6 周五~W7 周一 | Java | 手动触发成功；连续失败告警到运维；任务重入安全 | 1-4 |
| 1-10 | 前端补货工作台（对齐原型 V2）：统计卡 + 三步引导条 + 筛选 chips + 依据折叠展开 | 3d | W6~W7 | 前端 | 与原型交互一致；数据走真实接口；loading 有 finally 复位 | 1-7 |
| 1-11 | 前端调整/跳过弹窗 + 暂停横幅 + toast/状态标签反馈 | 1.5d | W7 周二~三 | 前端 | 每个操作有确认弹窗 + 状态变化，对齐原型 | 1-10 |
| 1-12 | 联调 + 数据核对（Inventory/Order 聚合口径与 Java 侧对账） | 1.5d | W7 周三~四 | Python+Java | 抽查 3 台设备全字段对账一致 | 全部 |
| 1-13 | **M2 验收**：≥10 台真实设备"夜间分析→早晨确认→批量建单→dkd-app 接单流转"全闭环 | 1d | W7 周四~五 | 全员 | 验收记录；遗留问题清单。**前置：dkd-app 测试环境与测试账号就绪（见 §四 风险）** | 全部 |
| 1-14 | 规则版并行对比启动（`generateRestockSuggestions` 双跑 3 周，埋点采集对比数据） | 0.5d | W7 周五 | Python | 对比看板/报表就绪 | 1-13 |

### Phase 2：设备故障诊断 Agent（W8~W10，11-09 ~ 11-27，15 个可用工日）

| # | 任务 | 工作量 | 排期 | 负责 | 交付物 / 完成标准 | 依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| 2-1 | 工具层-读：订单异常检测（支付成功未出货）/ 库存异常变动（`tb_inventory_log` 无单扣减） | 2d | W8 周一~二 | Python | 异常识别单测（构造异常样本验证） | M1 |
| 2-2 | 工具层-读：历史维修工单检索 + 相似案例匹配（先规则检索，RAG 后置） | 1.5d | W8 周二~三 | Python | 同类故障历史召回准确 | 2-1 |
| 2-3 | 诊断子图：多轮问诊循环 + 工具编排 + 设备实体消歧（innerCode/模糊地址→设备） | 3d | W8 周三~W9 周一 | Python | "3 号门那台机器"类模糊指代可消歧（用 `tb_vending_machine.addr` 模糊匹配） | 2-1,2-2 |
| 2-4 | 诊断结论结构化：病因置信度排序 + 处置建议（Pydantic 模型） | 1.5d | W9 周一~二 | Python | 输出结构化 JSON，前端可渲染置信度条 | 2-3 |
| 2-5 | 维修工单创建卡片：结论自动写入工单备注 + 异常订单清单附档 | 1d | W9 周二~三 | Python+Java | 建单后 dkd-app 端备注可见诊断结论 | 1-3 |
| 2-6 | 前端诊断对话页改造（vm/node 页入口切换 `/agent/**`，多轮 UI 对齐原型） | 2.5d | W9 周三~W10 周一 | 前端 | 旧单轮入口跳转新对话页；历史入口兼容 | 2-4 |
| 2-7 | 故障案例库冷启动：历史维修工单清洗入库（现象/原因/处置/复发间隔） | 2d | W9 周四~W10 周二 | Python | ≥50 条案例可检索。**前置：先做 0.5d 数据质量抽样，不达标则改口径或降级为规则库** | 2-2 |
| 2-8 | 兜底与降级：Agent 不可用时回落 `/manage/ai/diagnose` 单轮逻辑 | 0.5d | W10 周三 | Java | 停 Python 服务，前端自动降级 | 0-8 |
| 2-9 | **M3 验收**：运营人员无培训完成 5 个真实故障诊断→建单闭环 | 1d | W10 周五 | 全员 | 验收记录；诊断信息量对比报告 | 全部 |

### Phase 3：运营分析 Copilot + 体系化收尾（W11~W13，11-30 ~ 12-18，15 个可用工日）

| # | 任务 | 工作量 | 排期 | 负责 | 交付物 / 完成标准 | 依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| 3-1 | 预置分析模板 4 个：趋势对比 / 占比分布 / 转化漏斗 / 排行榜（SQL 人工审核固化） | 3d | W11 周一~三 | Python+产品 | 模板 SQL 评审通过，口径与 Java 报表一致 | M1 |
| 3-2 | **评测集构建（V1 缺失，3-3/3-2 的前置）**：50~100 条真实取数问题 + 期望结果，作为参数抽取与 NL2SQL 的回归基准 | 1d | W11 周三 | Python+产品 | 评测集入库可复跑；基线准确率记录 | 3-1 |
| 3-3 | 参数抽取：时间（今日/7 天/30 天）× 维度（区域/点位/品类）自然语言识别 | 1.5d | W11 周四~五 | Python | 在 3-2 评测集上准确率 ≥90% | 3-2 |
| 3-4 | 受控 NL2SQL：SQL 静态校验器（只读/白名单/LIMIT 注入/超时熔断） | 2.5d | W12 周一~二 | Python | 注入/越权/全表扫描用例全部拦截；在评测集上执行成功率达标 | 3-3 |
| 3-5 | 前端 Copilot 页（对齐原型 V2）：图表切换 + 筛选联动 + 洞察文字 + SQL 折叠面板 | 3d | W12 周一~三 | 前端 | 四图表与筛选联动与原型一致（ECharts 实现） | 3-1,3-3 |
| 3-6 | 补货复盘自动化：7 日回看（建议量 vs 实际消耗）→ review_metrics 回写 + 参数迭代建议 | 2d | W12 周三~四 | Python | 上期计划自动生成复盘报告 | M2 |
| 3-7 | 决策审计页：agent_decision_log RuoYi 标准列表页（筛选/JSON 详情/回放） | 1.5d | W12 周四~W13 周一 | Java+前端 | 全部写操作留痕可查可回放 | 0-12 |
| 3-8 | 规则版对比报告输出（3 周双跑数据）：缺货率/建议偏差/人工调整率 | 1d | W13 周一 | Python | 对比报告 → 决定是否下线规则版入口 | 1-14 |
| 3-9 | 压测与故障演练（对话并发 / 定时任务重入 / Python 宕机降级 / SQLite 文件损坏与恢复 / MySQL 慢查询） | 2d | W13 周二~三 | Python+Java | 演练清单全过；runbook 成文 | 全部 |
| 3-10 | **M4 验收**：Copilot 上线 + 审计完备 + 安全项复查 | 1d | W13 周五 | 全员 | 高频取数 80% 自助回答（按 3-2 评测集抽样验证）；验收签字 | 全部 |

### Phase 4：上线收尾（W14，12-21 ~ 12-25，5 个可用工日）

| # | 任务 | 工作量 | 排期 | 负责 | 完成标准 |
| --- | --- | --- | --- | --- | --- |
| 4-1 | 文档收尾：接口文档 / 部署 runbook / 运维手册 / 用户操作指引 / SQLite 备份恢复手册 | 2d | W14 周一~二 | 全员 | 文档评审通过 |
| 4-2 | 上线评审 + 灰度（`agent.enabled` 按环境/按角色开关） | 1d | W14 周三 | 全员 | 上线检查单全过；回滚预案演练 |
| 4-3 | 上线值守与首日观测（trace/错误率/token 消耗/留痕完整性） | 1d | W14 周四 | Python+Java | 无 P1/P2 问题 |
| 4-4 | 缓冲（承接延期项） | 1d | W14 周五 | — | — |

### 上线后观察期（W15 起，2026-12-28 ~ 2027-03-26，13 周）

| # | 任务 | 排期 | 负责 | 完成标准 |
| --- | --- | --- | --- | --- |
| 5-1 | 周度运营指标跟踪（缺货率/排单耗时/故障响应/取数周期） | 每周一 | 产品 | 指标看板持续更新 |
| 5-2 | 月度复盘会 + 参数迭代（安全库存系数/预测窗口） | 每月末 | 全员 | 迭代建议落地 |
| 5-3 | 按置信度自动建单的放量评估（Phase 5 立项评审） | 第 4 周（01-19） | 全员 | 复盘数据支撑决策 |
| 5-4 | 3 个月效果评估报告（对照方案第八节指标） | 第 13 周（03-26） | 产品 | 达标结论 + 下期规划 |

---

## 四、关键依赖与风险缓冲

| 风险点 | 影响任务 | 缓冲措施 |
| --- | --- | --- |
| **Java 工程师投入不足**（V1 最大排期风险） | 0-6/0-7/0-8/0-14/1-3/1-9/2-5 | W1~W3 提至 ≥80%（数学依据：Phase 0 Java 侧 7.25 人日 / 9 个可用工日）；接口契约先出文档冻结；DDL、留痕、计量主责移交 Python |
| 决策点未按时拍板（0-1） | 全链路 | **09-21 当天必须开评审会**；未决项按方案默认值先行（人工确认 / DeepSeek / 同机部署 / SQLite checkpointer） |
| 法定假期压缩工期 | Phase 0/1 | 本版已按实际可用工日（64 天）排；W14 留 1 天缓冲；调休上班日（09-20/10-10）作为额外储备 |
| 业务表 DDL 不在仓库（`sql/` 下只有 RuoYi 系统表 + `tb_report.sql`），无法评估数据量与索引 | 0-4/1-2/3-4 | 0-4 增加"数据量与索引勘查"并归档 `docs/ddl/`；1-2 强制范围比较 + 分批 + 超时；Phase 3 前评估只读实例/从库 |
| 分析查询与业务共库抢资源（`slave.enabled=false`，单主库） | 1-2/3-4/3-9 | 分析限定凌晨窗口 + 按 vm 分批 + 结果缓存；3-9 含 MySQL 慢查询演练；Phase 3 前把"只读实例/从库"列入决策 |
| 补货接单人区域不匹配（`insertTaskDto` 强校验 `emp.regionId == vm.regionId`） | 1-8/1-13 | 新增 1-8 分配策略与并发幂等；M2 验收前用 10 台跨区域设备预演 |
| dkd-app 接单流转环境未就绪 | 1-13 | 提前 1 周（W6）确认 dkd-app 测试环境、测试账号与员工区域数据 |
| LLM 建议质量不达标 | 1-4/1-5 | 统计基线不依赖 LLM，LLM 仅做校准与理由生成——最坏退化为"可解释的规则增强版" |
| 真实设备样本不足 | 1-13 | 与规则版并行双跑延至 3 周（W8~W10），M3 期间继续采集 |
| 历史维修工单数据质量未知 | 2-7 | 2-7 前先做 0.5d 数据质量抽样，不达标则降级为规则库 |
| LangGraph 版本漂移 / checkpoint 向后再兼容约束 | 0-2/1-6 | 0-2 锁定精确版本三元组；0-13 冻结 state schema；升级需走"兼容性评审 + 存量会话处理方案" |
| 技术债：本机 Redis 3.2.100（2016 版，无模块/无 Streams/`appendonly no`/与 Java 共用 db0） | — | 本期用 SQLite saver 绕开；登记为独立技术债（升级 Redis 或迁 Linux），触发条件：多实例部署或需跨机共享会话 |

---

## 五、每周节奏建议

- **周一**：15 分钟站会定本周验收点（对照上表"完成标准"）
- **每天**：Python/Java/前端各自更新任务状态（本清单可直接作为跟踪表）
- **周五**：里程碑内做半天联调 + 验收演示（G1 在 09-30、M1 在 10-09、M2 在 11-06、M3 在 11-27、M4 在 12-18）
- **里程碑门禁**：每个 M 结束后做一次"是否具备进入下阶段"的显式判断，不达标不进下一 Phase（V1 无此门禁）

---

## 六、修订记录（V2 vs V1）

| 类别 | V1 内容 | V2 处理 | 依据 |
| --- | --- | --- | --- |
| 假期区间 | "10 月国庆 W3~W4 部分影响" | 修正为"中秋 09-25~27、国庆 10-01~07"，W1~W3 可用工日 4/3/2 | 国务院办公厅 2026 年放假通知 |
| 总工期 | 10 周（50 工作日） | 14 周（64 个实际可用工日，扣假期后为 58 个执行工日） | 上述假期 + 人力负载核算 |
| Phase 0 Java 负载 | 7 人日排 7 天 × 30% 投入（3.3× 超载） | Java 提至 ≥80%；DDL/留痕/计量主责移交 Python；M1 拆 G1（09-30）+ G2（10-09） | 逐任务人日核对 |
| 表名白名单（0-4） | `inventory/order/task/...`（无 `tb_` 前缀，且漏表） | 修正为 `tb_` 真实表名，补 `tb_inventory_log`/`tb_task_details`/`tb_emp`/`tb_channel`；验收加"读非白名单表被拒" | `mapper/manage/*.xml` 实测 |
| Redis checkpointer（0-10） | "复用现有 Redis，db 隔离规划" 1d | 实测 Redis 3.2.100 无模块/无 Streams → 改 SQLite saver + 迁移触发条件；登记 Redis 版本技术债 | `E:\Redis-x64-3.2.100` 实测 |
| 安全整改（0-14） | "密钥轮换 + 外置环境变量" 1d，排在 10-02（假期） | 14a 已完成（2026-09-21 复核：初始提交已重写，可达历史无明文）；剩余 14b 轮换 + 14c 清 dangling 对象，1d | 旧 DeepSeek Key 实测 401 已失效；`git fsck` 可见 dangling `5d56572` |
| 订单聚合（1-2） | 未提索引问题 | 明确禁用 `date_format()` 包裹列，改范围比较 + 分批 + 超时 | `OrderMapper.xml:44` 现有写法 |
| 接单人分配 | 无此任务 | 新增 1-8（区域分配 + 幂等/并发确认） | `TaskServiceImpl.java:160-167,147-158` |
| LangGraph 版本 | `langgraph ≥0.2` 开放区间 | 0-2 锁定精确版本三元组；新增 0-13 state schema 冻结评审 | LangGraph/checkpoint 在 0.2→1.0 期间有破坏性变更 |
| 评测集 | 无（3-2 直接要求 ≥90%） | 新增 3-2 评测集构建作为 3-3/3-4 前置 | AGENTS §8 增量测试标准 |
| 成本计量 | 3-9（W10） | 前移至 0-12（Phase 1 就开始烧 token） | 方案 §7 成本风险 |
| 测试工时 | 未计入 | 全部估算已含 §8 测试时间（约 +25%），不再另行列项 | AGENTS §8 |
| request_id | 无对应任务 | 并入 0-9 | AGENTS §6.4 |
| 压测范围 | 1.5d（4 项） | 2d，增加 SQLite 文件损坏恢复与 MySQL 慢查询 | 0-10 选型变更 |
| 里程碑门禁 | 无 | 每个 M 结束做显式进入判断 | 本次评审建议 |

---

*本清单与方案文档（`docs/DKD智能体接入方案-LangChain-LangGraph.md` V1.1）及原型 V2（`docs/prototypes/dkd-agent-prototype.html`）配套使用。任务编号已按 Phase-序号编码，可直接映射为 Epic（Phase）/Story（任务）。*

---

## 七、执行进度（滚动更新）

> 更新于 **2026-09-21**（W1 周一，Phase 0 第 1 天）。状态图例：✅ 已完成并验证 / 🔄 进行中 / ⏳ 待办 / ⛔ 阻塞。

### Phase 0（W1~W3，09-21 ~ 10-09）

| # | 任务 | 状态 | 证据 / 备注 |
| --- | --- | --- | --- |
| 0-1 | 方案评审会 | ✅ | 2026-09-21 评审通过，方案 V1.1 + 排期 V2 已定稿 |
| 0-2 | Python 环境与依赖锁定（uv + Python 3.11.13） | ✅ | `uv.lock` 锁定：langgraph **1.2.11** / langgraph-checkpoint **4.2.0** / checkpoint-sqlite **3.1.1**（方案原写 `≥0.2`，实际落 1.x，印证版本漂移风险） |
| 0-3 | LLM 连通（DeepSeek） | ✅ | live 冒烟 HTTP 200 / 5.4s；单元测试全打桩（§8 要求）。**实测服务端返回 `deepseek-flash` 而非配置的 `deepseek-chat`** → 计量按实际模型记 |
| 0-4 | 只读账号 + 白名单 | ✅ | `dkd_agent` 两级授权；**8 项拒绝路径实测**：写业务表 / 读非白名单表 / 读系统表 / 跨库读 / DELETE 自有表 / DDL 全部 `ERROR 1142` 被拒 |
| 0-5 | `agent_*` 四表 DDL | ✅ | 本地 dkd 库执行通过；幂等重跑退出码 0；唯一键拒绝路径命中 `ERROR 1062`；`agent_message` 后续补 `user_id` 列（计量需要） |
| 0-6 | Java 网关（`AgentGatewayController` 等） | ✅ | `dkd-common` 新增 `com.dkd.common.agent`（9 类）+ 6 个测试类；**SSE 不缓冲实测证据**：51 帧/3.2s，首 delta 距首行 70ms、末帧距首帧 3217ms。**偏离项**：`dkd-common` 无 `spring-webmvc` → 改同步写 Servlet 输出流逐块 flush + Filter（见下“本轮详细记录之 5”） |
| 0-7 | Java 回调（`AgentCallbackController`） | ✅ | `/agent/callback/task` 服务间密钥 + 白名单 + 限流；实机 4 项拒绝路径：无密钥 401 / 伪造 401 / 未配置密钥 503 / 非白名单 404；正确密钥 → 鉴权放行并由 `insertTaskDto` 校验链拒绝（“售货机不存在”**原样回传**，未写库） |
| 0-8 | G1 中间验收（SSE 端到端透传） | ✅ 服务端链路全过（前端视觉待手测） | `docs/scripts/g1-gateway-smoke.sh` **16/16 PASS**（登录→状态→SSE 逐帧+requestId 回带→回调 3 类拒绝→未认证信封→停机降级）；一键降级 `--agent.enabled=false` 另测 4 项全符 |
| 0-9 | Python 服务骨架（/health + SSE + 鉴权 + request_id） | ✅ | ruff / pytest 全绿；SSE 实测 meta→delta→done 逐帧输出、身份头透传、客户端断开即停 |
| 0-10 | checkpointer 落地（SQLite） | ✅ | 跨请求恢复（history_len 2→4）、进程重启后仍可读回、`Last-Event-ID` 断点续传（只补发未收帧）均已验证 |
| 0-11 | 前端 SSE 客户端 + 侧边栏壳 | ✅ | `sseFrames.js`（纯分帧）/`sse.js`（fetch+ReadableStream+AbortController+空闲超时）/`api/manage/agent.js`/`store/modules/agent.js`/`components/AgentAssistant/index.vue` + Navbar 入口 + layout 挂载；`npm run build:prod` exit 0；**用真实抓包字节流跑 18 项分帧断言全过**（含 303 个二分切点/逐字符/随机分片/CRLF/半帧） |
| 0-12 | request_id + 留痕 + 成本计量与限额 | ✅ | 留痕/消息投影实写 MySQL（手机号实测脱敏为 `138****5678`，明文 0 行）；限额熔断 429；`/agents/usage/summary` 鉴权 401/200 正确；**审计库故障降级不中断对话**（3 项降级测试覆盖） |
| 0-13 | LangGraph 版本锁定 + state schema 评审 | ✅ 评审稿就绪（待开会确认） | 交付 `docs/dkd-agent-restock-state-schema.md`（冻结表 + 中断契约 + 兼容规则 + 10 项检查清单）；代码侧 `restock_state.py` 冻结 + 15 项契约测试；**评审中发现 `expect_capacity` 语义陷阱**（见该稿 §4.2） |
| 0-14 | 安全整改（14a ✅ / 14c ✅ / **14b 部分完成**） | 🔄 | **14c A/B 实测**：清理前对象库密钥模式命中 **2** 处、未可达提交 2 个（`5d56572`/`0fdbd15`，实测 6 行明文凭据）→ `reflog expire + gc --prune=now` 后未可达对象 **0**、密钥命中 **0**；**14b 新增进展**：新增 `docs/security-credential-rotation.md` runbook（四步流程 + 逐凭据影响面/验证/回滚 + 变更记录）；已实际轮换 `dkd_agent` 库账号（旧口令 1045、权限矩阵 3 项拒绝全中、Python 侧留痕写入复测通过），并连带修复依赖缺口（新增 `cryptography`，否则口令变更会因 `caching_sha2_password` 全量握手把智能体打挂）；**待人工**：MySQL root / Redis / JWT secret / OSS AK / Druid 口令（#1~#4、#6）需值班窗口与云控制台 |
| 0-15 | **M1 验收（G2）**：全链路演示 + 降级演练 + 安全复查 | ✅ 主体通过（2 项明确未达，见备注） | ① 真 LLM 全链路：前端→网关→Python→DeepSeek→SSE，`meta.mode=llm` / `model=deepseek-flash`，980ms，`X-Request-Id` 从网关贯穿到 `agent_decision_log`/`agent_message`；② 降级演练：停 Python → `upstream=down` + `degrade:true` 帧；`--agent.enabled=false` → 503 信封 + SSE 降级帧；未认证仍拒（信封 401）；③ 安全复查：跟踪文件明文凭据 0、对象库密钥 0、回调 4 类拒绝路径全过、`agent_decision_log` 明文手机号 0 行；④ 质量门禁：pytest **70 passed**/93.58%、mvn **42 passed**、`npm run build:prod` exit 0。**未达项**：(a) 0-14b 凭据轮换需人工/值班窗口；(b) 前端浏览器视觉未验（见下之 6）。**新发现缺口**：(c) LLM 节点仍用 `ainvoke`，属**帧级**流式而非 token 级（首帧延迟 ≈ 整段 LLM 时延），建议 Phase 1 的 1-5 节点改 `astream` + `stream_mode="messages"` |

### 质量门禁现状

| 项目 | 结果 |
| --- | --- |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 29 files already formatted |
| `uv run pytest` | **71 passed**（1 live 用例默认 deselect），覆盖率 **93%+**（要求 ≥70%） |
| `mvn -pl dkd-admin -am test`（**本轮新增**） | **Tests run: 42, Failures: 0, Errors: 0**（Filter 7 / Gateway 10 / Routing 3 / SseRelay 6 / TokenFilter 8 / UpstreamClient 8）；纯单测，不依赖 MySQL/Redis/Spring 上下文 |
| `npm run build:prod`（**本轮新增**） | exit 0（仅既有 chunk 体积告警） |
| 端到端（**本轮新增**） | `docs/scripts/g1-gateway-smoke.sh` **16/16 PASS**；SSE 增量到达实测；`agent.enabled=false` 降级 4 项全符 |
| DDL 真库执行 | 幂等重跑 + 唯一键拒绝路径 + 8 项权限拒绝路径均已实测 |

### 本轮（0-6 / 0-7 / 0-8 / 0-11 / 0-14c）详细记录

**1. 新增文件（Java）**：`dkd-common` 的 `config/AgentConfig|AgentProperties`、`controller/AgentGatewayController`、`filter/AgentTokenFilter|AgentCallbackAuthFilter`、`domain/AgentHeaders|AgentUserContext|AgentUpstreamResult`、`support/AgentUpstreamClient|AgentRequestId|AgentUpstreamException|AgentClientAbortException|AgentRegionResolver|DefaultAgentRegionResolver`；`dkd-manage` 的 `AgentCallbackController`；`dkd-admin/src/test` 6 个测试类 + `FakeAgentServer` 夹具。
*未改动任何现有业务类*；`dkd-admin/pom.xml` 仅新增 `spring-boot-starter-test`（test scope）与 pin `surefire 2.22.2`（父 POM 无 Boot parent，默认 surefire 2.12.4 会**静默跳过** JUnit5 用例，比红灯更危险）。

**2. 测试发现并修掉的两个真 bug（均已加回归用例）**：
- `forward()` 必须先读状态码再读响应体：JDK `HttpURLConnection.getErrorStream()` 仅在响应码已读出时才返回错误流，顺序反了会把上游 4xx/5xx 误判为“不可达”，**4xx 透传语义直接失效**；
- requestId 被解析两次（响应头与转发头各生成一个 UUID）→ 全链路 trace 断裂。

**3. 评审追加的两处修正（本次 CR 发现）**：
- 回调过滤器原注册在 order=0（安全链**之后**），导致“非白名单路径 → 404”永远被 Spring Security 的 401 抢先（白名单形同装饰）→ 改为 order=-200，实测 404 生效；
- `X-Request-Id` 原样透传客户端头（日志注入/非法响应头面）→ 新增 `AgentRequestId` 净化（仅可见 ASCII、≤64 字符、全非法则重新生成）+ 2 项回归用例，并将 filter 与网关重复的 requestId 解析合并为一处。

**4. 一键降级实测（`--agent.enabled=false` 启动）**：`/agent/status` → `enabled=false, upstream=down`（开关关闭时不再探活）；非 SSE 路径 → HTTP 503 + 信封；`/agent/chat` → `error{degrade:true}` + `done` 帧；未认证访问 → 沿用 RuoYi 约定 HTTP 200 + `code:401`（与 `/manage/**` 行为逐字对比一致，**降级不等于免鉴权**）。

**5. 与任务书的偏离（需项目负责人知悉，已同步方案 §3.3）**：任务书要求 `StreamingResponseBody`/`HandlerInterceptor`，但 **`dkd-common` 只依赖 `spring-web`、不含 `spring-webmvc`**（`dependency:tree` 实测为空），在该模块无法编译。落地改用：SSE = 控制器直接写 `HttpServletResponse` 输出流、读一块写一块并 flush；回调 = `AgentCallbackAuthFilter`。语义合同（禁止先读完再返回、逐帧 flush）不变，且同步写不受 Tomcat 异步 `asyncTimeout`（30s）掐流影响；代价是每个在途对话占用一个 Tomcat 工作线程（max=800）。若改回原名 API，只需给 `dkd-common` 加 `spring-webmvc` 依赖。

**6. 风险与未验证项（不隐瞒）**：
- **前端未做浏览器视觉验证**：Playwright/Chrome 自动化会引入未声明依赖，按 AGENTS §8 以“构建通过 + 手测清单”交付（清单见下）；SSE 增量到达（51 帧/3.2s）已在 HTTP 层证实；
- **流式为帧级而非 token 级（M1 发现，✅ 已修）**：已改为 `astream(stream_mode=["messages","values"])` 逐 token 下发，实测 13~14 帧 / 首 token 距 meta **545ms** / 末 token 距首 token **487ms**（修复前整段 980ms）；回归锚点 `tests/test_chat_llm_mode.py::test_llm_tokens_arrive_as_multiple_delta_frames`（帧数退化成 1 即失败）；
- **回调不回传 `taskId/taskCode`**（`insertTaskDto` 只返回影响行数）→ 1-3/1-8 需一并处理，已标 TODO；
- **`X-Agent-Region` 缺省**：当前 schema 无 sys_user → 区域映射（影响仅审计上下文，1-8 按设备区域选人不受影响）；
- **`@RateLimiter` 的 Redis 依赖**：Redis 不可用时回调用例会失败（fail-closed），属预期行为，未做演练；
- **0-14b 凭据轮换未做**：旧凭据虽已不在仓库与对象库（14a/14c 已验），但轮换前仍算“已泄露过的凭据”，属上线阻塞项。

**6b. 0-15 M1 验收实测证据（2026-09-21）**：
- 真 LLM 全链路：`POST /agent/chat`（经网关，带 JWT 与 `X-Request-Id: m1b-***`）→ 980ms 返回；meta 帧 `{"mode":"llm","model":"deepseek-flash"}`；`X-Request-Id` 响应头与 meta 帧一致；
- 留痕/计量：`agent_decision_log` 新增 2 行（`request_id` 为网关注入值、`cost_tokens` 26/30）；`agent_message` 2 行（`model=deepseek-flash`，tokens 17/9 与 17/13）；`/agents/usage/summary` → `total_calls=2, tokens_total=56`；
- 发现 1 个观测性缺陷并已修：`meta.mode` 硬编码 `echo`（真 LLM 调用被误标）→ 见 `fix(agent)` 提交（含 3 项新测试，LLM 节点全打桩）。

**7. 前端 0-11 冒测清单（✅=已由 `docs/scripts/agent-assistant-browser-check.cjs` 自动化，其余需人工）**：
1. ✅ 顶栏出现「AI 助手」入口，点击打开右侧抽屉（绿点在线/橙点降级；颜色态未自动化）；
2. 首次打开时探测 `/agent/status`：`enabled=false` 或 `upstream=down` → 显示降级横幅且输入框禁用，点「重试」可恢复；
3. ✅ 发送问题 → AI 气泡**逐字增长**（实测 1→50 字）+ 光标闪动，结束时消失；
4. ✅ 生成中点「停止」→ 立即中止（AbortController），气泡显示“（已取消）”，`finally` 复位后可再发；中断后继续对话亦已验证；
5. 快捷问题条点击即发送；场景 Tab 切换会清空会话并提示新会话；
6. ✅ 新建会话按钮清空消息（自动化）；`conversation_id` 重新下发由 API 契约保证；
7. 关掉抽屉再打开：对话内容保留（组件挂在 layout 层）；
8. ✅ AI 气泡内 Markdown/HTML 已过 DOMPurify（自动化：注入 `<img src=x onerror=...>` 不执行）；
9. 停掉 Python 进程后再发一条：出现“暂不可用”错误气泡 + 降级横幅，输入框禁用；
10. `agent.enabled=false` 重启 Java 后打开抽屉：横幅提示“智能体服务未启用”。

### Phase 1（W4~W7，10-12 ~ 11-06）——已开工

| # | 任务 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1-1 | 工具层-读：库存 / 货道档案 / 设备 / 点位查询 | ✅ | `app/tools/read_tools.py`：`get_machine_profile` / `list_operating_machines` / `list_channel_stock`；**12 项单测**（SQL 形状、分批、超时、行数上限、白名单拒绝路径）+ **4 项 live 抽样**（真库 3 台设备逐字段核对）；发现并如实暴露”inventory 与 channel 档案 3/3 不一致“ |
| 1-2 | 工具层-读：近 30 天订单聚合 + 在途补货工单 | ✅ | 范围比较（删 `date_format`）+ 按 vm 分批（120 设备→3 批）+ 超时熔断 + LIMIT；**对账通过**（tool amount=7/count=5 == 独立 SQL）；`list_inflight_tasks` 合并明细货道；EXPLAIN 留档 `docs/ddl/business_tables_survey.md`（量化：同一查询预估行 199430 → 2） |
| 1-3 ~ 1-14 | 工单回调封装 / 基线引擎 / LLM 校准 / 状态机 / 前端工作台等 | ⏳ | 下一步 |

**Phase 1 首轮发现（待产品/Java 侧决策，详见勘查记录 §三）**：
① **Java 报表 SQL 丢了参数绑定**（`ReportMapper.xml:122-130` 只用 `status >= 1`，未用 `#{status}`/时间窗）→ 现有报表数字与时间窗无关，不能作为对账基准；
② **`tb_order` 无 `inner_code`/`create_time` 索引** → 已出待评审 DDL `docs/ddl/add_index_tb_order_and_tb_task.sql`（附回滚）；
③ **`tb_inventory` 与 `tb_channel` 的 `sku_id`/容量实测 3/3 不一致** → 读工具如实双返并标记 `data_consistent=false`，需业务确认权威源；
④ 销量口径固定 `status=2`（出货成功，`ReportServiceImpl.java:28`），已做成配置项。

### 阻塞与待办需求（需项目负责人决策/提供）

1. **0-15（M1 验收）待排**：Python 侧改 `DKD_AGENT_USE_LLM=1` 后跑真 LLM 全链（本轮为 echo 链路）；`agent.enabled=false` 降级演练已提前完成；
2. **0-14b 剩余凭据轮换需值班窗口**（MySQL root / Redis / JWT secret / OSS AK / Druid）；
   其中 JWT secret 轮换会踢掉全部在线用户；完整步骤与回滚见 `docs/security-credential-rotation.md`；
3. **`DKD_AGENT_SERVICE_SECRET` 两端一致性**：本机 `.env` 已生成，Java 与 Python 均已读取并实测通过；上线前需在部署环境注入同一值；
4. **`.env` 不会自动生效**：Spring Boot / Python 均不读 `.env`，需在 IDE/`setx` 注入（详见 `dkd-agent/README.md`）；
