-- ============================================================================
-- agent_restock_pause · 补货自动分析的暂停开关（排期任务 1-7）
-- 归档：docs/ddl/agent_restock_pause.sql    创建日期：2026-09-21
-- 执行环境：本机 dkd 库（MySQL 8.0），执行账号 root（建表 + 授权需高权限）
-- 幂等：本脚本可重复执行（CREATE TABLE IF NOT EXISTS + 幂等 UPSERT 演示）
-- 回滚：见文件末尾「回滚语句」
-- ----------------------------------------------------------------------------
-- 为什么需要这张表（而不是复用某张表的一个字段）：
--   原型 V2 的「⏸ 暂停自动分析」是**计划级开关**：暂停后次日 06:00 不再生成新建议，
--   但当前清单仍可继续处理；恢复后继续。它有三个硬要求：
--     ① 必须持久化（跨进程重启、跨天生效）——放内存或 checkpoint 都不行；
--     ② 必须留痕（谁暂停的、什么原因、谁恢复的）——审计要求（AGENTS §6.3）；
--     ③ 必须是智能体自有数据（不属业务事实）→ `agent_` 前缀 + 不授 DELETE（AGENTS §7.3/§7.4）。
--   塞进 agent_restock_plan 的 JSON 列会让“计划数据”与“系统开关”耦合在同一行，
--   而计划行是按 (vm_id, plan_date) 逐台设备的，天然不适合放全局开关。
-- ----------------------------------------------------------------------------
-- 设计：**单行表**（scope 唯一键），用 UPSERT 表达“暂停/恢复”，
--       不做行历史（历史在 agent_decision_log 里，那里才是留痕表）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS `agent_restock_pause` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `scope`        VARCHAR(32)  NOT NULL DEFAULT 'global' COMMENT '暂停范围：global-全局自动分析（首期仅此一种）',
  `paused`       TINYINT      NOT NULL DEFAULT 0      COMMENT '开关：1-已暂停自动分析 0-正常',
  `reason`       VARCHAR(500)          DEFAULT NULL   COMMENT '暂停/恢复原因（人工必填，用于复盘归因）',
  `paused_by`    VARCHAR(64)           DEFAULT NULL   COMMENT '暂停操作人（网关 X-Agent-User 的用户ID）',
  `paused_time`  DATETIME              DEFAULT NULL   COMMENT '暂停时间',
  `resumed_by`   VARCHAR(64)           DEFAULT NULL   COMMENT '恢复操作人',
  `resumed_time` DATETIME              DEFAULT NULL   COMMENT '恢复时间',
  `create_by`    VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time`  DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`    VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time`  DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`     CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除（软删，不物理删除）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_restock_pause_scope` (`scope`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='补货自动分析暂停开关';

-- ----------------------------------------------------------------------------
-- 授权（沿用 docs/ddl/create_agent_db_user.sql 的两级授权原则）
-- 选查改，不授 DELETE（软删）、不授 DDL
-- ----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON dkd.agent_restock_pause
  TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';

-- ----------------------------------------------------------------------------
-- 验证语句（执行后自查）
-- ----------------------------------------------------------------------------
-- SHOW CREATE TABLE agent_restock_pause\G
-- SHOW GRANTS FOR 'dkd_agent'@'127.0.0.1';
-- 幂等 UPSERT 演示（重复执行只有一行）：
--   INSERT INTO agent_restock_pause (`scope`, paused, reason, create_time, update_time, del_flag)
--     VALUES ('global', 1, '演练', NOW(), NOW(), '0')
--     ON DUPLICATE KEY UPDATE paused = VALUES(paused), reason = VALUES(reason), update_time = NOW();
-- 越权验证（应由 MySQL 拒绝，ERROR 1142）：
--   DELETE FROM agent_restock_pause;            -- 不授 DELETE
--   DROP TABLE agent_restock_pause;             -- 不授 DDL

-- ============================================================================
-- 回滚语句（需 root；回滚前请确认 1-9 的定时任务已不再依赖该开关）
-- ============================================================================
-- DROP TABLE IF EXISTS `agent_restock_pause`;
