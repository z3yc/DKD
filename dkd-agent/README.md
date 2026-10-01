# dkd-agent · DKD 智能体服务

Python 3.11 / FastAPI / LangGraph 的旁路智能体服务。
**硬约束**：读走 MySQL 只读账号（表白名单），写操作一律回调 Java REST（`AgentCallbackController`），
本服务**永不直连数据库写入**（见 `AGENTS.md` §1 约束 1-2、§7.3）。

## 环境准备（任务 0-2）

使用 **uv** 管理 Python 与依赖（方案要求 3.11+，uv 已装 3.11.13，不污染系统 Python）：

```powershell
cd dkd-agent
uv sync                # 按 uv.lock 精确安装（CI/部署用 uv sync --frozen）
uv run pytest          # 全量测试
uv run ruff check .    # lint
uv run ruff format --check .
```

### ⚠️ 本机环境三个坑（已实测，换机器请先看这段）

**1. venv 里没有 `pip`** —— uv 创建的 venv 默认不装 pip。装包必须用 uv：

```bash
uv pip install <包>     # 装到当前 venv（不写 pyproject）
uv add <包>             # 需要写入 pyproject 时用这个（会更新 uv.lock）
```

直接敲 `pip` 会命中别处的旧 Python（见下条），报 `No module named 'pip'`。

**2. 本机 PATH 里有 4 个 Python**（`D:\python3.8`、用户目录 3.10、uv 托管 3.11、WindowsApps 占位），
裸用 `python` / `pip` 极易误中 3.8。请一律用 `uv run ...` 或 `.venv\Scripts\python.exe` 绝对路径。

**3. 激活虚拟环境**（可选；`uv run` 会自动使用它）：

| Shell | 命令 |
| --- | --- |
| PowerShell | `.\.venv\Scripts\Activate.ps1`（本机 CurrentUser 策略为 RemoteSigned，无需改策略） |
| cmd | `.venv\Scripts\activate.bat` |
| Git Bash | `source .venv/Scripts/activate` |

> 注意：`.env` 里的变量必须注入到进程环境才生效（Spring Boot / Python 都不会自动读 `.env`）。
> Git Bash 临时注入：`set -a; source ../.env; set +a`；永久（Windows）：`setx DKD_DEEPSEEK_API_KEY "..."`（需重开终端）。

版本锁定：`uv.lock` 固定 langgraph / langgraph-checkpoint / saver 等全部传递依赖的**精确版本**，
升级须走「兼容性评审 + 存量会话处理方案」（方案 V1.1 §5.2）。

本次解析出的实际版本（任务 0-2 证据，2026-09-21）：

| 包 | 锁定版本 | 备注 |
| --- | --- | --- |
| langgraph | 1.2.11 | 方案原写 `≥0.2`，实际落到 1.x——印证开放区间的漂移风险，必须以 lock 锁定 |
| langgraph-checkpoint | 4.2.0 | 与 langgraph 主版本强绑定 |
| langgraph-checkpoint-sqlite | 3.1.1 | 会话持久化（Redis 3.2 不可用的替代方案） |
| langchain-openai | 1.6.2 | LLM 接入（OpenAI 兼容 → DeepSeek） |
| fastapi / starlette | 0.141.1 / 1.6.0 | — |
| pydantic | 2.13.5 | 对外契约与结构化输出 |
| sqlalchemy / aiomysql / aiosqlite | 2.0.54 / 0.3.2 / 0.22.1 | MySQL 只读 + 本地 checkpoint |

## 运行

```bash
# 推荐入口（Windows + postgres 后端**必须**用它，理由见下）
uv run python -m app

# 直接用 uvicorn 时必须加 --loop none
uv run uvicorn app.main:app --loop none
```

