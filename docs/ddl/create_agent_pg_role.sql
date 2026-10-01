-- =============================================================================
-- 智能体 · PostgreSQL 角色与授权（排期 0-10 / PG 选型评审「方案 A」）
-- =============================================================================
--
-- 对位脚本：`docs/ddl/create_agent_db_user.sql`（MySQL 侧的两级授权）
-- 执行环境：PostgreSQL 17（本机服务名 `postgresql-x64-17`，5432 已监听）
--
-- ⚠️⚠️ 执行状态：**本脚本尚未在任何 PG 实例上执行过**（评审稿 D5 未闭环：本机 PG 账号
--      与「生产是否允许新增 DB 引擎」尚未确认）。执行后请在下文「执行记录」登记。
--      在 D5 闭环前，任何环境都**不得**把 `DKD_AGENT_CHECKPOINT_BACKEND` 置为 postgres。
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
--   方案 1（本脚本采用，推荐）：角色**自己拥有**一个独立 database（DDL 被限制在自己的库内）。
--        · 简单、`setup()` 开箱可用、蓝绿/备份体系可复用；
--        · 「隔离」体现在**库级边界**而不是「表级授权」——对这个库之外的一切无权限。
--   方案 2（更严，需 DBA 介入）：DBA 用超级用户跑一次 `setup()` 预建表，应用角色只给
--        SELECT/INSERT/UPDATE/DELETE，**不给 CREATE**。
--        · ⚠️ **未验证**：`setup()` 是否能在无 CREATE 权限下于已存在的表上幂等通过，
--          尚未在真库验证（含 `CREATE INDEX IF NOT EXISTS` 的权限判定）。
--          选方案 2 之前必须先用 `docs/scripts/verify-checkpoint-pg.py` 验一次，
--          否则会在「首次部署」而不是「写代码」时才发现。
--
-- 本脚本给出方案 1；方案 2 的 GRANT 以注释形式附在末尾。
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
-- 5.2 负向（必须全部被拒，对应 MySQL 侧 8 项拒绝路径的同等要求）
--     ① 读别的库：      SELECT * FROM dkd_public_schema.anything;      -- 期望 permission denied / 库不存在
--     ② 建新库：        CREATE DATABASE x;                            -- 期望 permission denied（NOCREATEDB）
--     ③ 建角色：        CREATE ROLE x;                                -- 期望 permission denied（NOCREATEROLE）
--     ④ 超级用户操作：  ALTER SYSTEM SET log_statement='all';         -- 期望 permission denied
--     ⑤ 删 checkpoint： DELETE FROM checkpoints;                      -- 允许（checkpoint 属会话状态，
--                                                                         评审稿 D4：切换不做双向同步）
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-09-28：脚本创建，**尚未在任何 PG 实例上执行**（D5 未闭环）。
--   执行后请登记：执行人 / 时间 / PG 版本 / 第 5.2 节 5 项结果 / setup() 是否幂等。


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
