# 智能体存储后端 · PostgreSQL 选型评审稿（V0.1 · 待评审）

> 状态：**待评审**（评审通过后置为「已定稿」，并在本文件顶部标注评审日期、结论与版本号）
> 起因：评审会（0-1）决策点 7「是否接受单机会话存储？」的延伸讨论——「智能体知识库直接换 PG 行不行？」
> 影响面：排期 **0-10（checkpointer 落地）**、**FIX-14（Redis 3.2.100 技术债）**、Phase 2 的 2-7（故障案例库 RAG，方案写「RAG 后置」）、AGENTS §1 约束 5（基础设施实测边界）
> 相关文档：方案 V1.1 §5.1 / §4.2、排期 V2.6 §七/§八、`docs/DKD智能体项目-面试讲解与复盘.md` §4.2 / §九、`docs/ddl/create_agent_db_user.sql`
> 评审人：项目负责人（决策）+ Python + Java + 运维（各 0.25 人日；无需全员到齐）
> 预估落地成本：**方案 A 约 0.5~1 人日**（含本机真库验证与门禁），另加 0.5 人日评审与文档同步

---

## 1. 一句话结论

**能换，而且本机比现有文档假设的更可行**（实测本机已有 PostgreSQL 17 服务在跑）。
但「换 PG」其实是**三件不同的事**，代价差一个数量级，必须拆开决策：

| 层 | 内容 | 建议 | 理由摘要 |
| --- | --- | --- | --- |
| ① 会话持久化 | LangGraph checkpointer（0-10，现 SQLite） | **可换，方案 A 推荐** | 官方 `langgraph-checkpoint-postgres` 成熟；接入点唯一（`app/checkpoint.py`）；一步消掉 FIX-14 的两条触发条件；未来 pgvector 可复用同一实例 |
| ② 智能体自有表 | `agent_conversation` / `agent_message` / `agent_decision_log` / `agent_restock_plan` / `agent_restock_pause` | **不建议本期搬** | 会打断 **3-7 决策审计页**的数据通路（RuoYi 标准列表页走 MySQL/Druid，要加第二数据源或改走 Agent API）；`create_agent_db_user.sql` 的两级授权模型要出 PG 版；DDL 归档与 `assert_table_allowed` 白名单语义都要跟着改 |
| ③ 知识库 / 向量检索 | Phase 2 故障案例库 RAG（2-7） | **本期不引入**；若真要上，选 **pgvector** 与 ① 共用实例 | 复盘稿 §九已有结论：案例库百~千级，规则/关键词检索足够，「过早引入向量库是典型的过度工程」；且本机已随 SQLite saver 装了 `sqlite-vec 0.1.9`，≤10 万条有零新增组件的选项 |

**换 PG 的真正收益不在「知识库」，而在**：一次引入同时覆盖 **checkpoint + 未来向量**，从而**销掉 FIX-14 的触发条件**（多实例部署 / 跨机共享会话），避免 M2/M3 阶段二次迁移。

---

## 2. 为什么要专门评审一次

1. **它是一次选型变更，不是重构**。按 AGENTS §4.3，选型变更需方案与排期**同步改版并互相引用版本号**；排期里 0-10 与 FIX-14 的措辞、runbook 的迁移条款都绑在这条决策上。
2. **它会改变测试契约**（最容易被忽略的代价）：现在 `tests/test_checkpoint.py` 用 `tmp_path` 的真 SQLite 文件，**零外部依赖**、天然满足 AGENTS §8；换成 PG 后要么标 `-m live`、要么依赖本机 PG 服务，**不再 hermetic**。这条必须在评审时就定下来，而不是等 CI 挂了再吵。
3. **它是红线相邻变更**：AGENTS §7.3 的「读走 MySQL 只读账号、写走 Java 回调」不得因新增数据库而模糊。PG 只能承载 checkpoint（以及将来的向量索引），**不得成为业务数据的第二通道**。
4. **现有文档的前提是错的**：「本机与团队当前无 PG 经验 / 当期无 PG」与实测不符（见 §3），必须先更正，否则后面所有讨论都建立在错前提上。

---

## 3. 实测事实（2026-09-23，可复核）

