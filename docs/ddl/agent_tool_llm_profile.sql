-- =============================================================================
-- 智能体 · 工具注册表与模型档案（排期 3-4 / 3-5）
-- =============================================================================
-- 目的：为「工具中心（agent_tool）」与「模型档案（agent_llm_profile + 探测记录）」
--       提供表结构与初始 seed；对应原型 S12（工具/MCP 同表）与 S14（模型档案）。
-- 执行环境：MySQL 8.0（库 dkd）；生产执行前需 DBA 评审。
-- 执行方式：mysql --default-character-set=utf8mb4 -h<host> -u<user> -p dkd < agent_tool_llm_profile.sql
-- 幂等性：CREATE TABLE IF NOT EXISTS + INSERT IGNORE（重跑不覆盖人工修改）。
-- 回滚：见文件末尾；三表独立。
--
-- 三条边界（与 AGENTS §7 一致）：
--   1. 工具/模型档案属**智能体配置**，写操作只能经 Java（RuoYi 权限 + @Log），
--      Python 侧账号 `dkd_agent` 只有 SELECT（授权见 create_agent_db_user.sql 追加段）；
--   2. `agent_llm_profile.api_key_env` **只存环境变量名**，永不存密钥值；探测记录不落响应体/密钥；
--   3. `agent_tool.read_only=1` 才允许被 LLM 直接调用；写工具（含未来 MCP 写工具）必须存
--      read_only=0 并在接入层拒绝（AGENTS §7.9：写操作只能人工确认后回调 Java）。
-- =============================================================================


-- ----------------------------------------------------------------------------
-- 1. agent_tool · 工具注册表（本地工具与 MCP 工具同表）
--
--    `code` 必须能映射到代码里的工具名（启动校验；映射不到直接拒绝启动，见排期 3-4）。
--    `source`：local=代码内置工具；mcp=外部 MCP server 同步来的工具（3-7 写入）。
--    `scope`：适用载体/场景分组（chat / restock / fault / all），首期仅 chat 工具开放给模型。
--    `timeout_ms` / `row_limit`：工具级护栏（MCP 强制 ≤5000ms、返回 ≤200 行；本地工具为声明值，
--      实际读取护栏仍由 read_tools 的 5s/2000 行统一兜底）。
--    `builtin`：1=随代码发布的工具（不可删除，只能启停/调参）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_tool` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code`        VARCHAR(64)  NOT NULL                COMMENT '工具编码（必须能映射到代码/MCP 工具名）',
  `name`        VARCHAR(100) NOT NULL                COMMENT '展示名称',
  `description` VARCHAR(500)          DEFAULT NULL   COMMENT '能力说明（给运营看，不是给模型看）',
  `scope`       VARCHAR(32)  NOT NULL DEFAULT 'chat' COMMENT '适用载体：chat/restock/fault/all',
  `source`      VARCHAR(16)  NOT NULL DEFAULT 'local' COMMENT '来源：local-代码内置 mcp-外部 MCP',
  `read_only`   TINYINT      NOT NULL DEFAULT 1      COMMENT '是否只读：1-只读（可被模型调用）0-写工具（必须拒绝直调）',
  `enabled`     TINYINT      NOT NULL DEFAULT 1      COMMENT '启用状态：0-停用（不注入模型）',
  `timeout_ms`  INT          NOT NULL DEFAULT 10000  COMMENT '工具级超时（毫秒）；MCP 强制 ≤5000',
  `row_limit`   INT          NOT NULL DEFAULT 2000   COMMENT '工具级返回行数上限；MCP 强制 ≤200',
  `builtin`     TINYINT      NOT NULL DEFAULT 1      COMMENT '是否随代码发布：1-内置（不可删除）',
  `create_by`   VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time` DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`   VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time` DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`    CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_tool_code` (`code`),
  KEY `idx_agent_tool_scope_enabled` (`scope`, `enabled`),
  KEY `idx_agent_tool_source` (`source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体工具注册表（本地+MCP）';


