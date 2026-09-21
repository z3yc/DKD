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

## 环境变量

除复用 Java 侧的 `DKD_DEEPSEEK_API_KEY` 外，其余为服务自身变量：

| 变量 | 用途 | 说明 |
| --- | --- | --- |
| `DKD_AGENT_ENV` | 运行环境 | dev / test / prod |
| `DKD_AGENT_SERVICE_SECRET` | 服务间密钥 | 与 Java `AgentProperties.secret` 对应；未配置则回调接口返回 503 |
| `DKD_AGENT_DB_USER` / `_PASSWORD` | MySQL **只读账号** | 默认 `dkd_agent_ro`，禁止使用 root |
| `DKD_AGENT_SQLITE_PATH` | 会话 checkpoint 文件 | 默认 `var/checkpoints.db`，**需纳入备份** |
| `DKD_AGENT_TABLE_WHITELIST` | 只读表白名单 | 表名带 `tb_` 前缀 |

## 当前进度（Phase 0）

| 任务 | 状态 |
| --- | --- |
| 0-2 环境与依赖锁定 | ✅ |
| 0-3 LLM 连通 | ⛔ 阻塞：旧 DeepSeek Key 已失效（HTTP 401），需新 Key |
| 0-5 `agent_*` 四表 DDL | ✅ 已在本地 dkd 库执行通过（幂等重跑 + 唯一键拒绝路径已验证） |
| 0-9 服务骨架（/health + SSE echo + 鉴权 + request_id） | ✅ |
| 0-10 SQLite checkpointer 接入 | 待办 |
