# DKD 帝可得智能售货机平台

> 售货机运营管理平台（管理后台 + 运维移动端 + 管理前端 + **智能体服务**）。
> 后端基于 RuoYi-Vue 3.8.7 改造；智能体为旁路集成，**读走 MySQL 只读账号、写一律回调 Java REST**。

---

## 一、子系统总览

| 子系统 | 技术栈 | 端口 | 职责 |
| --- | --- | --- | --- |
| `dkd-parent` | Spring Boot 2.5.15 / **Java 8** / MyBatis / Druid / Redis，Maven 7 模块 | 8080 | 管理后台，**业务事实唯一所有者**（事务 / 校验 / 权限 / 审计） |
| `dkd-app` | Spring Boot / MyBatis-Plus / JWT | 9070 | 运维移动端 API（补货 / 维修作业），**与后台共用 `dkd` 库** |
| `dkd-vue` | Vue 3.4 / Element Plus 2.4 / Vite 5 / Pinia / ECharts 5 | 80(dev) | 管理前端（RuoYi-Vue3 模板，JavaScript 非 TS）；`/dev-api` 代理到 Java `:8080`，**不直连 Python** |
| **`dkd-agent`** | Python 3.11 / FastAPI / LangGraph / SQLAlchemy(async) | 8090 | **智能体服务**（旁路）：补货 Agent / 诊断 Agent / 运营分析 Copilot |

依赖基础设施：**MySQL 8.0**（库名 `dkd`）、**Redis 3.2.100**（本机实例，见 §五 已知限制）。

### 核心业务模型

```
区域 Region → 点位 Node → 售货机 VendingMachine → 货道 Channel
商品 Sku/SkuClass/Policy · 交易 Order · 库存 Inventory/InventoryLog · 作业 Task/TaskDetails
```

核心闭环：

```
投放上架 → 库存初始化 → 扫码下单(Order) → 库存扣减(InventoryLog)
→ 库存预警(低库存/缺货) → 补货建议 → 人工建工单(Task 校验：设备状态/防重/员工区域)
→ 运维端接单 → 现场补货 → 库存回写 → 循环
```

> 业务表统一 `tb_` 前缀（`tb_inventory`/`tb_order`/`tb_task`/`tb_task_details`/`tb_inventory_log`/`tb_channel`/`tb_sku`/`tb_vending_machine`/`tb_node`/`tb_region`/`tb_emp` …）；
> 智能体新增表统一 `agent_` 前缀。**注意：旧文档中"业务表无前缀"的说法是错的**。

---

## 二、目录结构

```
DKD/
├─ AGENTS.md                  # 开发规范（人 + AI 协作共同遵守，优先于个人习惯）
├─ .env.example               # 全部环境变量模板（真值只放本机 .env，不入仓）
├─ dkd-parent/                # 管理后台 Maven 多模块
│  ├─ dkd-admin/              #   启动入口 + 系统 Controller
│  ├─ dkd-framework/          #   安全(JWT) / AOP / 拦截器
│  ├─ dkd-system/             #   系统域（用户/角色/部门/字典/日志）
│  ├─ dkd-manage/             #   ★ 核心业务域（设备/货道/商品/点位/订单/工单/库存）
│  ├─ dkd-common/             #   通用工具 + 现有 AI 包 com.dkd.common.ai（降级兜底链路，保留）
│  ├─ dkd-quartz/             #   定时任务
│  ├─ dkd-generator/          #   代码生成器
│  └─ sql/                    #   RuoYi 系统表 / quartz / 报表脚本
├─ dkd-app/                   # 运维移动端 API（只含作业域）
├─ dkd-vue/                   # 管理前端
├─ dkd-agent/                 # ★ 智能体服务（Python）
│  ├─ app/api/                #   HTTP 层（chat SSE / ops）
│  ├─ app/graphs/             #   LangGraph 图与 state schema（含 restock_state 冻结版）
│  ├─ app/{checkpoint,audit,db,llm,security,config}.py   # checkpointer/留痕计量/DB/LLM/鉴权/配置
│  ├─ app/tools/             #   （Phase 1 待建）工具层：MySQL 只读查询 / Java REST 回调封装
│  └─ tests/                  #   pytest（LLM 一律打桩，真实调用走 -m live）
└─ docs/                      # 方案 / 排期 / DDL / 原型 / 复盘笔记
```

---

## 三、快速开始

### 0）准备环境变量（必做，否则服务起不来）

所有凭据已从配置文件中移除，改为环境变量注入：

```bash
cp .env.example .env      # 填入真实值；.env 已被 .gitignore 排除，不会入仓
```