| 检查项 | 实测结果 | 命令 / 依据 | 对结论的影响 |
| --- | --- | --- | --- |
| 本机 PostgreSQL 服务 | **`postgresql-x64-17` 正在运行** | `Get-Service *postgres*` | 「当期无 PG」不成立 → 引入成本低于原假设 |
| 5432 端口 | **已监听** | `(echo > /dev/tcp/127.0.0.1/5432)` 连接成功（PowerShell 等价写法：`Test-NetConnection 127.0.0.1 -Port 5432`） | 本机可直接做真库验证，无需先装 |
| Docker | **已安装但守护进程未运行**（`npipe:////./pipe/dockerDesktopLinuxEngine` 不可达） | `docker ps` | **不能靠 compose 起 PG**；开发/测试要么用本机服务，要么先拉起 Docker Desktop |
| `psql` 客户端 | 不在 PATH（未纳入本机开发链） | `where psql` | 运维文档要给 `psql` 路径或改用 Python 连接串 |
| Python 依赖 | 已有 `aiosqlite 0.22.1` / `langgraph-checkpoint-sqlite 3.1.1` / **`sqlite-vec 0.1.9`**；**无** `psycopg` / `langgraph-checkpoint-postgres` | `uv pip list` | 换 PG 需联网装两个包（注意 FIX-18：代理节点偶发不稳） |
| `.env` / `.env.example` | **无任何 PG 配置项** | `grep -i pg .env.example` | 需新增 `DKD_AGENT_PG_DSN` 占位符（AGENTS §7.1：模板只写占位符） |
| agent_* 五张表 | 在 **MySQL `dkd` 库**，账号 `dkd_agent` 由 `docs/ddl/create_agent_db_user.sql` 授权 | 0-5 / FIX-8 | 迁 PG = 动 DDL 归档 + 授权脚本 + 3-7 数据通路 |
| 代码接入点 | `app/checkpoint.py` 的 `CheckpointStore`（生命周期封装 + `open()/close()`），`app/main.py` lifespan 装配 | 读代码 | 换后端是**受控改动**：新增一个同接口实现即可 |

> 结论：**PG 的「是否可用」已不是问题；需要评审的是「要不要纳入本期架构、纳到哪一层」。**

---

## 4. 三层拆解（为什么 ② 不建议本期搬）

### 4.1 ① checkpointer（建议换，方案 A）

- 现在：`AsyncSqliteSaver`，文件 `var/checkpoints.db`，单进程单例（`app/checkpoint.py`）。
- 换后：`AsyncPostgresSaver`（`langgraph-checkpoint-postgres` + `psycopg[binary,pool]`），`setup()` 幂等建表。
- 收益：① 多实例/跨机共享会话**开箱可解**（FIX-14 第一条触发条件）；② 与未来 pgvector **同实例**，等于把「将来要引入的东西」提前一次性引完；③ 备份/恢复纳入现有 PG 备份体系，比「复制 .db 文件」更规范。
- 代价：多一种 DB 的运维面（备份策略、账号、监控、值班窗口内变更）；测试不再 hermetic（见 §6）。
- **对照：升级 Redis（8.0+/Stack）能不能替代？** 能解决多实例，但**解决不了向量检索**，且本机 Redis 与 Java 共用 db0（`appendonly no`、`maxmemory` 未设）——`maxmemory` 与淘汰策略的调整会影响 Java 侧会话，属跨子系统变更。因此若目标是「一次到位」，**PG 优先于升级 Redis**。

### 4.2 ② agent_* 自有表（本期不搬）

| 关联点 | 搬 PG 后的影响 | 结论 |
| --- | --- | --- |
| 3-7 决策审计页 | RuoYi 标准列表页读 MySQL（Druid 数据源）；搬走后要么给 Java 加**第二个数据源**，要么改成「前端 → Agent API → PG」 | 两种都不便宜，且第二数据源会让「RuoYi 一站式运维」这条既有优势打折 |
| 两级授权模型 | 需要在 PG 侧重写一套（schema 隔离 + 只授 checkpoint/自有表、不授业务库） | 工作量不大，但**必须同步 `docs/ddl/` 与 FIX-9 的三处同步清单** |
| AGENTS §7.3 | 业务事实只读 MySQL 这条不变；但「Agent 自有表在哪」会变成两句口径 | 边界变复杂，收益却只有「会话数据换个库」 |
| 数据迁移 | conversation/message/decision_log 是**审计证据**（§6.3），迁移必须保真可回溯 | 迁移风险 > 收益 |

