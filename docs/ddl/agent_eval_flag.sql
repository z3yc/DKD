-- =============================================================================
-- 评测报告与降级标记（排期 5-1 / 5-6）
-- =============================================================================
-- 1) agent_eval_report：版本保存前的评测护栏报告（5-1）
--    - 只增不改（评测是证据，不是可编辑内容）；
--    - config_code + config_version 定位“这次评测针对哪版配置”；
--    - status: scored（有 verified 样本并算出准确率）/ blocked（无 verified 样本，不能算准确率）/ failed（跑挂）
--    - accuracy 只在 scored 时有值；baseline_* 来自评测集 meta.baseline（未设置则 NULL，不伪造）。
-- 2) agent_restock_plan.llm_calibrated：LLM 校准标记（5-6）
--    - NULL = 历史数据/未知（旧行）；1 = 本轮 LLM 校准生效；0 = 退化为纯统计基线（未校准）
--    - 前端据此显示“未校准”徽标；驾驶舱告警条可统计 0 的比例。
-- 回滚：
--   ALTER TABLE agent_restock_plan DROP COLUMN llm_calibrated;
--   DROP TABLE agent_eval_report;
-- 执行环境：本机 dkd 库（MySQL 8.0.40）。
-- =============================================================================

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `agent_eval_report` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  `config_code` VARCHAR(64) NOT NULL COMMENT '智能体配置编码（agent_config.code）',
  `config_version` BIGINT NOT NULL COMMENT '被评测的配置版本',
  `scene` INT NULL COMMENT '评测场景（空=全场景汇总）',
  `mode` VARCHAR(16) NOT NULL COMMENT 'live=真实 LLM / offline=结构校验（不调 LLM）',
  `status` VARCHAR(16) NOT NULL COMMENT 'scored=已算准确率 / blocked=无 verified 样本 / failed=运行失败',
  `accuracy` DECIMAL(5,4) NULL COMMENT '准确率（仅 scored；0~1）',
  `total` INT NOT NULL DEFAULT 0 COMMENT '参与用例数',
  `passed` INT NOT NULL DEFAULT 0 COMMENT '通过数',
  `failed` INT NOT NULL DEFAULT 0 COMMENT '失败数',
  `blocked` INT NOT NULL DEFAULT 0 COMMENT '未评分/阻塞数',
  `baseline_accuracy` DECIMAL(5,4) NULL COMMENT '评测集 meta.baseline.accuracy（空=未设基线）',
  `baseline_n` INT NULL COMMENT '评测集 meta.baseline.n',
  `report_json` LONGTEXT NULL COMMENT '完整报告（含逐条结果/错误类型聚合；不含密钥）',
  `run_by` VARCHAR(64) NULL COMMENT '执行人/CI',
  `create_time` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_eval_code_time` (`config_code`, `create_time`),
  KEY `idx_eval_version` (`config_version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体配置版本评测报告（5-1）';

ALTER TABLE `agent_restock_plan`
  ADD COLUMN `llm_calibrated` TINYINT NULL COMMENT 'LLM 校准标记：1=校准生效 0=纯统计基线 NULL=历史/未知（5-6）'
  AFTER `items`;

-- 校验：
-- SHOW COLUMNS FROM agent_restock_plan LIKE 'llm_calibrated';
-- SHOW TABLES LIKE 'agent_eval_report';

-- 执行记录
-- 2026-11-04：已在本机 dkd 库执行（root@127.0.0.1，MySQL 8.0.40）：agent_eval_report 16 列创建成功，
--   agent_restock_plan.llm_calibrated 列存在（NULL 允许）。真机验证：评测报告 POST/查询/gate 通过；
--   gate 临时开启后无报告保存返回 409 evalGate、force 保存 200 且 change_note 留痕；测试报告已清理。