> ⚠️ **Spring Boot / Python 都不会自动读取 `.env`**。
> - 临时（Git Bash）：`set -a; source .env; set +a`
> - 永久（Windows）：`setx DKD_DB_PASSWORD "..."`（需重开终端 / IDE）
> - IDE：在运行配置的 Environment variables 中逐项添加

### 1）后端

```bash
cd dkd-parent
mvn -pl dkd-manage -am compile -q     # 增量编译核心业务模块
# 启动（需已注入环境变量）：mvn -pl dkd-admin spring-boot:run
```

### 2）前端

```bash
cd dkd-vue
npm install
npm run dev          # 开发
npm run build:prod   # 构建（含语法检查）
```

### 3）智能体服务（dkd-agent）

用 **uv** 管理 Python 与依赖（要求 3.11+）：

```bash
cd dkd-agent
uv sync                                            # 按 uv.lock 精确安装
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
uv run pytest                                      # 全量测试（覆盖率门槛 70%）
uv run ruff check . && uv run ruff format --check .
```

首次运行需执行建表与建权脚本（详见 `docs/ddl/`）：

```bash
mysql -h127.0.0.1 -uroot -p dkd < ../docs/ddl/agent_tables.sql          # agent_* 四表
mysql -h127.0.0.1 -uroot -p    < ../docs/ddl/create_agent_db_user.sql   # dkd_agent 账号（两级授权）
```

---

## 四、智能体集成的四条硬约束（改代码前必读）

1. **业务事实唯一所有者是 Java**：所有业务写操作（建单/改库存等）**只能**走 Java REST 回调，复用
   `TaskServiceImpl.insertTaskDto` 的事务与校验链（设备状态 / 设备级工单防重 / 员工区域匹配）。
   **任何"图方便直接 UPDATE 业务表"的代码一律拒绝。**
2. **MySQL 账号两级授权**（`docs/ddl/create_agent_db_user.sql`）：
   - 业务表 `tb_*`（白名单内）：**仅 SELECT**；
   - 自有表 `agent_*`（会话/留痕/计划）：`SELECT/INSERT/UPDATE`，**无 DELETE（软删除）、无 DDL**；
   - **禁止跨库**（本机 MySQL 上还有 8 个其他项目库）。
3. **鉴权不裸奔**：前端统一经 Java 网关（`/agent/**`）；Python **不解析 JWT**，只信任网关注入的
   `X-Agent-User / X-Agent-Roles / X-Agent-Region`，服务只监听内网/回环。回调与运维接口用**服务间密钥**（两套体系不得混用）。
4. **写操作 human-in-the-loop**：首期所有建单类写操作必须人工确认；LLM 输出视为不可信输入
   （结构化解析 + 范围夹取 + 参数白名单）。

`agent.enabled=false` 时一键降级回原有单轮 AI 链路（`com.dkd.common.ai`），**Python 服务不可用时主业务零影响**。

---

## 五、已知限制与坑（换机器请先看这段）

| 项 | 现状 | 影响 / 规避 |
| --- | --- | --- |
| **Redis 3.2.100**（Microsoft 2016 移植版） | 无 `MODULE` 系统（不支持 RedisJSON/RediSearch）、内核 < 5.0（无 Streams）、`appendonly no`、与 Java 共用 db0 | **不可用作 LangGraph checkpointer，也不可作 Stream 队列**；会话持久化用 SQLite saver |
| **单主库、无从库** | `application-druid.yml` 中 `slave.enabled: false` | 分析类查询必须限定时间窗口 + 分批 + 超时熔断，**禁止 `date_format()` 包裹索引列**（否则全表扫描） |
| **业务表 DDL 未入库** | `dkd-parent/sql/` 只有 RuoYi 系统表 / quartz / 报表 | 涉及表结构工作前先导出归档至 `docs/ddl/` |
| **本机 PATH 有 4 个 Python**（3.8/3.10/uv 托管 3.11/WindowsApps） | 裸敲 `python`/`pip` 会误中 3.8；uv 创建的 venv **不含 pip** | 一律用 `uv run ...` / `uv pip ...` |
| `expect_capacity` 字段语义 | `TaskDetailsDto` 注释写「货道容量」，但 app 侧**当作补货数量累加进库存** | 建单必须传建议补货量，**绝不能传容量**；详见 `docs/dkd-agent-restock-state-schema.md` §4.2 |
| 凭据整改未收尾 | 源码已占位符化、可达 Git 历史无明文 | 仍待**轮换**（旧 DeepSeek Key 实测已失效；DB/Redis 为弱口令）与清理 dangling 对象 |

