-- =============================================================================
-- 智能体配置化 · 表结构（排期任务 2-1 / 2-4）
-- =============================================================================
-- 目的    ：为「智能体配置台」提供配置主表、版本快照表与执行日志表。
-- 依据    ：《docs/智能体配置化方案-2026-11-03.md》§2、§6；
--           《docs/智能体实现详细计划表-2026-11-03.md》2-1 / 2-4。
-- 执行环境：MySQL 8.0（库 `dkd`）；生产执行前需 DBA 评审。
-- 执行方式：mysql -h<host> -u<user> -p dkd < agent_config.sql
-- 幂等性  ：可重复执行（CREATE TABLE IF NOT EXISTS）。seed 数据见 agent_config_seed.sql。
-- 回滚    ：见文件末尾（三表独立，可逐表回滚；回滚会丢失配置与日志数据，生产先备份）。
-- 锁表评估：三表均为新建空表，无锁表风险。
--
-- 三条设计纪律（改动前先读）：
--   1. **配置是 Java 的业务事实**：写操作只能经 Java（RuoYi 权限 + 审计 + 乐观锁），
--      Python 侧账号 `dkd_agent` 对 agent_config / agent_config_history **只有 SELECT**
--      （授权见 create_agent_db_user.sql），从权限层堵住“绕过 Java 改配置”；
--   2. **未配置 = 现状**：seed 的初值必须与代码常量逐字一致（由
--      docs/scripts/gen-agent-config-seed.py 从 app/agent_defaults.py 生成，禁止手改）；
--   3. **只增不改不删**：history 与 log 只有 INSERT；log 的保留期清理属排期 4-4，
--      由 Java/运维执行（Agent 账号无 DELETE 权限）。
-- =============================================================================


