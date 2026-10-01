-- =============================================================================
-- 智能体 · PostgreSQL 角色与授权（排期 0-10 / PG 选型评审「方案 A」）
-- =============================================================================
--
-- 对位脚本：`docs/ddl/create_agent_db_user.sql`（MySQL 侧的两级授权）
-- 执行环境：PostgreSQL 17（本机服务名 `postgresql-x64-17`，5432 已监听）
--
-- ⚠️⚠️ 执行状态：**已在本机 PG 17.11 执行（2026-10-01）**，带真实验收证据：
--      `docs/scripts/verify-checkpoint-pg.py` → **18/18 PASS**（含 5 项拒绝路径）
--      + 跨进程恢复 3/3 PASS + DML 探针 1/1（结论见下【实测更正】）。
--      本机执行方式：**通过 `docs/scripts/setup-agent-pg.py`**（等价实现）——因为实测本机
--      `psql` 不在 PATH；本文件仍是评审与回滚的**权威文本**，两者改授权策略时须同步。
--      生产环境仍未执行：**D5 的另一半**（是否允许新增 DB 引擎）待运维/负责人拍板。
--
-- 幂等性：部分幂等——`CREATE ROLE` / `CREATE DATABASE` 重复执行会报错（这是有意的：
--        静默跳过会让「授权没生效」查不出原因）。重跑前先执行文件末尾「回滚」段。
-- 回滚  ：见文件末尾（DROP DATABASE / DROP ROLE；**不涉及任何业务数据**）
--
-- -----------------------------------------------------------------------------
-- 设计要点：为什么是「独立 database + 该库内的自有 schema」
-- -----------------------------------------------------------------------------
-- AGENTS §1 约束 5 / §7.3 的红线是「PG 不是业务数据的第二通道」。这条红线的可验证形式是：
--
--   1. PG 里**没有**任何 `tb_*` 业务表，业务读取仍走 MySQL 只读账号 + 表白名单；
--   2. 本角色对业务库（MySQL 的 `dkd`）**完全没有权限**——两者是不同的引擎，天然隔离；
--   3. 本角色的权限范围被限制在**它自己的 database 内**，且只用于 checkpoint
--      （将来的 pgvector 知识库索引另起 schema，见文件末尾「后续」段）。
--
-- -----------------------------------------------------------------------------
-- ⚠️ 一个必须显式解决的权限矛盾（评审稿 §8-4 已登记，属未验证假设）
-- -----------------------------------------------------------------------------
-- 评审稿要求「只授 checkpoint 表读写」，但 `AsyncPostgresSaver.setup()` 需要
-- **CREATE 权限**（建 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 等表与索引）。
-- 两条出路：
--
--   方案 1（本脚本采用，**实测唯一可行**）：角色**自己拥有**一个独立 database
--        （DDL 被限制在自己的库内）。简单、`setup()` 开箱可用、备份体系可复用；
--        「隔离」体现在**库级边界**而不是「表级授权」——对这个库之外的一切无权限。
--   方案 2（更严）：DBA 用超级用户跑一次 `setup()` 预建表，应用角色只给
--        SELECT/INSERT/UPDATE/DELETE，**不给 CREATE**。
--        ⛔ **实测结论（2026-10-01）：方案 2 不可行**——探针角色
--        （只有 CONNECT/USAGE + ALL TABLES 的 DML，`has_schema_privilege(CREATE)=false`）
--        跑 `setup()` 直接失败：
--            InsufficientPrivilege: 对模式 public 权限不够
--        原因：`setup()` 不只在“表不存在时建表”，它还要跑 `CREATE INDEX IF NOT EXISTS`
--        与 migration 记录写入，均需模式的 CREATE 权限。**因此不要再尝试方案 2**
--        （评审稿 §8-4 登记的那条未验证假设，现已关闭）。
--
-- 本脚本给出方案 1；方案 2 仅作为已证伪的历史记录保留在上面。
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：确认目标库/角色尚不存在
-- ---------------------------------------------------------------------------
SELECT datname FROM pg_database WHERE datname = 'dkd_agent_state';
SELECT rolname FROM pg_roles    WHERE rolname = 'dkd_agent_pg';

-- 两者都应返回 0 行。任一行有内容 → 先执行「回滚」段再继续。


