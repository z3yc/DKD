-- =============================================================================
-- 会话持久化增强（排期 4-1/4-2）：给会话头与消息加 config_version
-- =============================================================================
-- 目的：详情页/排障要能回答「这条回复是由哪一版智能体配置生成的」；
--       配置回滚后，旧消息不因当前配置变化而被改写（版本只是标注，不回写历史内容）。
-- 影响面：两张表各加一列（NULL 允许，历史行保持 NULL；V8.0 加列对现有数据无锁表风险，
--         小表且低峰执行；上线顺序：先执行本 DDL，再发布 Python/Java）。
-- 回滚：
--   ALTER TABLE agent_message      DROP COLUMN config_version;
--   ALTER TABLE agent_conversation DROP COLUMN config_version;
-- 执行环境：本机 dkd 库（MySQL 8.0.40）；生产执行前评估锁表窗口。
-- =============================================================================

SET NAMES utf8mb4;

ALTER TABLE `agent_message`
  ADD COLUMN `config_version` BIGINT NULL COMMENT '生成本条消息时使用的智能体配置版本（NULL=历史/旧数据）'
  AFTER `model`;

ALTER TABLE `agent_conversation`
  ADD COLUMN `config_version` BIGINT NULL COMMENT '本会话首次创建时使用的智能体配置版本（NULL=历史/旧数据）'
  AFTER `scene`;

-- 校验：两表都应有 config_version 列
-- SHOW COLUMNS FROM agent_message LIKE 'config_version';
-- SHOW COLUMNS FROM agent_conversation LIKE 'config_version';

-- 执行记录
-- 2026-11-04：已在本机 dkd 库执行（root@127.0.0.1，MySQL 8.0.40），两列均存在且 NULL 允许；
--   真机两轮会话验证：agent_conversation.config_version=1、agent_message 4 行 config_version 均为 1；
--   Java 会话列表/详情接口返回 configVersion；历史行保持 NULL（页面显示“—（历史数据）”）。