→ **建议：agent_* 表留在 MySQL**。若未来 3-7 改为走 Agent API，再重新评估。

### 4.3 ③ 知识库 / 向量检索（本期不引入）

- 复盘稿 §九已给出分阶段触发条件（≤1 千条规则检索 → 1 万~10 万 sqlite-vec 或 pgvector → >50 万 pgvector/Qdrant → 千万级 Milvus）。
- **本机已有 `sqlite-vec 0.1.9`**（随 SQLite checkpoint 依赖一并安装）：若 Phase 2 真要做小规模 RAG，**零新增基础设施**即可先跑通，把 PG 的引入时点继续往后推也是合理选项。
- 因此「为了知识库而换 PG」在本期不成立；**只有当 ① 决定要换（多实例）时，顺手为 ③ 留一个实例**才划算。

---

## 5. 方案对比与建议

| 方案 | 内容 | 成本 | 何时选 |
| --- | --- | --- | --- |
| **A（推荐）** | **只换 checkpointer**：新增 `DKD_AGENT_CHECKPOINT_BACKEND=sqlite\|postgres`（默认 `sqlite`，保持现状），PG 实现与 SQLite 实现同接口；**不碰 Java、不搬 agent_* 表** | 0.5~1 人日 | 年内确定多实例部署，或 Phase 2 真做 RAG（需要向量实例） |
| **B（现状）** | 维持 SQLite，把触发条件留在 runbook | 0 | 长期单机 + 不做向量检索 |
| **C（一步到位）** | A + 本机 PG 预留 `vector` 扩展 + 案例库表结构草稿（**不含** agent_* 表迁移） | 1.5~2 人日 | 若产品明确要在 Phase 2 做语义检索，提前把 DDL/授权/评测口径一起定下来 |

> 三者**互斥**，不要在没定 A/B 之前做 C 的一部分（会同时踩「测试不 hermetic」和「白引基础设施」两个坑）。

### 方案 A 的落地清单（若评审通过）

1. **代码**：`app/checkpoint.py` 抽象为「工厂 + 两个实现」（`SqliteCheckpointStore` / `PostgresCheckpointStore`），`app/main.py` 只依赖工厂；**改一处装配点**。
2. **配置**：`app/config.py` 新增 `checkpoint_backend`、`pg_dsn`（`DKD_AGENT_PG_DSN`）；`.env.example` 加占位符；**真实 DSN/口令只入 `.env`**（§7.1）。
3. **依赖**：`pyproject.toml` + `uv.lock` 增 `langgraph-checkpoint-postgres` + `psycopg[binary,pool]`（需联网，FIX-18 代理不稳时先切节点）。
4. **授权**：新增 `docs/ddl/create_agent_pg_role.sql`（对位 `create_agent_db_user.sql`）：建独立 database/schema，只授 checkpoint 表读写；**明确不授业务数据**；附回滚语句。
5. **存档澄清（§3.1 红线）**：在 `app/db.py` 与 README 写明——**PG 只承载 checkpoint，不是业务数据通道**，业务读取仍走 MySQL 只读账号 + 白名单。
6. **测试**：见 §6（这是本方案的**必答项**，不是可选项）。
7. **回滚**：`DKD_AGENT_CHECKPOINT_BACKEND=sqlite` 一行切回，SQLite 文件保留；**不做双向数据同步**（见 §7 D4）。
8. **文档**（§4.3 同步义务）：方案 §5.1 表格、排期 0-10 / FIX-14 / 质量门禁表、AGENTS §1 约束 5、runbook（备份/迁移/凭据轮换）。

---

## 6. 测试策略的取舍（评审必答）

现状：`tests/test_checkpoint.py` 用 `tmp_path` 真 SQLite 文件覆盖「跨请求恢复 / 重启后可读回 / 断点续传」，**不连外部依赖**。换 PG 后有三条路：

| 选项 | 做法 | 优点 | 代价 | 建议 |
| --- | --- | --- | --- | --- |
| T1 | PG 路径标 `-m live`（人工触发），默认门禁只跑 SQLite 路径 | 保持默认门禁 hermetic（§8 现状不破） | PG 路径的回归依赖人手动跑 | ✅ **推荐**（与 `test_*_live.py` 既有做法一致） |
| T2 | 测试用例起 PG 容器 | 真 hermetic 且覆盖完整 | 本机 Docker 守护进程当前未运行；Windows + CI 都要额外准备 | 🔸 若 CI 最终在 Linux 上做，可回看此选项 |
| T3 | 用假 saver 注入做**契约测试**（断言工厂/装配/生命周期/失败降级） | 零依赖，能覆盖「配置错误即失败」等拒绝路径 | 不验证真实 SQL 语义 | ✅ **推荐与 T1 并用** |

