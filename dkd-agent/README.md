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
| `DKD_AGENT_DB_USER` / `_PASSWORD` | MySQL **只读账号** | 默认 `dkd_agent_ro`，禁止使用 root |
| `DKD_AGENT_SQLITE_PATH` | 会话 checkpoint 文件 | 默认 `var/checkpoints.db`，**需纳入备份**；备份/恢复见 `app/checkpoint.py` docstring |
| `DKD_AGENT_USE_LLM` | 是否真实调用 LLM | `0`=echo 链路（默认）；`1` 但无密钥则**启动即失败** |
| `DKD_AGENT_AUDIT_ENABLED` | 留痕/计量开关 | `0` 便于本地不连库调试 |
| `DKD_AGENT_TOKEN_LIMIT_PER_USER` / `_GLOBAL` | 日 token 限额 | `0`=不限；超限返回 **429**（防成本失控） |

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
| `DKD_AGENT_TABLE_WHITELIST` | 只读表白名单 | 表名带 `tb_` 前缀 |

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
