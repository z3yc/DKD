# 智能体服务 · 运维手册与 Runbook（排期任务 4-1）

> 版本：V1.0（2026-10-01）
> 适用：`dkd-agent`（Python 智能体服务）+ `dkd-parent` 里的 Java 网关/回调
> 相关：`docs/DKD智能体接入方案-LangChain-LangGraph.md` V1.3、`docs/DKD智能体接入项目排期计划.md` V2.10、
> `docs/ddl/create_agent_pg_role.sql`、`deploy/docker/README.md`、`docs/security-credential-rotation.md`
>
> **本手册的每一条都来自实测**（不是照着“标准运维手册”抄的）。
> 凡未验证的，都明确写了「未验证」。

---

## 一、启动与停止

### 1.1 启动（⚠️ 命令不能随意换）

```bash
cd dkd-agent
set -a; source ../.env; set +a          # 环境变量（Spring/Python 都不读 .env，必须显式注入）
uv run python -m app                     # ✅ 推荐入口
```

**为什么不能用 `uvicorn app.main:app`（默认参数）**：
uvicorn **0.36+** 改用 `loop_factory` **直接指定**事件循环，**不再读 asyncio 全局策略**；
它在 Windows 上的默认工厂是 `ProactorEventLoop`，而 psycopg 异步不支持 Proactor。
现象是启动失败并抛 `psycopg.InterfaceError: cannot use the 'ProactorEventLoop'`
——报错指向 psycopg，与真因（uvicorn 机制变更）相距很远（排期 FIX-20）。

- 想直接用 uvicorn：`uv run uvicorn app.main:app --loop none`
- ⚠️ **`--reload` 下 PG 后端不可用**：uvicorn 的 reloader 会强制 Proactor，此时只能用 SQLite 后端或去掉 reload。

### 1.2 停止

```bash
# 看谁占着端口（Windows）
netstat -ano | findstr :8090
taskkill /PID <pid> /F
```

⚠️ **别用 `pkill -f "python -m app"` 就以为停干净了**：`uv run` 包了一层，实际监听进程的命令行
不匹配该模式。实测踩过：以为停了、其实老进程还占着 8090，新进程绑定失败（日志里是
`[Errno 10048] error while attempting to bind`），**而你访问到的一直是旧代码**。
判断依据用 `netstat -ano | findstr :8090` 拿到真实 PID。

---

## 二、健康检查与状态判定

```bash
curl -s http://127.0.0.1:8090/health
```

| 字段 | 含义 | 排障读法 |
| --- | --- | --- |
| `checkpoint_backend` | **实际装配**的会话存储后端 | 报的是实际值而不是配置值——配置 `postgres` 但装配回退 sqlite 时，只报配置值的 health 会说“一切正常” |
| `checkpoint_location` | 存储位置（**已脱敏**，PG 口令替换为 `***`） | 用于确认连的是哪个库 |
| `checkpoint_open` | 存储**是否真的挂着** | **单独的绿灯字段**：进程关闭后模块级单例仍在，只看后端名会得到误导排障的假绿灯 |
| `db_configured` | 业务库（MySQL 只读账号）口令是否配置 | false → 读工具全部不可用 |
| `service_secret_configured` | 服务间密钥是否配置 | false → 回调/运维接口返回 503 |
| `llm_configured` | LLM 密钥是否配置 | false → 校准降级为统计基线（可预期行为） |

网关侧就绪判定（前端据此显示/禁用入口）：

```bash
curl -s -H "Authorization: Bearer <JWT>" http://127.0.0.1:8080/agent/status
# enabled=false        → agent.enabled 开关关闭（一键降级），或未认证
# upstream=down        → Python 服务不可达（网络/进程/端口）
```

### 巡检清单（建议每日）

1. `GET /health` → `status=ok`、`checkpoint_open=true`、`db_configured=true`
2. `GET /agent/status` → `enabled=true`、`upstream=up`
3. 06:00 定时任务是否成功：查 `sys_job_log`（`agentTask.restockAnalyze`）；
   **注意**「开关关闭/被暂停**不算失败**」（AGENTS §6.4 四条约定），不要按失败告警处置
4. 留痕是否在增长：`select count(*) from agent_decision_log where create_time >= curdate();`
5. **重复活跃工单巡检**（FIX-1 的监控项，正常应为 0 行）：
   ```sql
   select inner_code, product_type_id, count(*) c from tb_task
    where task_status in (1,2) group by inner_code, product_type_id having c > 1;
   ```
   ⚠️ 本机开发库**当前有 1 组（4 张）**，是历史缺陷产物，待业务确认清理口径（见 FIX-1）。