> 结论建议：**T1 + T3**。真库冒烟按 0-10 既有习惯写一个 `docs/scripts/verify-*.py`（真 PG 跑一次 setup 与中断恢复），把结果记入排期证据栏。

---

## 7. 待评审决策点（未决项按「建议默认值」先行，对齐 0-1 的处置方式）

| # | 决策点 | 建议默认值 | 需要谁拍板 |
| --- | --- | --- | --- |
| **D1** | 本期是否引入 PostgreSQL | 若「年内多实例部署」或「Phase 2 真做语义检索」成立 → **引入（方案 A）**；否则**维持 B** | 项目负责人 |
| **D2** | 范围是否只限 checkpointer（不搬 agent_* 表） | **是**（理由见 §4.2） | 项目负责人 + Java |
| **D3** | PG 路径测试方式 | **T1 + T3**（`-m live` + 假 saver 契约测试） | Python |
| **D4** | 存量 SQLite checkpoint 是否迁移 | **不迁移**：切换后旧会话不可恢复（会话价值低、非审计数据），新旧双跑观察 1~2 周再停 SQLite 写入 | 项目负责人 + 产品 |
| **D5** | 环境与凭据 | 需提供：① 本机 PG 账号（或授权新建专用角色/库）；② **生产是否允许新增 DB 引擎**、是否已有 PG 实例 | 运维 / 项目负责人 |

> **D5 未闭环前，方案 A 不得进入编码**（否则会写出只能在本机跑的实现）。

---

## 8. 风险与未确认项（不隐瞒）

1. **运维面新增**：2~3 人团队要同时维护 MySQL + Redis + PG 三套（备份/监控/权限/值班）；D1 的成本核算必须把这部分算进去，而不是只算代码改造。
2. **团队 PG 经验缺口**：本机**有** PG，但团队**没有**在本项目里用过——"能装"不等于"能运维"（备份策略、`max_connections`、连接池与 PG 侧 idle 超时的配合）。
3. **代理不稳**：FIX-18 记录过 `github.com` 偶发不可达；装 `psycopg` 走 PyPI 同样受影响。
4. **一条没验证的假设**：`AsyncPostgresSaver.setup()` 需要建表权限；若 PG 侧账号只给 DML，**首次部署会直接失败**——需在验收脚本里显式覆盖这条拒绝路径。
5. **不做的事**：本稿不主张把 `agent_*` 表、业务表或审计页数据迁到 PG；也不主张现在引入向量库。

---

## 9. 若评审通过：验收方式（可执行）

1. `DKD_AGENT_CHECKPOINT_BACKEND=postgres` 启动 → `/health` 正常，PG 中 `checkpoints` 等表由 `setup()` 幂等创建（重复启动无报错）。
2. 真 PG 上跑一次「中断 → 进程重启 → 恢复」：复用 `docs/scripts/verify-1-6-restock-plan.py` 的双进程思路，新增 `verify-checkpoint-pg.py`，输出 `中断=是 / 恢复后 status=2` 类证据。
3. 门禁：`ruff check` / `ruff format --check` 全过；`pytest` 默认门禁仍全绿（SQLite 路径 + 契约测试），`-m live` 跑 PG 真库。
4. 回滚演练：切回 `sqlite` 启动一次，会话与 1-6/1-7 链路行为不变。
5. 文档三处同步（方案 / 排期 / AGENTS §1 约束 5）并互相引用版本号。

---

## 10. 变更记录

| 日期 | 版本 | 变更 | 作者 |
| --- | --- | --- | --- |
| 2026-09-23 | V0.1 | 首版（待评审）：三层拆解、A/B/C 方案、D1~D5 决策点、测试策略取舍、实测事实表 | 开发（AI 协作） |

---

*本稿为**评审输入**，不是结论。评审通过后：① 本文件置为「已定稿」并标注日期/结论；② 按 AGENTS §4.3 同步改版方案与排期（互相引用版本号）；③ 排期 §八 FIX-14 与本文件状态一并更新。*