---

## 六、文档索引

| 文档 | 内容 |
| --- | --- |
| `AGENTS.md` | **开发规范**（安全红线 / 命名 / 测试门槛 / CR 准入 / AI 协作流程）——动手前必读 |
| `docs/DKD智能体接入方案-LangChain-LangGraph.md` | 智能体接入方案 V1.1（架构决策 / 读写分离 / 技术选型 / 风险 / 证据索引） |
| `docs/DKD智能体接入项目排期计划.md` | 排期计划 V2（14 周里程碑 + 任务清单 + **§七 执行进度**） |
| `docs/dkd-agent-restock-state-schema.md` | 补货子图 state schema 冻结稿（字段契约 / 中断恢复 / 版本兼容 / 评审清单） |
| `docs/DKD智能体项目-面试讲解与复盘.md` | 面试讲解分层话术 / 技术选型理由 / **9 类真实踩坑复盘** / 知识库与向量库选型讨论 |
| `docs/ddl/agent_tables.sql` | `agent_*` 四表 DDL（含回滚与增量 ALTER） |
| `docs/ddl/create_agent_db_user.sql` | `dkd_agent` 两级授权账号 + 8 项权限验证矩阵（6 拒绝 / 2 允许） |
| `docs/scripts/g1-gateway-smoke.sh` | **网关端到端冒烟脚本**（0-8 验收）：登录 → SSE 逐帧 → requestId 贯穿 → 回调 4 类拒绝 → 未认证信封 → 停机降级，16 项断言 |
| `docs/prototypes/dkd-agent-prototype.html` | 前端原型 V2（AI 侧边栏 / 补货工作台 / 分析 Copilot） |
| `dkd-agent/README.md` | 智能体服务：环境准备 / 运行 / 环境变量 / 进度 |

---

## 七、当前状态（2026-09-21）

**已完成并验证**（Phase 0：Python 侧 + Java 侧 + 前端壳）：`agent_*` 四表 DDL 真库执行、两级授权账号（8 项验证矩阵实测：6 项拒绝 + 2 项允许）、
`uv.lock` 精确版本锁定、LLM 连通（含 live 冒烟）、服务骨架（`/health` + SSE + `request_id` 贯穿）、
SQLite checkpointer（跨请求恢复 / 重启可读回 / `Last-Event-ID` 断点续传）、
决策留痕与成本计量（强制脱敏、限额熔断 429、用量报表、**审计库故障降级不中断对话**）、
补货 state schema 冻结（含契约测试）；
**Java 网关与回调**（`/agent/**` 转发 + 身份头白名单重建 + SSE 逐块中继 + 回调密钥/白名单/限流，纯单测 42 项）；
**前端 AI 助手侧边栏**（`fetch`+`ReadableStream` SSE 客户端、逐帧渲染、可取消、降级横幅）。

**质量门禁**：Python `pytest` **70 passed / 1 deselected / 覆盖率 93.58%**（要求 ≥70%）/ `ruff` 全过；Java `mvn -pl dkd-admin -am test` **42 passed**；
前端 `npm run build:prod` exit 0；端到端 `bash docs/scripts/g1-gateway-smoke.sh` **16/16 PASS**（含 SSE 不缓冲实测：51 帧/3.2s，首 delta 距首行 70ms）。

**待办**：Python 侧真 LLM 全链联调（0-15 M1 验收）、前端人工手测（清单见排期 §七之 7）、
凭据轮换（0-14b，需值班窗口）、补货与诊断 Agent 主体（Phase 1/2）。

---

## 八、开发规范摘要（完整见 `AGENTS.md`）

- **安全红线**（违反即阻塞合并）：密钥永不入库；SQL 只用 `#{}`；Agent 读写分离；软删除；鉴权不裸奔；LLM 输出视为不可信；前端 `v-html` 必 sanitize；敏感信息脱敏；写操作人工确认。
- **Java 8 语法边界**：禁止 `var` / `record` / switch 表达式 / `List.of()` 等高版本特性。
- **一次提交 = 一个问题**（Conventional Commits，中文描述，说明"为什么"）。
- **测试门槛**：修复 bug 必须带防回归测试；校验/风控逻辑必须覆盖**拒绝路径**；`dkd-agent` 覆盖率 ≥70% 且 LLM 调用一律打桩。
- **流程**：`main`（稳定）/ `develop`（集成）/ `feature/*`；**AI 助手不得自行 push/merge `main`/`develop`**（需负责人批准）。
