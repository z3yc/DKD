# DKD 平台 · Docker 部署手册（V0.1）

> 目标：一台 Linux 服务器上，用一条命令把「管理后台 + 智能体 + 管理前端（+ 可选运维移动端）」跑起来。
> 依据：`AGENTS.md`（§1 基础设施边界、§7 安全红线）、方案 V1.1 §5.1、排期 V2.6。
> **当前状态：脚本已交付但未在真实服务器上跑过**（本机 Docker 守护进程不可用，见 §9「未验证项」）。

---

## 1. 交付物清单（谁负责什么）

```
deploy/docker/
├─ docker-compose.yml            # 应用栈：dkd-admin / dkd-agent / web（+ mobile profile: dkd-app）
├─ docker-compose.infra.yml      # 自建基础设施：MySQL 8 / Redis 7（用外部库时不带本文件）
├─ .env.deploy.example           # 全部环境变量的**模板**（占位符，无真值）
├─ deploy.sh                     # 一键：自检 → 构建 → 起服务 → 授权 → 健康检查
├─ initdb/                       # MySQL 首次初始化（数据卷为空时自动执行）
│   ├─ 01-ruoyi-system.sh        #   RuoYi 系统表 + quartz + tb_report
│   └─ 02-agent-tables.sh        #   agent_* 自有表（5 张）
├─ nginx/default.conf            # 前端静态站 + /prod-api 反代（**含 SSE 不缓冲**）
└─ scripts/
    ├─ _env.sh                   # 公共库（解析 .env.deploy、compose 参数、断言输出）
    ├─ setup-agent-db.sh         # 幂等建 dkd_agent 最小权限账号 + 否决路径验证
    ├─ import-business-dump.sh   # 导入现网业务库 dump（**必做，见 §4**）
    ├─ healthcheck.sh            # 部署后检查（含 SSE 是否被缓冲）
    └─ backup.sh                 # 备份业务库 + 智能体会话检查点
```

镜像构建文件放在各应用目录（构建上下文必须是该目录，否则 Maven/npm 找不到源码）：

| 文件 | 上下文 | 说明 |
| --- | --- | --- |
| `dkd-parent/Dockerfile` | `dkd-parent/` | 多阶段 Maven 构建 → JRE8 运行；`-pl dkd-admin -am` 需要父 POM 与全部模块在场 |
| `dkd-app/Dockerfile` | `dkd-app/` | 同上，端口 9070 |
| `dkd-vue/Dockerfile` | `dkd-vue/` | node 构建静态站 → nginx 托管 |
| `dkd-agent/Dockerfile` | `dkd-agent/` | uv 官方镜像 + `uv sync --frozen`（版本按 `uv.lock` 锁死） |

**两个设计决定值得先知道**（都是被实际报错逼出来的）：

1. **基础设施单独一个 compose 文件**：单机部署需要 MySQL/Redis 容器，但客户也可能用外部托管库。
   应用栈文件里如果写 `depends_on: mysql`，外部库模式下会直接报 `depends on undefined service`。
   实测本机 compose **不支持** `required: false`（Compose 2.20+ 的特性），因此把跨文件依赖写在
   `docker-compose.infra.yml` 的服务段里（compose 会把两份定义合并到同一服务）——
   带 infra 文件才有依赖，不带也能起。`deploy.sh` 无论哪种模式都会先等基础设施 healthy 再起应用。
2. **必须显式 `--env-file .env.deploy`**：compose 默认只读项目目录下的 `.env`（即 `deploy/docker/.env`），
   我们不用那个文件。所有脚本都封装了这一点；手工执行 docker compose 时请自己加 `--env-file`。

---

## 2. 服务器前置条件

| 项 | 要求 | 为什么 |
| --- | --- | --- |
| Docker | 24+ 与 `compose` v2 插件 | 用到 BuildKit 缓存挂载与 `depends_on: required: false` |
| 资源 | 建议 ≥2C4G，磁盘 ≥20G | 4 个 Java/Python/Node 镜像 + Maven 依赖缓存 |
| 端口 | `DKD_HTTP_PORT`(80)、可选 `DKD_APP_PORT`(9070) | 其余端口只绑回环（MySQL 3306、后台 18080） |
| 出网 | 构建期需访问 Maven/npm/PyPI（建议走国内镜像） | 三处镜像地址在 `.env.deploy` 里可配 |
| DNS/证书 | 本文档**不含 HTTPS**（见 §9） | 生产上线前需补 nginx TLS 或前置 SLB |