6. **不可归因订单占比**（FIX-21 的数据质量项）：
   ```sql
   select count(*) total, sum(channel_code is null) unattributed from tb_order where status = 2;
   ```
   本机实测 29%（2/7）——偏高应反馈业务（下单未回填货道）。

---

## 三、会话存储的备份与恢复

### 3.1 PostgreSQL 后端（当前本机已启用）

**备份**（纳入现有 PG 备份体系即可）：

```bash
pg_dump -h 127.0.0.1 -U dkd_agent_pg -d dkd_agent_state -Fc -f checkpoint-$(date +%F).dump
# 只备份会话表时：加 -t checkpoints -t checkpoint_writes -t checkpoint_blobs -t checkpoint_migrations
```

**恢复**（全新实例）：建库建角色 → `pg_restore -d dkd_agent_state <dump>` → 启动服务
（`setup()` 幂等，缺表会自动补）。

**恢复后自检**：`GET /health` → `checkpoint_backend=postgres`、`checkpoint_open=true`；
再发一轮对话确认可正常返回。

> ⚠️ 参考：`AsyncConnectionPool.setup()` 需**建表权限**，所以授权走「角色拥有独立库」方案
> （评审稿 D5 / §8-4；方案 2「DBA 预建表 + 只授 DML」已实测**不可行**）。

### 3.2 SQLite 后端（回滚路径 / 未启用 PG 的环境）

```bash
# 热备份（推荐：不中断服务）
sqlite3 var/checkpoints.db ".backup out.db"
# 冷备份：停止写入后直接复制 var/checkpoints.db
```

数据文件路径由 `DKD_AGENT_SQLITE_PATH` 决定（默认 `var/checkpoints.db`）。

### 3.3 切换与回滚（**一行环境变量**）

```bash
# 切到 PG：需先 uv sync --extra postgres，并配置 DKD_AGENT_PG_DSN
DKD_AGENT_CHECKPOINT_BACKEND=postgres

# 回滚到 SQLite：
DKD_AGENT_CHECKPOINT_BACKEND=sqlite
```

**不做双向数据同步**（评审稿 D4 结论）：切换后旧会话不可恢复（会话价值低、非审计数据）。
SQLite 文件会一直保留，回滚即可用。

---

## 四、故障处置

| 现象 | 真因（按可能性排序） | 处置 |
| --- | --- | --- |
| 启动即失败：`psycopg.InterfaceError ... ProactorEventLoop` | 用错启动命令（见 §1.1） | 改 `uv run python -m app` 或加 `--loop none` |
| 启动即失败：`拒绝以错误配置启动...未配置 DKD_AGENT_PG_DSN` | 后端选了 postgres 但没给 DSN | 配置 DSN 或把后端改回 sqlite（这是**刻意的 fail-fast**，不会静默回退） |
| 启动即失败：`DKD_AGENT_CHECKPOINT_BACKEND 只能是 sqlite 或 postgres` | 值拼错（如 `postgresql`） | 改正。**不回退是为了不让“看起来成功了”的错配发生** |
| 对话报「会话存储暂时不可用」+ 503 | PG 不可达/连接池拿不到连接 | 查 PG 服务与网络；**池会自动重建连接，通常无需重启**（FIX-23 修完后的行为） |
| 对话报「智能体服务内部错误」+ 500 | 其它基础设施异常 | 按响应里的**请求号**去服务日志检索（`chat aborted by infra error rid=...`） |
| 运营说「点了确认没反应」 | 建单失败但状态未回滚（设计如此） | 看 502 的 `msg`：**原样再点一次即可重试**（FIX-15 已把这条写进接口契约）；若建单失败请先刷新计划状态再决定重试 |
| 定时任务失败：`Agent 分析失败（已重试 N 次）` | Python 侧 5xx / 不可达 | 4xx **不重试**（密钥/路径/参数类错误重试不会变好）；5xx 按配置重试；最终失败已写入 `sys_job_log` |
| Agent 建单全部失败，且日志里有「服务器限流异常」 | **Redis 不可用**（回调经过 Redis 限流计数，fail-closed） | 先恢复 Redis（实测：恢复后自动可用，无需重启 Java）。⚠️ 见下方“已知诊断性缺口” |
| 审计页看不到新留痕 | 时区/筛选条件；或 Agent 侧留痕被降级（审计库不可用时只 WARN 不阻断业务） | 查 `agent_decision_log`；审计降级属预期（旁路能力不拖垮主链路） |

### 4.1 已知诊断性缺口（FIX-16 演练产出，务必知道）

**Redis 抖动时，运营看到的文案有两条路，不一定指向 Redis**：

