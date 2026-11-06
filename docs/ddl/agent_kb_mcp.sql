-- =============================================================================
-- 智能体 · 知识库 L2（3-6）与 MCP 接入（3-7）表结构
-- =============================================================================
-- 目的：为「知识库 L2（文档语料层）」与「MCP 工具接入」提供持久化结构。
-- 依据：计划表 3-6/3-7；L3 案例库仍「未启用·数据未达标」（FIX-22），本文件不建 L3 表。
-- 执行环境：MySQL 8.0（库 dkd）；生产执行前需 DBA 评审 + 语料授权确认（1-5）。
-- 执行方式：mysql --default-character-set=utf8mb4 -h<host> -u<user> -p dkd < agent_kb_mcp.sql
-- 幂等性：CREATE TABLE IF NOT EXISTS；agent_tool 两列用 information_schema 判断后 ALTER（可重复执行）。
-- 回滚：见文件末尾。
--
-- 边界（AGENTS §7）：
--   1. 知识库文档/切片/检索记录属**智能体自有数据**（agent_*），Python 可 SELECT/INSERT/UPDATE，
--      不授 DELETE/DDL；写入入口只经 Python ingest（Java 页面只做读/代理），避免把语料当业务事实；
--   2. MCP 只接 **read_only=1** 工具；写工具一律拒绝（仍只能人工确认后回调 Java）；
--      密钥只存环境变量名（env_keys_json），不存密钥值；
--   3. 检索记录只增不改（命中归因/回放依据）。
-- =============================================================================