-- ---------------------------------------------------------------------------
-- 第 1 步：创建角色
--
-- 口令**不得**写进本文件（AGENTS §7.1）。执行方式二选一：
--   psql -v agent_pg_password="$(读取本机 .env 的值)" -f docs/ddl/create_agent_pg_role.sql
--   或在 psql 交互里先 `\set agent_pg_password '...'` 再粘贴本文件。
--
-- 说明：
--   · LOGIN  —— 必需（应用要连）
--   · 不授 CREATEDB / CREATEROLE / SUPERUSER —— 最小权限
--   · NOINHERIT —— 权限只来自**直接授权**，不通过角色成员关系传递；
--                  以后若有人用“给组角色授权”的方式放权，本角色不会意外获得
--   · CONNECTION LIMIT —— 限制连接数，避免应用连池打满把 PG 拖垮
--
-- 环境提示（实测，2026-09-23）：本机 `psql` **不在 PATH**（未纳入开发链），要么用
--   "C:\Program Files\PostgreSQL\17\bin\psql.exe"，要么把节 1~3 转成 Python
--   （`docs/scripts/verify-checkpoint-pg.py` 会用 psycopg 连接，不依赖 psql）。
-- ---------------------------------------------------------------------------
CREATE ROLE dkd_agent_pg WITH
  LOGIN
  PASSWORD :'agent_pg_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  CONNECTION LIMIT 20;

COMMENT ON ROLE dkd_agent_pg IS
  'DKD 智能体 checkpoint 专用角色；只承载会话状态，绝不是业务数据通道（AGENTS §7.3）';


-- ---------------------------------------------------------------------------
-- 第 2 步：创建独立 database（owner = 本角色 → DDL 被限制在自己的库内）
--
-- 为什么 owner 给应用角色：`setup()` 要建表，没有 CREATE 权限会在**首次部署**就失败。
-- 之所以仍然安全：这个库里**没有也不会有任何业务表**，它存在只为存放 checkpoint。
-- ---------------------------------------------------------------------------
CREATE DATABASE dkd_agent_state
  OWNER dkd_agent_pg
  ENCODING 'UTF8'
  TEMPLATE template0;                      -- template0 避免继承模板库里的意外对象

COMMENT ON DATABASE dkd_agent_state IS
  '智能体会话 checkpoint 存储（LangGraph AsyncPostgresSaver）；不存放业务数据';


-- ---------------------------------------------------------------------------
-- 第 3 步：回收 public schema 的公共权限（PG 15+ 默认已收紧，这里显式声明意图）
--
-- 连到目标库执行（psql: \c dkd_agent_state）
-- ---------------------------------------------------------------------------
\connect dkd_agent_state

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  ALL ON SCHEMA public TO dkd_agent_pg;   -- 建表所需（setup() 幂等建表/索引）

-- 兜底：确保没有其他角色能连上/写入这个库
REVOKE ALL ON DATABASE dkd_agent_state FROM PUBLIC;
-- ⚠️ 实测修正（2026-10-01）：这一句会连带收走**任何**其他角色的 CONNECT 权限。
--    这是有意为之的收紧（等价于 MySQL 侧只授 127.0.0.1），但代价是：
--    新增任何使用者（如监控账号、DBA 排障账号）时**必须显式** GRANT CONNECT，
--    否则报的是「用户没有CONNECT权限」——这句本身就是我们收走的。
--    踩到实例：方案 2 探针角色第一次跑就卡在这一步（脚本缺陷，非结论）。

-- ⚠️ 不授任何业务相关对象：这里的库里没有业务表可授；
--    而 MySQL 的业务库与本引擎无关，天然不可达（这正是选独立引擎的隔离优势）。