-- ----------------------------------------------------------------------------
-- 1. agent_config · 当前生效配置（一个智能体一行）
--
--    UK(code)：内置固定 restock/fault/chat；自定义编码 `^[a-z][a-z0-9_]{2,31}$`。
--    version：每次保存 +1（乐观锁 + 版本回放；agent_run.config_version 指向它）。
--    temperature / max_tokens：**NULL = 未设置（走模型默认）**，与改造前行为一致。
--    system_prompt：fault 载体不调用 LLM，故允许 NULL（配置化方案 §0.1 的代码事实；
--      其余载体由 Java 校验必填 100~8000 字）。
--    params_json：业务参数（按 runtime 不同 schema，区间由 app/agent_defaults.py 定义，
--      Python 侧读取后逐字段夹取——DB 里的值同样视为不可信输入）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_config` (
  `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code`            VARCHAR(64)   NOT NULL                COMMENT '智能体编码：内置 restock/fault/chat；自定义 ^[a-z][a-z0-9_]{2,31}$',
  `name`            VARCHAR(100)  NOT NULL                COMMENT '名称（列表/助手抽屉展示）',
  `description`     VARCHAR(500)           DEFAULT NULL   COMMENT '描述（用途/边界说明）',
  `runtime`         VARCHAR(32)   NOT NULL                COMMENT '运行载体：chat/restock/fault（决定可配能力面）',
  `scene`           INT           NOT NULL DEFAULT 1      COMMENT '场景码：1-4 内置；自定义 chat 从 101 起自动分配',
  `builtin`         TINYINT       NOT NULL DEFAULT 0      COMMENT '是否内置：1-内置（不可删除、不可改 code/runtime）',
  `system_prompt`   MEDIUMTEXT             DEFAULT NULL   COMMENT '角色设定/系统提示词（fault 无 LLM，可为 NULL）',
  `tools`           VARCHAR(500)           DEFAULT NULL   COMMENT '工具子集（逗号分隔；须为 runtime 工具白名单子集）',
  `model`           VARCHAR(64)            DEFAULT NULL   COMMENT '模型名（空 = 走 env 默认，保证未配置=现状）',
  `temperature`     DECIMAL(3,2)           DEFAULT NULL   COMMENT '采样温度 0~2；NULL=模型默认',
  `max_tokens`      INT                    DEFAULT NULL   COMMENT '输出上限 256~8192；NULL=模型默认',
  `timeout_s`       INT           NOT NULL DEFAULT 60     COMMENT '单次 LLM 调用超时（秒）5~300',
  `max_retries`     INT           NOT NULL DEFAULT 2      COMMENT 'LLM 失败重试次数 0~5',
  `params_json`     JSON                   DEFAULT NULL   COMMENT '业务参数（按 runtime 的 schema，Python 读取后夹取）',
  `enabled`         TINYINT       NOT NULL DEFAULT 1      COMMENT '启用状态：1-启用 0-停用（停用必填原因）',
  `disabled_reason` VARCHAR(200)           DEFAULT NULL   COMMENT '停用原因（enabled=0 时必填 5~100 字）',
  `version`         BIGINT        NOT NULL DEFAULT 1      COMMENT '配置版本（每次保存 +1；乐观锁与回放依据）',
  `change_note`     VARCHAR(200)           DEFAULT NULL   COMMENT '本次变更说明（写进 history）',
  `create_by`       VARCHAR(64)            DEFAULT NULL   COMMENT '创建者',
  `create_time`     DATETIME               DEFAULT NULL   COMMENT '创建时间',
  `update_by`       VARCHAR(64)            DEFAULT NULL   COMMENT '更新者',
  `update_time`     DATETIME               DEFAULT NULL   COMMENT '更新时间',
  `del_flag`        CHAR(1)       NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除（内置不可删除）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_config_code` (`code`),
  KEY `idx_agent_config_runtime` (`runtime`),
  KEY `idx_agent_config_scene` (`scene`),
  KEY `idx_agent_config_enabled` (`enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体当前生效配置';


-- ----------------------------------------------------------------------------
-- 2. agent_config_history · 版本快照（只增不改）
--
--    UK(code, version)：同一版本号不会重复写入；回滚 = 选历史版本 → 复制为**新** version
--    （不做“原地回退”，否则版本序列会出现断点，无法回答“当前配置是哪一次保存”）。
--    snapshot：整行 JSON（字段名与 agent_config 列名一致，params_json 以对象内嵌）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_config_history` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code`        VARCHAR(64)  NOT NULL                COMMENT '智能体编码',
  `version`     BIGINT       NOT NULL                COMMENT '被快照的版本号（= 当时 agent_config.version）',
  `snapshot`    JSON         NOT NULL                COMMENT '整行快照（字段名与 agent_config 列名一致）',
  `change_note` VARCHAR(200)          DEFAULT NULL   COMMENT '该版本的变更说明（回滚来源也要写清）',
  `create_by`   VARCHAR(64)           DEFAULT NULL   COMMENT '操作者',
  `create_time` DATETIME              DEFAULT NULL   COMMENT '快照时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_config_history_code_version` (`code`, `version`),
  KEY `idx_agent_config_history_code_time` (`code`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体配置版本快照（只增不改）';


-- ----------------------------------------------------------------------------
-- 3. agent_log · 执行日志（排期 2-4，只增；供后台「执行日志」页只读查询）
--
--    为什么单独一张表：现有 agent_run/step/tool_call 是**过程观测**（结构化、按运行组织），
--    而排障需要的是“某一时刻哪一行 WARN 说了什么”（非结构化、按时间组织）。
--    把日志硬压进 step 会让 error_msg 没有自然落点（配置化方案 §6 的取舍 A）。
--
--    写入方：Python 自定义 logging.Handler（异步批量，级别>=INFO，带 request_id/run_id 上下文）；
--    读取方：Java 只读接口；**不授 DELETE**（保留期清理属排期 4-4，由 Java/运维执行）。
--    索引依据查询形态：默认近 24h 按时间倒序；按 request_id 串全链路；级别+时间过滤。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_log` (
  `id`              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
  `create_time`     DATETIME(3)   NOT NULL                COMMENT '日志时间（毫秒精度）',
  `level`           VARCHAR(16)   NOT NULL                COMMENT '级别：DEBUG/INFO/WARNING/ERROR/CRITICAL',
  `logger`          VARCHAR(128)           DEFAULT NULL   COMMENT 'logger 名称（如 dkd.agent.chat）',
  `run_id`          VARCHAR(64)            DEFAULT NULL   COMMENT '关联运行ID（agent_run.run_id，可空）',
  `conversation_id` VARCHAR(64)            DEFAULT NULL   COMMENT '关联会话ID（可空）',
  `task_id`         VARCHAR(64)            DEFAULT NULL   COMMENT '关联工单ID（可空）',
  `scene`           VARCHAR(32)            DEFAULT NULL   COMMENT '场景：chat/restock/fault（可空）',
  `node`            VARCHAR(64)            DEFAULT NULL   COMMENT '图节点/阶段（可空）',
  `status`          VARCHAR(32)            DEFAULT NULL   COMMENT '状态标记：ok/error/timeout/skipped（可空）',
  `message`         VARCHAR(1000)          DEFAULT NULL   COMMENT '日志正文（已脱敏，不含 LLM 请求体/凭据）',
  `error_msg`       VARCHAR(1000)          DEFAULT NULL   COMMENT '错误摘要（异常类型+消息，已脱敏）',
  `request_id`      VARCHAR(64)            DEFAULT NULL   COMMENT '全链路追踪ID',
  `duration_ms`     INT                    DEFAULT NULL   COMMENT '耗时（毫秒，可空）',
  `create_by`       VARCHAR(64)            DEFAULT NULL   COMMENT '写入来源（如 python-agent）',
  PRIMARY KEY (`id`),
  KEY `idx_agent_log_create_time` (`create_time`),
  KEY `idx_agent_log_request` (`request_id`),
  KEY `idx_agent_log_level_time` (`level`, `create_time`),
  KEY `idx_agent_log_run` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体执行日志（只增，保留 30 天）';


-- ----------------------------------------------------------------------------
-- 验证语句（执行后自查）
-- ----------------------------------------------------------------------------
-- SHOW TABLES LIKE 'agent_%';
-- SHOW CREATE TABLE agent_config\G
-- SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES
--   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN
--     ('agent_config','agent_config_history','agent_log');
--
-- 只读账号越权验证（应由 MySQL 拒绝，ERROR 1142/1044）：
--   INSERT INTO dkd.agent_config (code,name,runtime) VALUES ('x','x','chat');
--   DELETE FROM dkd.agent_log WHERE id > 0;


-- ----------------------------------------------------------------------------
-- 回滚（逐表独立；生产先备份）
-- ----------------------------------------------------------------------------
-- mysqldump -h<host> -u<user> -p dkd agent_config agent_config_history agent_log \
--   > backup_agent_config_$(date +%F).sql
--
-- DROP TABLE IF EXISTS `agent_log`;
-- DROP TABLE IF EXISTS `agent_config_history`;
-- DROP TABLE IF EXISTS `agent_config`;


-- ============================================================================
-- 执行记录
-- ----------------------------------------------------------------------------
-- 2026-11-04：**已在本机 dkd 库执行**（MySQL 8.0.40，root@127.0.0.1）：
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_config.sql"`
--   随后执行 agent_config_seed.sql（3 行 config + 3 行 v1 history）与
--   create_agent_db_user.sql 的 2026-11-03 追加授权段（agent_config / history 仅 SELECT、
--   agent_log SELECT+INSERT）。回读结果见 docs/scripts/verify-agent-config-seed.py：
--   **PASS（3 行配置与 agent_defaults.py 逐字一致、均有 v1 快照）**。
--   只读账号拒绝写验证：INSERT agent_config / DELETE agent_log 均返回 ERROR 1142。
--   生产执行仍需 DBA 评审（见文件头）。


-- ============================================================================
-- 变更记录
-- ----------------------------------------------------------------------------
-- 2026-11-03（排期 2-1/2-4）：初版。三张新表。
--   - 与配置化方案 §2.1 的差异（有意）：`system_prompt` 允许 NULL——fault 载体不调 LLM
--     （结论由 services/diagnosis.py 纯规则合成），强制必填只会造出一个“配了但不生效”的字段；
--     Java 校验改为“chat/restock 必填，fault 忽略”。
--   - `model` 允许空串：空 = 走 DKD_AGENT_LLM_MODEL（未配置=现状）；非空才注入。
--   - 回滚脚本需与 seed 脚本版本同步：seed 仅 INSERT IGNORE，不覆盖人工修改。
-- ============================================================================