-- ----------------------------------------------------------------------------
-- 2. agent_llm_profile · 模型档案（S14）
--
--    ⚠️ `api_key_env` 只存**环境变量名**（如 DKD_DEEPSEEK_API_KEY），密钥值只在本机 .env/部署环境；
--       页面永不回显密钥、日志/探测记录不落密钥（AGENTS §7.1）。
--    `is_default=1` 最多一个（应用层校验；MySQL 无部分唯一索引，故用服务层保证）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_llm_profile` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code`        VARCHAR(64)  NOT NULL                COMMENT '档案编码（自定义，^[a-z][a-z0-9_]{2,31}$）',
  `name`        VARCHAR(100) NOT NULL                COMMENT '展示名称',
  `provider`    VARCHAR(32)  NOT NULL DEFAULT 'openai_compatible' COMMENT '供应商类型：openai_compatible/deepseek',
  `base_url`    VARCHAR(255) NOT NULL                COMMENT 'OpenAI 兼容基址（如 https://api.deepseek.com）',
  `model`       VARCHAR(64)  NOT NULL                COMMENT '模型名（如 deepseek-chat）',
  `api_key_env` VARCHAR(64)  NOT NULL                COMMENT '**环境变量名**（如 DKD_DEEPSEEK_API_KEY），绝不存密钥值',
  `timeout_s`   INT          NOT NULL DEFAULT 60     COMMENT '调用超时（秒）5~300',
  `max_retries` INT          NOT NULL DEFAULT 2      COMMENT '失败重试 0~5',
  `enabled`     TINYINT      NOT NULL DEFAULT 1      COMMENT '启用状态：1-启用 0-停用',
  `is_default`  TINYINT      NOT NULL DEFAULT 0      COMMENT '是否默认档案：1-默认（应用层保证最多一个）',
  `description` VARCHAR(500)          DEFAULT NULL   COMMENT '说明（用途/成本注意）',
  `create_by`   VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time` DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`   VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time` DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`    CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_llm_profile_code` (`code`),
  KEY `idx_agent_llm_profile_enabled` (`enabled`, `is_default`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体模型档案（仅连接信息，采样参数在 agent_config）';


-- ----------------------------------------------------------------------------
-- 3. agent_llm_probe · 连通性探测记录（只增）
--
--    记录每次「测试连通」：HTTP 状态/延迟/实际服务模型/结论；**不记响应正文、不记密钥**。
--    `error_type` 只存异常类型（如 AuthenticationError/LlmError），错误消息脱敏截断。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_llm_probe` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `profile_code`  VARCHAR(64)  NOT NULL                COMMENT '模型档案编码',
  `ok`            TINYINT      NOT NULL DEFAULT 0      COMMENT '探测结论：1-成功 0-失败',
  `http_status`   INT                   DEFAULT NULL   COMMENT 'HTTP 状态码（拿不到则 NULL）',
  `latency_ms`    INT          NOT NULL DEFAULT 0      COMMENT '端到端延迟（毫秒）',
  `model_served`  VARCHAR(64)           DEFAULT NULL   COMMENT '服务端实际返回模型名（如 deepseek-flash）',
  `error_type`    VARCHAR(64)           DEFAULT NULL   COMMENT '失败异常类型（不落堆栈）',
  `error_msg`     VARCHAR(500)          DEFAULT NULL   COMMENT '失败摘要（脱敏截断）',
  `request_id`    VARCHAR(64)           DEFAULT NULL   COMMENT '全链路追踪ID',
  `create_by`     VARCHAR(64)           DEFAULT NULL   COMMENT '操作者',
  `create_time`   DATETIME(3)  NOT NULL                COMMENT '探测时间（毫秒）',
  PRIMARY KEY (`id`),
  KEY `idx_agent_llm_probe_code_time` (`profile_code`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='模型连通性探测记录（只增）';


-- ----------------------------------------------------------------------------
-- seed：本地工具注册（与 app/prompts/assistant_chat.TOOL_NAMES_BY_SCENE 并集一致）
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO `agent_tool`
  (`code`, `name`, `description`, `scope`, `source`, `read_only`, `enabled`, `timeout_ms`, `row_limit`, `builtin`,
   `create_by`, `create_time`, `update_time`, `del_flag`)
VALUES
  ('list_restock_suggestions', '补货建议清单', '查询某日补货建议计划（可按设备/明细）', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('get_machine_inventory', '设备库存', '按设备编码查货道库存与容量', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('list_inflight_work_orders', '在途工单', '查设备进行中的工单（接单人/状态）', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('get_sales_summary', '近期销量', '按设备与窗口聚合出货成功订单销量', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('find_devices', '设备定位', '按编号/点位/区域模糊定位设备（≤10 台）', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('list_device_anomalies', '设备异常检测', '查订单/库存异常（窗口可配）', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('search_device_history', '历史维修线索', '按设备查历史维修工单（只当线索，不当故障现象）', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0'),
  ('build_device_diagnosis', '结构化诊断', '合成证据/未知项/动作/置信度（不含病因）', 'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0');

-- seed：默认模型档案（api_key_env 只存变量名；基址/模型可由页面修改）
INSERT IGNORE INTO `agent_llm_profile`
  (`code`, `name`, `provider`, `base_url`, `model`, `api_key_env`, `timeout_s`, `max_retries`, `enabled`, `is_default`,
   `description`, `create_by`, `create_time`, `update_time`, `del_flag`)
VALUES
  ('deepseek', 'DeepSeek（默认）', 'deepseek', 'https://api.deepseek.com', 'deepseek-chat',
   'DKD_DEEPSEEK_API_KEY', 60, 2, 1, 1, '当前生产使用的 OpenAI 兼容供应商；密钥只从环境变量读取',
   'system', NOW(), NOW(), '0');


-- ----------------------------------------------------------------------------
-- 验证 / 回滚
-- ----------------------------------------------------------------------------
-- SELECT code, enabled, read_only, timeout_ms, row_limit FROM agent_tool ORDER BY code;
-- SELECT code, name, model, api_key_env, is_default FROM agent_llm_profile;
-- 只读账号越权验证（应 1142）：INSERT INTO dkd.agent_tool (...) VALUES (...);
--
-- 回滚（生产先备份）：
-- DROP TABLE IF EXISTS `agent_llm_probe`;
-- DROP TABLE IF EXISTS `agent_llm_profile`;
-- DROP TABLE IF EXISTS `agent_tool`;


-- ============================================================================
-- 变更记录
-- 2026-11-04（排期 3-4/3-5）：初版。三表 + 8 个本地工具 seed + 默认 DeepSeek 档案。
--   - `agent_llm_profile.is_default` 的“最多一个”由应用层保证（MySQL 无部分唯一索引）；
--   - `agent_tool.timeout_ms/row_limit` 对本地工具是“声明值”，实际读取护栏仍由 read_tools 统一兜底；
--     MCP 工具由接入层强制 ≤5000ms / ≤200 行（排期 3-7）。
-- ============================================================================