---

## 3. 首次部署（5 步）

```bash
git clone <repo> /opt/dkd && cd /opt/dkd/deploy/docker

# ① 准备环境变量（唯一需要手写文件的一步）
cp .env.deploy.example .env.deploy
chmod 600 .env.deploy
vi .env.deploy        # 逐项替换 CHANGE_ME；DKD_JWT_SECRET 用强随机（openssl rand -hex 32）

# ② 构建（首次慢：Maven/npm/pip 拉依赖）
docker compose -f docker-compose.infra.yml -f docker-compose.yml build

# ③ 起基础设施并初始化库（首次自动建 RuoYi 系统表 + agent_* 表）
docker compose -f docker-compose.infra.yml -f docker-compose.yml up -d mysql redis

# ④ 导入现网业务库（**必做**，见 §4）
./scripts/import-business-dump.sh /tmp/dkd_prod_20260923.sql.gz

# ⑤ 起应用 + 授权 + 健康检查（deploy.sh 内部会调用后两者）
./scripts/setup-agent-db.sh
./deploy.sh --skip-build
```

或者直接：`./deploy.sh`（它会自动判断要不要带 infra 文件、逐步等健康、最后调 healthcheck）。

> 用外部托管 MySQL/Redis：把 `.env.deploy` 里 `DKD_DB_HOST` / `DKD_REDIS_HOST` 指到外部地址，
> 然后**不要**带 `-f docker-compose.infra.yml`（此时 `setup-agent-db.sh` 会生成一份授权 SQL 交给 DBA 执行）。

---

## 4. 数据库初始化：哪些自动、哪些必须人工

**自动建（数据卷为空时执行 `initdb/`）**：RuoYi 系统表（19 张）、quartz、`tb_report`、`agent_*` 五张表。

**必须人工导入**：全部业务表（`tb_inventory` / `tb_order` / `tb_task` / `tb_task_details` / `tb_channel` / `tb_vending_machine` / `tb_node` / `tb_region` / `tb_sku` / `tb_emp` …）。

> 原因（AGENTS §1 约束 5）：这些表的 DDL **不在仓库里**（`sql/` 只有 RuoYi 系统表、quartz、`tb_report.sql`）。
> 这是排期 §八 FIX-6 登记的阻塞项；在拿到导出前，管理后台与智能体读工具都会报“表不存在”。

```bash
# 在现网/测试库导出（不要在业务高峰做；大表建议加 --single-transaction）
mysqldump -h<host> -uroot -p --single-transaction --databases dkd | gzip > dkd_prod_$(date +%F).sql.gz
# 拷到服务器后导入
./scripts/import-business-dump.sh /tmp/dkd_prod_2026-09-23.sql.gz
./scripts/setup-agent-db.sh      # 业务表到位后**必须重跑**：给 dkd_agent 补白名单只读授权
```

另有三份 DDL 属「需评审后人工执行」，部署脚本**故意不跑**：

| 文件 | 状态 | 处理 |
| --- | --- | --- |
| `docs/ddl/add_index_tb_order_and_tb_task.sql` | 待评审，尚未执行（FIX-4） | 评审通过后在低峰执行（含回滚） |
| `docs/ddl/agent_restock_plan_status_ordering.sql` | 本机已执行，生产待执行 | 新库由 `initdb/02` 直接建出目标结构；老库需人工 ALTER |
| `docs/ddl/create_agent_db_user.sql` | 由 `setup-agent-db.sh` 在运行时改写 host/口令后执行 | 不要手工改文件里的 host（容器里 127.0.0.1 是 agent 自己） |

---

## 5. 升级与回滚

```bash
cd /opt/dkd && git pull
cd deploy/docker

# 升级（同 tag 覆盖构建；生产建议每次部署打新 tag，便于回滚）
sed -i 's/^DKD_IMAGE_TAG=.*/DKD_IMAGE_TAG=2026-10-01-1/' .env.deploy
./deploy.sh                                   # build + up + 授权 + healthcheck

# 回滚到上一版镜像
sed -i 's/^DKD_IMAGE_TAG=.*/DKD_IMAGE_TAG=2026-09-23-1/' .env.deploy
DKD_IMAGE_TAG=2026-09-23-1 docker compose -f docker-compose.infra.yml -f docker-compose.yml up -d
```

