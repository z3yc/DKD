-- =============================================================================
-- 对比报告持续化（排期 5-4/6-1 驾驶舱对比卡）
-- =============================================================================
-- 目的：compare-1-14 脚本每次双跑产出报告后落一行，驾驶舱读最新一行展示
--       「采纳/建议偏差 + n 样本量 + 口径」；n=0 时页面必须显示“样本不足”，不得画趋势。
-- 只增不改（证据），保留策略由 4-4 清理脚本另行处理（默认 90 天）。
-- 回滚：DROP TABLE agent_compare_report;
-- 执行环境：本机 dkd 库（MySQL 8.0.40）。
-- =============================================================================

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `agent_compare_report` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `plan_date` DATE NOT NULL COMMENT '对比的业务日期',
  `generated_at` DATETIME(3) NOT NULL COMMENT '报告生成时间（UTC 转本地）',
  `n` INT NOT NULL DEFAULT 0 COMMENT '配对货道样本数（驾驶舱必须展示，n 小不得下结论）',
  `rule_total` INT NOT NULL DEFAULT 0 COMMENT '规则版建议总量',
  `baseline_total` INT NOT NULL DEFAULT 0 COMMENT '基线引擎建议总量',
  `mean_delta_pct` DECIMAL(8,2) NULL COMMENT '偏差率均值（仅配对货道）',
  `median_delta_pct` DECIMAL(8,2) NULL COMMENT '偏差率中位数（仅配对货道）',
  `report_json` LONGTEXT NULL COMMENT '完整报告（compare 脚本原样 JSON）',
  `create_by` VARCHAR(64) NULL,
  `create_time` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_compare_time` (`generated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='规则版 vs 基线引擎对比报告（5-4/6-1）';

-- 校验：SHOW TABLES LIKE 'agent_compare_report';

-- 执行记录（2026-11-04）：已在本机 dkd 库执行（root@127.0.0.1，MySQL 8.0.40）。
--   agent_compare_report：表创建成功，compare 脚本 --persist 写入 1 行（n=0，驾驶舱读取 available=false）。
--   驾驶舱菜单 9035：插入成功，perms=manage:agentDashboard:list，component=manage/agentDashboard/index；重新登录后可见。