-- ----------------------------------------------------------------------------
-- 1. agent_kb_document · 知识库文档（L2 语料）
--
--    content 存**提取后的纯文本**（PDF/Word 由上传端先转文本；首版支持 txt/md/粘贴文本），
--    原文件不入库。content_hash 用于「重复上传检测 + 增量重建不双写」。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_kb_document` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `doc_code`      VARCHAR(64)  NOT NULL                COMMENT '文档编码（唯一；上传时生成或人工指定）',
  `title`         VARCHAR(200) NOT NULL                COMMENT '标题',
  `category`      VARCHAR(32)  NOT NULL DEFAULT 'manual' COMMENT '分类：manual-厂商手册 fault_code-故障码表 sop-运营SOP faq-常见问题 other',
  `source_type`   VARCHAR(16)  NOT NULL DEFAULT 'text' COMMENT '来源：text-粘贴/上传文本 file-文件名引用 url-链接（首版只存引用文本）',
  `source_ref`    VARCHAR(500)          DEFAULT NULL   COMMENT '来源引用（文件名/URL；不存敏感内容）',
  `content`       LONGTEXT     NOT NULL                COMMENT '提取后的纯文本（≤1MB，由应用层限制）',
  `content_hash`  CHAR(64)     NOT NULL                COMMENT '内容 SHA-256（去重/增量重建）',
  `chunk_count`   INT          NOT NULL DEFAULT 0      COMMENT '切片数（冗余，列表页展示）',
  `status`        TINYINT      NOT NULL DEFAULT 1      COMMENT '状态：1-已启用 2-已停用 0-处理中（切片失败时保持 0）',
  `enabled`       TINYINT      NOT NULL DEFAULT 1      COMMENT '是否参与检索：1-是 0-否',
  `create_by`     VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time`   DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`     VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time`   DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`      CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_kb_document_code` (`doc_code`),
  KEY `idx_agent_kb_document_category` (`category`, `enabled`),
  KEY `idx_agent_kb_document_hash` (`content_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='知识库 L2 文档（纯文本语料）';


-- ----------------------------------------------------------------------------
-- 2. agent_kb_chunk · 切片与本地向量
--
--    `vector_json`：本地向量化结果（哈希 bag-of-words / char-bigram，L2 归一化后 float 数组）。
--    为什么不用 pgvector：当前无 embedding 服务；先保证「检索→引用→回退」闭环可测，
--    后续可把 vector_json 换成 pgvector 列（适配层在 Python，不改业务语义）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_kb_chunk` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `document_id`   BIGINT       NOT NULL                COMMENT '文档ID（agent_kb_document.id）',
  `doc_code`      VARCHAR(64)  NOT NULL                COMMENT '文档编码（冗余，便于按 code 重建）',
  `chunk_no`      INT          NOT NULL                COMMENT '切片序号（从 1 递增）',
  `content`       TEXT         NOT NULL                COMMENT '切片正文（≤2000 字符）',
  `content_length` INT         NOT NULL DEFAULT 0      COMMENT '正文字符数',
  `vector_json`   LONGTEXT              DEFAULT NULL   COMMENT '本地向量（JSON float 数组）',
  `dim`           INT          NOT NULL DEFAULT 0      COMMENT '向量维度',
  `enabled`       TINYINT      NOT NULL DEFAULT 1      COMMENT '是否参与检索：1-是 0-否',
  `create_time`   DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_time`   DATETIME              DEFAULT NULL   COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_kb_chunk_doc_no` (`document_id`, `chunk_no`),
  KEY `idx_agent_kb_chunk_doc_code` (`doc_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='知识库切片与本地向量';


-- ----------------------------------------------------------------------------
-- 3. agent_kb_retrieval · 检索/引用记录（只增，命中归因）
--
--    hits_json：[{chunkId,docCode,title,score,excerpt}]；used=是否被最终回答引用；
--    fallback_reason：无引用时为什么回退（no_hit/low_score/citation_missing）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_kb_retrieval` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `request_id`      VARCHAR(64)           DEFAULT NULL   COMMENT '全链路追踪ID',
  `conversation_id` VARCHAR(64)           DEFAULT NULL   COMMENT '会话ID（工具/诊断查询时）',
  `scene`           INT                   DEFAULT NULL   COMMENT '场景码',
  `query`           VARCHAR(500) NOT NULL                COMMENT '检索语句（不含用户隐私？原样，前端输入）',
  `top_k`           INT          NOT NULL DEFAULT 5      COMMENT '期望召回数',
  `hits_json`       JSON                  DEFAULT NULL   COMMENT '命中列表（含分数）',
  `used`            TINYINT      NOT NULL DEFAULT 0      COMMENT '是否被最终回答引用',
  `fallback_reason` VARCHAR(64)           DEFAULT NULL   COMMENT '未引用原因：no_hit/low_score/citation_missing',
  `create_time`     DATETIME(3)  NOT NULL                COMMENT '检索时间（毫秒）',
  PRIMARY KEY (`id`),
  KEY `idx_agent_kb_retrieval_request` (`request_id`),
  KEY `idx_agent_kb_retrieval_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='知识库检索与引用记录（只增）';


-- ----------------------------------------------------------------------------
-- 4. agent_mcp_server · MCP server 接入配置
--
--    ⚠️ 只接 read_only=1 的工具；写工具由接入层拒绝（不靠 server 自觉）。
--    `env_keys_json` 只存**环境变量名映射**（如 {"API_KEY":"DKD_MCP_X_KEY"}），不存密钥值。
--    首期只支持 http（SSE/JSON-RPC）与 stdio 两种 transport；出网白名单由部署层（防火墙/代理）保证。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_mcp_server` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code`           VARCHAR(64)  NOT NULL                COMMENT 'server 编码（^[a-z][a-z0-9_]{2,31}$）',
  `name`           VARCHAR(100) NOT NULL                COMMENT '展示名称',
  `transport`      VARCHAR(16)  NOT NULL DEFAULT 'http' COMMENT '传输：http（JSON-RPC over HTTP）| stdio',
  `endpoint`       VARCHAR(500) NOT NULL                COMMENT 'http 为 URL；stdio 为可执行命令（不含密钥，密钥走 env_keys_json）',
  `env_keys_json`  JSON                  DEFAULT NULL   COMMENT '需要的环境变量名映射（只存名字）',
  `enabled`        TINYINT      NOT NULL DEFAULT 0      COMMENT '是否启用：默认 0（需安全复核后手工开启）',
  `timeout_ms`     INT          NOT NULL DEFAULT 5000   COMMENT '调用超时（≤5000）',
  `row_limit`      INT          NOT NULL DEFAULT 200    COMMENT '返回行数上限（≤200）',
  `read_only_only` TINYINT      NOT NULL DEFAULT 1      COMMENT '只接只读工具：固定 1（应用层强制）',
  `status`         VARCHAR(16)  NOT NULL DEFAULT 'unknown' COMMENT '探测状态：unknown/ok/error/disabled',
  `last_probe_time` DATETIME(3)          DEFAULT NULL   COMMENT '最近探测时间',
  `last_probe_msg` VARCHAR(500)          DEFAULT NULL   COMMENT '最近探测结论（脱敏）',
  `create_by`      VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time`    DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`      VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time`    DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`       CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_mcp_server_code` (`code`),
  KEY `idx_agent_mcp_server_enabled` (`enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='MCP server 接入配置';


-- ----------------------------------------------------------------------------
-- 5. agent_mcp_tool · MCP 工具同步结果（与 agent_tool 通过 code 关联）
--
--    `code` = `mcp.<server_code>.<tool_name>`（与本地工具同表不冲突）；只读工具才允许同步；
--    input_schema 原样保存，供模型 function calling 使用。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_mcp_tool` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `server_id`     BIGINT       NOT NULL                COMMENT 'MCP server ID',
  `code`          VARCHAR(128) NOT NULL                COMMENT '工具编码：mcp.<server>.<tool>',
  `name`          VARCHAR(100) NOT NULL                COMMENT '工具名（原始）',
  `description`   VARCHAR(500)          DEFAULT NULL   COMMENT '描述（来自 server）',
  `input_schema`  JSON                  DEFAULT NULL   COMMENT '入参 JSON Schema（function calling）',
  `read_only`     TINYINT      NOT NULL DEFAULT 1      COMMENT '只读：1-允许 0-拒绝（写工具不同步）',
  `enabled`       TINYINT      NOT NULL DEFAULT 1      COMMENT '是否启用',
  `synced_at`     DATETIME              DEFAULT NULL   COMMENT '最近同步时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_mcp_tool_code` (`code`),
  KEY `idx_agent_mcp_tool_server` (`server_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='MCP 工具同步结果';


-- ----------------------------------------------------------------------------
-- 6. agent_mcp_probe · MCP 启动/手工探测记录（只增）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_mcp_probe` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `server_id`   BIGINT       NOT NULL                COMMENT 'MCP server ID',
  `ok`          TINYINT      NOT NULL DEFAULT 0      COMMENT '探测结论：1-成功 0-失败',
  `latency_ms`  INT          NOT NULL DEFAULT 0      COMMENT '耗时（毫秒）',
  `tool_count`  INT          NOT NULL DEFAULT 0      COMMENT '只读工具数',
  `error_type`  VARCHAR(64)           DEFAULT NULL   COMMENT '失败异常类型',
  `error_msg`   VARCHAR(500)          DEFAULT NULL   COMMENT '失败摘要（脱敏）',
  `create_time` DATETIME(3)  NOT NULL                COMMENT '探测时间（毫秒）',
  PRIMARY KEY (`id`),
  KEY `idx_agent_mcp_probe_server` (`server_id`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='MCP 探测记录（只增）';


-- ----------------------------------------------------------------------------
-- 7. agent_tool 扩展：MCP 工具与本地工具同表（source='mcp'）
--    MySQL 8 不支持 ADD COLUMN IF NOT EXISTS，用 information_schema 判断（可重复执行）。
-- ----------------------------------------------------------------------------
SET @sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE `agent_tool` ADD COLUMN `server_id` BIGINT NULL COMMENT ''MCP server ID（本地工具为 NULL）'' AFTER `builtin`',
    'SELECT 1')
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_tool' AND COLUMN_NAME = 'server_id'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE `agent_tool` ADD COLUMN `input_schema_json` JSON NULL COMMENT ''入参 JSON Schema（MCP/本地可选）'' AFTER `server_id`',
    'SELECT 1')
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_tool' AND COLUMN_NAME = 'input_schema_json'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ----------------------------------------------------------------------------
-- 验证 / 回滚
-- ----------------------------------------------------------------------------
-- SHOW TABLES LIKE 'agent_kb%'; SHOW TABLES LIKE 'agent_mcp%';
-- SHOW COLUMNS FROM agent_tool LIKE 'server_id';
-- 只读账号越权验证（应 1142）：INSERT INTO dkd.agent_kb_document (...) VALUES (...);
--
-- 回滚（生产先备份；agent_tool 两列如无数据可保留）：
-- DROP TABLE IF EXISTS `agent_mcp_probe`;
-- DROP TABLE IF EXISTS `agent_mcp_tool`;
-- DROP TABLE IF EXISTS `agent_mcp_server`;
-- DROP TABLE IF EXISTS `agent_kb_retrieval`;
-- DROP TABLE IF EXISTS `agent_kb_chunk`;
-- DROP TABLE IF EXISTS `agent_kb_document`;
-- ALTER TABLE `agent_tool` DROP COLUMN `input_schema_json`;
-- ALTER TABLE `agent_tool` DROP COLUMN `server_id`;


-- ============================================================================
-- 执行记录
-- ----------------------------------------------------------------------------
-- 2026-11-04：**已在本机 dkd 库执行**（root@127.0.0.1，MySQL 8.0.40，
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_kb_mcp.sql"`）。
--   执行后：6 张新表（agent_kb_document/chunk/retrieval、agent_mcp_server/tool/probe）
--   与 agent_tool 两列（server_id/input_schema_json）均就绪；
--   授权段见 create_agent_db_user.sql 的 2026-11-04 追加（kb 三表 SELECT/INSERT/UPDATE、
--   mcp_server 只读、mcp_tool SELECT/INSERT/UPDATE、mcp_probe SELECT/INSERT）。
--   seed/语料/真实 MCP server 尚未接入（待 1-5 授权与 3-7 清单）。

-- ============================================================================
-- 变更记录
-- 2026-11-04（排期 3-6/3-7）：初版。知识库 L2 三表 + MCP 三表 + agent_tool 两列扩展。
--   - 向量暂存 vector_json（本地哈希/TF-IDF 向量），不依赖 pgvector/外部 embedding；
--   - MCP 默认 enabled=0（安全复核后手工开启），只读强制、密钥只存 env 名；
--   - L3 案例库不建表（数据不达标，页面如实“未启用”）。
-- ============================================================================