> ⚠️ **别用 `uvicorn app.main:app`（不带 `--loop none`）跑 postgres 后端**：
> uvicorn **0.36+** 用 `loop_factory` 直接指定事件循环，**不再读 asyncio 全局策略**，
> 而它在 Windows 上的默认工厂是 `ProactorEventLoop`——psycopg 异步不支持，启动会失败，
> 且报错指向 psycopg 内部。`python -m app` 内部用 `loop="none"` 回到“按策略建循环”，
> 并在建循环前把策略设为 `SelectorEventLoop`（排期 FIX-20）。

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
# 健康检查
curl http://127.0.0.1:8090/health
# SSE 流式冒烟（echo 模式）
curl -N -H "X-Agent-User: 1" -H "Content-Type: application/json" \
     -d '{"message":"ping"}' http://127.0.0.1:8090/agent/chat
```

对外**只监听内网/回环**，前端一律经 Java 网关（`/agent/**`）访问；Python 不解析 JWT，
只信任网关注入的 `X-Agent-User / X-Agent-Roles / X-Agent-Region` 头。

## LLM 连通性验证（任务 0-3）

`app/llm.py` 用 `langchain-openai` 以 OpenAI 兼容模式指向 DeepSeek。
**单元测试全部打桩**（AGENTS §8：测试不得真实调用外部 API）；真实调用需人工显式触发：

```bash
set -a; source ../.env; set +a          # 注入 DKD_DEEPSEEK_API_KEY
DKD_AGENT_LIVE_LLM=1 uv run pytest -m live -v
```

首次实测结论（2026-09-21）：HTTP 200、内容返回正常、耗时 5.4s、token 9/1。

> ⚠️ **注意服务端模型名可能与配置不一致**：配置 `deepseek-chat` 时服务端返回的 `model` 为
> `deepseek-flash`。`ping_llm()` 会记录**实际返回的模型名**并在不一致时打 WARN——
> 成本计量与效果归因必须按实际模型，不能按配置假设。

## 环境变量

除复用 Java 侧的 `DKD_DEEPSEEK_API_KEY` 外，其余为服务自身变量：

| 变量 | 用途 | 说明 |
| --- | --- | --- |
| `DKD_AGENT_ENV` | 运行环境 | dev / test / prod |
| `DKD_AGENT_SERVICE_SECRET` | 服务间密钥 | 与 Java `AgentProperties.secret` 对应；未配置则回调接口返回 503 |
| `DKD_AGENT_DB_USER` / `_PASSWORD` | MySQL **只读账号** | 默认 `dkd_agent`（两级授权见 `docs/ddl/create_agent_db_user.sql`），禁止使用 root |
| `DKD_AGENT_CHECKPOINT_BACKEND` | 会话 checkpoint 后端 | `sqlite`（默认）/ `postgres`；**切换见下「会话存储后端」** |
| `DKD_AGENT_SQLITE_PATH` | 会话 checkpoint 文件 | 默认 `var/checkpoints.db`，**需纳入备份**；切到 postgres 后文件仍保留（回滚用） |
| `DKD_AGENT_PG_DSN` | PG 连接串（**仅 checkpoint**） | 仅 `BACKEND=postgres` 时必填；缺 DSN 则**启动即失败**（不静默回退）；口令只入 `.env` |
| `DKD_AGENT_USE_LLM` | 是否真实调用 LLM | `0`=echo 链路（默认）；`1` 但无密钥则**启动即失败** |
| `DKD_AGENT_AUDIT_ENABLED` | 留痕/计量开关 | `0` 便于本地不连库调试 |
| `DKD_AGENT_TOKEN_LIMIT_PER_USER` / `_GLOBAL` | 日 token 限额 | `0`=不限；超限返回 **429**（防成本失控） |
| `DKD_AGENT_TABLE_WHITELIST` | 只读表白名单 | 表名带 `tb_` 前缀 |
| `DKD_AGENT_RESTOCK_CALIBRATION_ENABLED` | 补货 LLM 校准开关 | `0`=纯统计基线（本地验收默认关闭，避免真实调用 LLM 产生费用） |

## 会话存储后端（sqlite / postgres，任务 0-10 + PG 选型评审 V1.0）

**默认是 `sqlite`**（单实例部署，零新增基础设施）。出现**多实例部署**或**需跨机共享会话**时切 postgres，
一步销掉 FIX-14（Redis 3.2.100）的两条触发条件。

```bash
# 启用 postgres 后端（D5 闭环后）
uv sync --extra postgres                 # 装 langgraph-checkpoint-postgres + psycopg
# .env 里设置（口令勿写入任何入仓文件）：
#   DKD_AGENT_CHECKPOINT_BACKEND=postgres
#   DKD_AGENT_PG_DSN=postgresql://dkd_agent_pg:<口令>@127.0.0.1:5432/dkd_agent_state

# 回滚：改一个变量即可，不做双向数据同步（评审稿 D4 结论）
#   DKD_AGENT_CHECKPOINT_BACKEND=sqlite
```

**为什么 PG 依赖做成可选组**：默认后端用不到它；放进主依赖会让镜像体积与依赖面无谓变大。
`app/checkpoint.py` 对 PG 驱动做**惰性导入**，缺失时只在真正选 postgres 时给出可执行的补救命令。

**红线（AGENTS §1 约束 5 / §7.3）**：PG **只承载 checkpoint**（及将来的向量索引），
**不是业务数据的第二通道**。业务读取仍走 MySQL 只读账号 + 白名单，写操作仍一律回调 Java；
`agent_*` 表**留在 MySQL**。授权脚本：`docs/ddl/create_agent_pg_role.sql`（只授自己那个库，明确不授业务库）。

**排查入口**：`GET /health` 会报 `checkpoint_backend` / `checkpoint_location`（已脱敏）/ `checkpoint_open`。
报的是**实际装配**的后端而不是配置值，且单独给出 `checkpoint_open` —— 避免“存储已在关闭后仍报绿灯”。

**本机已实际切到 postgres 并验收（2026-10-01）**：角色/库已建（`docs/scripts/setup-agent-pg.py`）、
`verify-checkpoint-pg.py` **18/18** + 跨进程恢复 **3/3** + DML 探针。回滚一行：把
`DKD_AGENT_CHECKPOINT_BACKEND` 改回 `sqlite`。

⚠️ **生产启用仍需 D5 的另一半拍板**：是否允许新增 DB 引擎（本机已闭环）。

⚠️ 另一条已接受的边界：库 owner 可以安装 **trusted 扩展**（实测 `pg_trgm` 成功）——
见 `docs/ddl/create_agent_pg_role.sql` 第 5.3 节。

## 留痕与成本计量（任务 0-12）

- 每轮对话写 `agent_decision_log`（含 `input_context`/`llm_output`/`result`/`cost_tokens`）；
  落库前**强制脱敏**（手机号、卡号/订单号）。
- `agent_message` 是成本报表的唯一数据源（`tokens_in`/`tokens_out`/`model`/`user_id`）；报表接口：

```bash
curl -H "X-Agent-Secret: $DKD_AGENT_SERVICE_SECRET" \
     "http://127.0.0.1:8090/agents/usage/summary?days=1"
```

- **降级原则**：审计/计量属旁路，数据库故障时**记录 WARN 后放行**，不中断用户对话；
  但限额熔断属业务规则，命中时明确返回 `429`（不静默）。

## 定时分析入口（任务 1-9）

06:00 的补货分析由 Java 的 dkd-quartz 触发本接口（任务注册见 `docs/ddl/agent_restock_job.sql`）：

```bash
curl -X POST -H "X-Agent-Secret: $DKD_AGENT_SERVICE_SECRET" \
     -H "X-Request-Id: restock-job-manual01" -H "Content-Type: application/json" \
     -d '{"planDate":"2026-09-28"}' \
     http://127.0.0.1:8090/agents/restock/analyze
```

| 返回 `data.status` | 含义 | Java 侧反应 |
| --- | --- | --- |
| `analyzed` | 已生成计划并落库（`deviceCount`/`channelCount`/`totalQuantity`），图停在人工确认处 | 任务成功 |
| `skipped` | `agent_restock_pause` 开关为暂停（**不是错误**） | 任务成功（不重试、不告警） |

- 路径用 `/agents/**`（服务间密钥）而不是 `/agent/restock/**`（用户 JWT 经网关）：鉴权体系不同，
  **不能混用**（AGENTS §7.5）；
- 失败返回 502（Python 自身故障，Java 侧重试后仍失败则由 `sys_job_log` 留下失败记录）；
- 同一日期重复触发安全（`thread_id = restock-<planDate>`，计划按 `(vm_id, plan_date)` 幂等 upsert）；
- 每次触发都写 `agent_decision_log`（`scene=2`、`trigger_type=2`、`user_id=NULL`、`llm_output=NULL`）。

端到端验收：`python ../docs/scripts/verify-1-9-analyze.py`（真 MySQL + 真实 FastAPI，20 项断言）。

## 当前进度（Phase 0）

| 任务 | 状态 |
| --- | --- |
| 0-2 环境与依赖锁定 | ✅ `uv.lock` 已锁定（langgraph 1.2.11 / checkpoint 4.2.0 / sqlite 3.1.1） |
| 0-3 LLM 连通 | ✅ 代码 + 打桩单测 + 可选 live 冒烟；实测 HTTP 200（5.4s） |
| 0-5 `agent_*` 四表 DDL | ✅ 已在本地 dkd 库执行通过（幂等重跑 + 唯一键拒绝路径已验证） |
| 0-9 服务骨架（/health + SSE echo + 鉴权 + request_id） | ✅ 真实进程冒烟通过 |
| 0-13 LangGraph 版本锁定复核 + state schema 冻结评审 | ✅ 评审稿 `docs/dkd-agent-restock-state-schema.md`；代码侧 `app/graphs/restock_state.py` 冻结 + 15 项契约测试 |
| 0-12 `request_id` 贯穿 + 决策留痕 + 成本计量与限额 | ✅ 全链：留痕/消息投影实写 MySQL（含脱敏）、限额熔断 429、`/agents/usage/summary` 报表；审计库故障降级不中断对话 |
| 0-10 SQLite checkpointer 接入 | ✅ 跨请求恢复、进程重启后仍可读回、`Last-Event-ID` 断点续传均已验证 |
| 0-4 只读账号 + 白名单 | ✅ `dkd_agent` 两级授权（业务表只读 + agent_* 可写无 DELETE）；8 项拒绝路径实测均被 MySQL 拒绝 |

## 当前进度（Phase 1 · 补货 Agent）

| 任务 | 状态 |
| --- | --- |
| 1-3 工单创建回调 | ✅ 唯一写通道 + 失败三分类 + 零重试；真库真回调 taskId=573 |
| 1-4 统计基线引擎 | ✅ 纯函数 + 三条边界 + 200 次随机序列不变量；回测留档（±20% 验收受 FIX-6 数据缺失阻塞） |
| 1-5 LLM 校准节点 | ✅ 系数化 + 三层不可信输入防护 + 失败降级（真实 live 链路待 FIX-11） |
| 1-6 中断确认子图 + 状态机 | ✅ 跨进程恢复、终态不可复活、幂等 upsert |
| 1-7 人工干预 API | ✅ 调整（可重算）/跳过/恢复/暂停，写操作强制网关身份 |
| 1-7b 干预决策留痕 | ✅ 六类动作 × 四条终态路径全部留痕，`llm_output` 恒为 NULL |
| 1-8 接单人分配与并发防护 | 🟡 主体实现 + CAS 乐观锁；10 台跨区域真机验收待环境（FIX-6） |
| 1-9 06:00 定时分析 | ✅ `POST /agents/restock/analyze` 消费暂停开关 + Java `agentTask.restockAnalyze` 重试/告警 + 注册 SQL；端到端 20/20 |