- **快路**（Java 立即失败）：`服务器限流异常，请稍候再试` → **能直接定位**；
- **慢路**（Java 等 Redis 连接超时 > 10s，Agent 的 10s 回调超时先到）：
  Java 的精确原因被丢弃，运营看到的是我们兜底后的文案
  （“…未确认结果：请先刷新该计划状态再决定是否重试…可能原因包括 Java 或 Redis 不可用，建议一并检查”）。

两条路现在都**可排查**了（超时文案已含 Redis 线索），但**根因未消除**：
Java 侧在 Redis 不可用时失败得比 Agent 的超时更慢。**上线期观测项**：把 Redis 可用性纳入告警。

### 4.2 生产上必炸、开发机上撞不到的两类问题（已修，留作排查直觉）

1. **PG 单连接 → 一次抖动永久失效**（FIX-23）：`from_conn_string` 内部是单个连接，
   断一次后续全部失败，**只有重启进程能恢复**。现改为连接池（`check=check_connection`），
   中断后自动恢复（演练 4/4）。
2. **基础设施异常抛穿 SSE**（FIX-24）：客户端以前只拿到断流（`httpx.ReadError`），
   现在会收到带请求号的 error 帧（存储类 503 / 其它 500）。

排障直觉：**“一次性故障”如果表现为“此后一直失败”，先怀疑单连接/连接池**；
**“请求没有响应体”如果服务本身健康，先怀疑生成器里抛穿了异常**。

---

## 五、一键降级（AGENTS §1 约束 3）

```bash
DKD_AGENT_ENABLED=false   # 网关层：/agent/status 报 enabled=false；对话返回 503 信封 + 降级帧
```

- 降级后：前端禁用对话入口并显示提示；**降级不等于免鉴权**（未认证仍是 `HTTP 200 + code:401`）。
- LLM 不可用时（密钥失效/超时）：补货校准**降级为统计基线**并写 `issues`，
  **不写 `failures`**（校准失败 ≠ 业务失败，避免运营误判）；实测用无效密钥拿到真 401 后行为一致。
- ⚠️ 降级开关只影响**网关转发**，已排期的 06:00 定时任务由 Java 侧开关
  （`agent_restock_pause` / `agent.enabled`）控制，两者要**一起**考虑。

---

## 六、部署要点（容器/新环境）

1. **环境变量必须注入**：Spring 与 Python 都**不读 `.env`**，用 `setx`/systemd/docker env。
   密钥只入部署环境，模板文件写占位符（AGENTS §7.1）。
2. **PG 账号 host 限制**：`create_agent_db_user.sql`（MySQL）只授 `@'127.0.0.1'`/`@'localhost'`，
   容器里 agent 的 127.0.0.1 是它自己 → **不改 host 模式会直接 1045**（V2.7 记录的容器化障碍）。
3. **PG 侧同理**：`create_agent_pg_role.sql` 第 3 步做了 `REVOKE ALL ON DATABASE ... FROM PUBLIC`，
   因此**任何新增使用者都要显式 `GRANT CONNECT`**。
4. **业务表 DDL 未入库**：自动初始化只能建出 RuoYi 系统表 + `agent_*`，业务表必须导入现网 dump
   （见 `deploy/docker/scripts/import-business-dump.sh`）。这是新环境的**硬阻断**级前置。
5. **uv 可选依赖**：PG 后端需 `uv sync --extra postgres`（默认 sqlite 不需要，避免无谓扩大依赖面）。

---

## 七、本手册的执行证据（可复跑的验证脚本）