-- ---------------------------------------------------------------------------
-- 第 4 步：字段与表级说明（表由 setup() 创建，这里只声明归属）
--
-- setup() 会创建（版本 langgraph-checkpoint-postgres 3.1.2）：
--   checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations
-- 我们**不手工建表**：手工建表会与库内部的 migration 版本表不一致，
-- 升级 langgraph 时会出现「表存在但 migration 记录缺失」的诡异状态。
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 第 5 步：验证（D5 闭环后执行；结果记入排期证据栏）
--
-- 5.1 正向：应用角色能连上并能建表（setup() 幂等）
--     psql "postgresql://dkd_agent_pg:****@127.0.0.1:5432/dkd_agent_state" -c "select current_user"
--     然后启动服务：DKD_AGENT_CHECKPOINT_BACKEND=postgres uv run uvicorn app.main:app
--     再重启一次确认 setup() 幂等（第二次启动不报错）
--
-- 5.2 负向（必须全部被拒；**已实测，见下方执行记录**）
--     ① 建新库：        CREATE DATABASE x;           -- InsufficientPrivilege（NOCREATEDB）✅
--     ② 建角色：        CREATE ROLE x;               -- InsufficientPrivilege（NOCREATEROLE）✅
--     ③ 改系统参数：    ALTER SYSTEM SET ...;        -- InsufficientPrivilege（非超级用户）✅
--     ④ 装非 trusted 扩展：CREATE EXTENSION postgres_fdw;  -- InsufficientPrivilege ✅
--     ⑤ 在别的库建表：  CREATE TABLE x(id int);      -- InsufficientPrivilege ✅
--
-- 5.3 ⚠️ 已记录且已接受的边界（**不是漏洞，但必须知道**）
--     库 owner 可以安装 **trusted 扩展**：实测 `CREATE EXTENSION pg_trgm` **成功**。
--     这是 PostgreSQL 13+ 的 trusted extension 机制 + “角色是自己库的 owner”的固有含义。
--     影响面：仅限该自有库（装不了 postgres_fdw 等非 trusted 扩展，也碰不到业务数据）。
--     若业务上不能接受，则授权形态必须换掉（而方案 2 已证伪，所以实际等价于
--     “由 DBA 代装扩展 + 把库 owner 收归 DBA + 承担 setup() 需预建表的维护负担”）——
--     成本明显高于收益，故本期**明确接受**该边界，但要求它写在脚本里而不是靠人记得。
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-09-28：脚本创建（当时未执行）。
-- 2026-10-01：**已在本机执行**。执行人：开发（AI 协作）；PG 版本 **PostgreSQL 17.11**（Windows）。
--   执行方式：`docs/scripts/setup-agent-pg.py`（本机无 psql）→ 建角色 `dkd_agent_pg`
--   （NOSUPERUSER/NOCREATEDB/NOCREATEROLE/CONNECTION LIMIT 20）+ 建库 `dkd_agent_state`
--   （owner = 该角色）+ 收紧 public schema 与 database PUBLIC 权限；脚本重复执行幂等（角色/库已存在则跳过）。
--   验收结果（全部实跑）：
--     · `docs/scripts/verify-checkpoint-pg.py --phase all` → **PASS=18 FAIL=0**
--       （含 setup 幂等、生命周期、5 项拒绝路径、口令脱敏、事件循环策略）
--     · 跨进程验收（两个独立进程）→ 进程 A 写 2 条 / 进程 B 新进程读回 2 条并追加到 4 条，**3/3 PASS**
--     · **方案 2 探针 → 不可行**（InsufficientPrivilege: 对模式 public 权限不够）
--     · 服务级验证：`python -m app` 启动后 `/health` → `checkpoint_backend=postgres`、
--       `checkpoint_open=true`、`checkpoint_location` 已脱敏；真实 SSE 会话落 PG
--       （`thread_id=pg-verify-conv`，checkpoints 3 行 / blobs 3 行，验收后已清理）
--   已接受边界：库 owner 可装 trusted 扩展（pg_trgm 实测成功，探针遗留已 DROP）。
--   生产待办：D5 的另一半 —— 是否允许新增 DB 引擎（非本机环境）尚未拍板。


-- ---------------------------------------------------------------------------
-- 回滚
-- ---------------------------------------------------------------------------
-- 必须以**超级用户**连接 postgres 库执行：
--
--   -- 1) 断开该库上的连接（否则 DROP DATABASE 会被占用阻塞）
--   SELECT pg_terminate_backend(pid) FROM pg_stat_activity
--    WHERE datname = 'dkd_agent_state' AND pid <> pg_backend_pid();
--
--   -- 2) 删库（会话数据随之丢弃；评审稿 D4：不做双向同步，切回 sqlite 即可继续）
--   DROP DATABASE IF EXISTS dkd_agent_state;
--
--   -- 3) 删角色
--   DROP ROLE IF EXISTS dkd_agent_pg;
--
-- 回滚影响面：
--   · 仅影响 PG 上的会话状态，**不触碰任何业务数据、不触碰 agent_* 表（仍在 MySQL）**；
--   · 回滚后把 `DKD_AGENT_CHECKPOINT_BACKEND` 改回 `sqlite` 即可恢复服务
--     （SQLite 文件一直保留，评审稿落地清单第 7 条）。
--
-- 应用程序侧补偿：部署环境需同时把 `DKD_AGENT_PG_DSN` 清空或保留为占位符
-- （选 postgres 而无 DSN 时服务会拒绝启动，不会静默回退）。


-- ---------------------------------------------------------------------------
-- 后续（**不在本期范围**，勿提前执行）
-- ---------------------------------------------------------------------------
-- 波次 4（知识库地基，对应排期 2-7 / 评审稿 §4.3 第 ③ 层）若确认要做语义检索，
-- 在本实例上启用 pgvector 与独立的 knowledge schema：
--
--   CREATE EXTENSION IF NOT EXISTS vector;          -- 需在 dkd_agent_state 库内、由 owner 执行
--   CREATE SCHEMA IF NOT EXISTS knowledge AUTHORIZATION dkd_agent_pg;
--
-- ⚠️ 两条纪律（写在这里是为了防止「顺手做了」）：
--   1. 现在**不要**创建案例库表——案例抽取口径尚未冻结（波次 4 交付物），
--      先建表会让口径被表结构反向绑架；
--   2. 案例库是**派生副本**，权威源仍是 MySQL 的 `tb_task`（Java），
--      同步链路若涉及写操作**不得绕过 Java 回调**（AGENTS §7.3）。
-- =============================================================================