- 数据层变更（DDL）**不随镜像回滚**：回滚前先看该版本是否带了 ALTER，按 `docs/ddl/` 里的回滚语句反向执行。
- 升级只影响应用容器；MySQL/Redis 数据卷不动，不会丢数据。
- Java 侧一键降级：`.env.deploy` 里 `DKD_AGENT_ENABLED=false` → `docker compose up -d dkd-admin`（主业务零影响，验收项 0-15 已验）。

---

## 6. 日常运维

```bash
./scripts/healthcheck.sh                       # 全链路检查（含 SSE 是否被缓冲）
docker compose -f docker-compose.infra.yml -f docker-compose.yml ps
docker compose logs -f --tail=200 dkd-agent    # 结构为单行 JSON，带 request_id
docker compose exec dkd-agent sh               # 进容器排查
docker compose restart dkd-agent               # 只重启智能体
```

**备份（上线前必须配 crontab）**：

```bash
KEEP_DAYS=30 ./scripts/backup.sh
# 0 3 * * * cd /opt/dkd/deploy/docker && KEEP_DAYS=30 ./scripts/backup.sh >> backups/cron.log 2>&1
```

备份内容 = 业务库（含 `agent_*`）+ 智能体会话 SQLite。**会话文件无法从 MySQL 重建**，必须一起备份；
`backup.sh` 用的是 SQLite 在线备份 API（不是 `cp`，避免撕裂快照）。备份请异地留存。

**审计查询**（3-7 审计页上线前，先用 SQL 顶）：

```sql
-- 某次请求的全链路留痕（request_id 由网关贯穿到 Python 到 MySQL）
select create_time, scene, user_id, trigger_type, action, target_type, target_id, result, error_msg
from agent_decision_log where request_id = '<X-Request-Id>' order by id;
-- 人工干预口径（1-7b：scene=2 补货 + trigger_type=3 人工干预）
select create_time, action, target_id, result, error_msg from agent_decision_log
where scene = 2 and trigger_type = 3 order by id desc limit 50;
```

---

## 7. 安全清单（部署时逐条核对，AGENTS §7）

- [x] **无明文密钥入仓**：`.env.deploy` 被 gitignore；模板只有占位符；`deploy.sh` 会拒绝 `CHANGE_ME` 直接部署。
- [x] **智能体不对外**：`dkd-agent` 未发布任何宿主机端口，只在 `dkd-net` 内被 Java 网关访问。
      （它不解析 JWT、只信任 `X-Agent-*` 头，一旦暴露即可冒充任意用户。）
- [x] **最小权限数据库账号**：`dkd_agent` 只能读白名单 `tb_*`、写 `agent_*`，无 DELETE/DDL；`setup-agent-db.sh` 会实测三条否决路径。
- [x] **Redis 有口令**（`--requirepass`）、MySQL 只绑回环、Druid 控制台不经 nginx 暴露。
- [ ] **HTTPS**：未包含，需自行补（§9）。
- [ ] **容器非 root 运行 / 只读根文件系统**：未做，属加固项（§9）。
- [ ] **凭据轮换**：见 `docs/security-credential-rotation.md`（FIX-7，仍需值班窗口）。

---

## 8. 常见故障对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `dkd-agent` 起不来，日志 `1045 Access denied` | 账号 host 是 `127.0.0.1`，容器源 IP 不匹配 | `.env.deploy` 里 `DKD_AGENT_DB_HOST_PATTERN=%`（或收窄到 compose 网段）后重跑 `setup-agent-db.sh` |
| 前端登录后接口 404 | nginx 反代路径写错（`/prod-api/` 必须映射到后端 `/`） | 检查 `nginx/default.conf` 的 `proxy_pass` 结尾 `/` |
| AI 助手“一次性吐出整段”/无打字机效果 | nginx 缓冲了 SSE | 确认 `/prod-api/` 段有 `proxy_buffering off;`（本仓库已带），改完 `docker compose restart web` |
| 长对话 60s 断流 | 反代读超时默认 60s | 已设 `proxy_read_timeout 3600s`；若上游还有 SLB，同样要放大 |
| `/agent/status` 显示 `upstream=down` | Java 容器里 `DKD_AGENT_BASE_URL` 指错 | 必须是 `http://dkd-agent:8090`（容器名，不是 127.0.0.1） |
| 回调 401 | 两端密钥不一致 | `DKD_AGENT_SERVICE_SECRET` 在 Java 与 Python 容器里必须逐字一致 |
| compose 报某变量 “required variable ... is missing a value” | 没传 `--env-file .env.deploy`（compose 默认只读项目目录下的 `.env`） | 用 `./deploy.sh`，或手工加 `--env-file .env.deploy` |
| `dkd-admin` 启动报连不上数据库/Redis | `DKD_DB_HOST`/`DKD_REDIS_HOST` 仍指向外部地址而实际用的是自建容器 | 自建库用服务名 `mysql`/`redis`（模板默认值） |
| 业务表不存在 | 没导入现网 dump | §4 |
| MySQL 初始化跑了一半失败 | `initdb` 中途出错 | `docker compose down -v` 清数据卷重来（**仅限首次初始化阶段**，有数据后严禁 `-v`） |