| 脚本 | 覆盖 | 最近结果 |
| --- | --- | --- |
| `docs/scripts/verify-checkpoint-pg.py` | PG 后端：配置/脱敏/生命周期/setup 幂等/5 项越权拒绝/跨进程恢复/DML 探针 | 18/18 + 3/3 |
| `docs/scripts/verify-3-9-drills.py` | 对话并发（8 会话无串话，**断言按 `meta.mode` 分支**）/ PG 连接中断恢复 / Python 宕机降级 / **SQLite 文件损坏**（7/7）/ **MySQL 慢查询护栏**（8/8） | 4/4（llm，echo 为 5/5）+ 4/4 + 2/2 + **7/7** + **8/8** |
| `docs/scripts/verify-fix16-redis-failmode.py` | Redis fail-closed 的三相位形态 | 基 2/2 + 故 7/7 + 恢 2/2 |
| `docs/scripts/verify-fix17-llm-degrade.py` | 密钥失效降级（真 401）/ 真 LLM 校准 live | 6/6 + 7/7 |
| `docs/scripts/verify-1-12-reconcile.py` | Agent 聚合口径 vs Java 报表口径 | 8/8 |
| `docs/scripts/restock-workbench-browser-check.cjs` | 补货工作台真浏览器 | 29/29 |
| `docs/scripts/agent-audit-browser-check.cjs` | 决策审计页真浏览器 | 14/14 |
| `docs/scripts/sample-2-7-case-library.py` | 知识库数据质量门槛（**当前不达标**，降级规则库） | 4 项不过 |
| `docs/scripts/verify-1-13-m2-acceptance.py` | M2「≥10 台真机全闭环」一键验收 + 报告（**默认只读**） | 只读 10/10；`--confirm-write` 15/15（已回退） |
| `docs/scripts/compare-1-14-rule-vs-baseline.py` | 规则版 vs 基线引擎建议量双跑对比 + 3-8 三项指标（只读） | 口径验证通过（n=2，偏差根因为 FIX-5） |
| `docs/scripts/verify-assistant-tools.py` | 助手只读工具真库取数 + 端到端 + **不写护栏** | 16/16 |
| `docs/scripts/verify-assistant-diagnosis.py` | 诊断场景（scene=3）：工具直连 **17 项**（含 2-4 结构化结论）+ 真 LLM 端到端 + 不写护栏 | 工具直连 **17/17**；端到端**当日 BLOCKED**（见下「配额」） |
| `docs/scripts/assistant-diagnosis-browser-check.cjs` | 诊断场景**浏览器**验收：切场景清会话、欢迎语不越权、快捷问题可答、异常问答带真值、建单被拒 + **数库护栅** | 15/15 |

### 6.3 退出码约定（2026-10-01 新增，务必区分“未验到”）

| 退出码 | 含义 | 怎么处理 |
| --- | --- | --- |
| 0 | 全部通过 | — |
| 1 | 有断言 FAIL | 当缺陷处理 |
| **2** | **有项“未验到”（BLOCKED）** | **不是通过也不是失败**：被环境/配额拦住，需换个条件复跑 |

`verify-assistant-diagnosis.py` 已按此实现：命中 **当日 token 配额熔断**（`code=429` / “token 已达上限”）时
报 `BLOCKED` 而不是一堆 `FAIL`，并打印复跑方式。**为什么必须分开**：本项目的每次验收都会真调 LLM，
一天跑几轮就会把每日配额（默认 20 万 token/用户）跑满——若把“护栏拦住了 LLM”报成“能力失败”，
几天之后团队就会开始用“调高配额”来让报告变绿，那正好毁掉这条成本护栏。

### 7.0 一条来自实际踩坑的断言纪律（2026-10-01）

**否证类断言不要用 `substring`**：想验证“助手没有声称已建单”，写成 `!reply.includes('已建单')`
会被**否定语境里的引用**击穿——实测模型会写「我不能代建，也不能说“已为你创建”」，
断言因此误报失败（同一脚本连栽两次）。可靠做法二选一：
① 直接把判据换成**事实**（数库：工单总数是否增加——见下面的写护栏）；
② 断言必须命中“语义标记集合”，并接受同义改写（措辞表要能扩，见 `verify-assistant-diagnosis.py` 的注释）。

### 7.1 会写数据的脚本：必须有的两道护栏（新增纪律，2026-10-01）

`verify-1-3-build-task.py`、`verify-1-13-m2-acceptance.py --confirm-write`、
`restock-workbench-browser-check.cjs` 这类脚本**会真的建单**。就此定两条硬规矩（来自三次真实事故）：

1. **写入护栏**：跑前/跑后对比“活跃补货工单数”，**新增数必须精确等于成功建单数**，否则判 FAIL 并列出差额；
2. **护栏的读数必须跨事务**：脚本的 `AsyncSession` 若从很早就开着事务，InnoDB REPEATABLE READ 会让它
   **读不到服务端另一条连接刚写的值**——实测出现过“新增 0 == 成功 0”静默通过、而库里真多了一张工单。
   修法：跨连接读数前先结束当前读事务（`await session.commit()`）或另开 session（见脚本里的 `refresh()`）。

> **可读出写前快照的护栏，比没有护栏更危险**——因为团队会相信它。

### 7.2 何时不要跑这些脚本

- `verify-3-9-drills.py --phase python-down` 要求 **Python 已停**；`--phase concurrent` 要求它**在跑**；
  两相都自带前置自检，不满足会拒跑（这是 FIX-16 误写事故留下的护栏）；
- 数据库维护窗口、生产环境、以及任何“只想看看”的场合：**不要**加 `--confirm-write`。

> ⚠️ 这些脚本会**读写真实环境**（部分会写业务数据，如 `verify-1-3-build-task.py` 真建单）。
> 人工显式执行、执行后按脚本提示清理；**不要塞进 CI**（AGENTS §8）。