---

## 9. 未验证项与已知限制（不隐瞒）

1. **未在真实服务器/Docker 上构建运行过**。已完成与未完成的验证如下（不隐瞒）：
   - ✅ 全部 `.sh` 过 `bash -n`（8 个）；`docker compose config` 三种组合均解析通过
     （含 infra / 仅应用栈（模拟外部库）/ 文件顺序反转），跨文件 `depends_on` 合并生效；
     `.gitattributes` 强制 `*.sh` 为 LF（避免 Linux 上 `bad interpreter`）。
   - ❌ **未做过真实构建与启动**：本机 Docker 守护进程不可用（手动拉起 Docker Desktop 后约 7 分钟仍不就绪），
     Docker Hub 的 tag 查询也被网络拦截。因此基础镜像 tag（`maven:3.8.8-eclipse-temurin-8`、
     `eclipse-temurin:8-jre-jammy`、`node:20-alpine`、`nginx:1.27-alpine`、`mysql:8.0`、`redis:7-alpine`）
     **未经拉取验证**；`ghcr.io/astral-sh/uv:python3.11-bookworm-slim` 已验证存在。
     若某 tag 拉不到：Java 侧换 `maven:3.8.5-openjdk-8` / `eclipse-temurin:8-jre`；
     node 换 `node:20` / `node:22-alpine`；nginx 换 `nginx:alpine`。
   - ❌ 未验证：MySQL `initdb` 首次初始化脚本的真实执行、`setup-agent-db.sh` 的授权与否决路径实测、
     nginx SSE 不缓冲的真实验证。**首次部署请按 §3 分步做**（不要直接 `./deploy.sh` 一条命令梭哈），
     便于把失败定位到具体一步。
2. **HTTPS 未包含**：需自行加证书或用前置负载均衡；`nginx/default.conf` 只监听 80。
3. **容器以 root 运行**：加固方向是加非 root 用户 + 日志卷权限调整（RuoYi 写 `logs/`、Python 写 `var/`）。
4. **会话存储仍是单机 SQLite**：多实例部署会各存一份会话（checkpointer 迁移评审见
   `docs/dkd-agent-postgres-selection-review.md`，D1~D5 未拍板）。
5. **`dkd-app` 无健康检查端点**：用 TCP 探测代替（能握手不等于业务可用）。
6. **MySQL 8 的 `useSSL=true`**：`dkd-admin` 的 JDBC 串沿用仓库原值；官方镜像默认开启自签 SSL，
   如遇握手问题可改为 `useSSL=false`（仅内网）——属需评估的改动，不在本手册内顺手改。
7. **未包含前端 mock/子应用等可选构建**；`VITE_BUILD_COMPRESS=gzip` 依赖 `gzip_static on`（已配）。

---

## 10. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-23 | V0.1 | 首版：compose（应用/基础设施分离）、4 个 Dockerfile、1 键 `deploy.sh`、初始化与授权脚本、备份/健康检查脚本；为容器化改了 3 处配置占位符（见下方） |

**为容器化对业务配置做的最小改动**（属配置外置，不涉业务逻辑）：

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `dkd-admin/.../application-druid.yml` | DB url/用户名改 `${DKD_DB_HOST:localhost}` 等占位符（带默认值，本机行为不变） | 原值写死 `localhost`/`root`，容器里连不到 MySQL |
| `dkd-admin/.../application.yml` | Redis host/port 占位符化；`agent.base-url` 改 `${DKD_AGENT_BASE_URL:http://127.0.0.1:8090}`；`agent.enabled` 改 `${DKD_AGENT_ENABLED:true}` | 同上：容器里 agent 的名字是 `dkd-agent`，不是回环地址 |
| 仓库根 `.gitattributes` / `.gitignore` | `*.sh` 强制 LF；忽略 `.env.deploy` 与生成的授权 SQL | Windows 检出 CRLF 会让 Linux 上脚本报 `bad interpreter`；部署真值不入仓 |
