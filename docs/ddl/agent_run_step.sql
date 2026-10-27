-- ----------------------------------------------------------------------------
-- 运行观测三表：agent_run / agent_step / agent_tool_call（F-01，2026-10-26）
-- ----------------------------------------------------------------------------
-- 目的：一次 Agent 运行（run）→ 每一轮调度（step）→ 每次工具调用（tool_call）可逐轮回放。
--       补货场景与故障场景**共用**这三张表，通过 scene_code 区分，不各建一套。
--
-- 与 agent_decision_log 的关系（重要，别当成两套留痕）：
--   decision_log 是"决策"粒度：一次写操作一条（输入/LLM 输出/动作/结果/置信度），回答"谁在什么时候点了什么"；
--   run/step/tool_call 是"过程"粒度：回答"这一轮派给谁、调了哪个工具、拿到几行、花了多久"。
--   两者用 run_id 关联（decision_log 后续加 run_id/step_no 两列即可挂到时间线，见 F-02 备注）。
--
-- 写入方：
--   - Python（dkd-agent）写入三张表（智能体自有数据，不是业务事实，AGENTS §7.3）；
--   - Java 只读（管理端时间线页），不参与写。
-- 权限：dkd_agent 账号 SELECT/INSERT/UPDATE，**不授 DELETE**（软删，AGENTS §7.4）与任何 DDL。
--
-- 执行环境：本机 MySQL 8.0（dkd 库）；生成环境执行前需 DBA 评审。
-- 执行方式：mysql -h127.0.0.1 -uroot -p dkd < agent_run_step.sql
-- 回滚    ：见文件末尾（DROP 三张表；数据不可恢复，执行前先备份）。
-- 幂等    ：全部 CREATE TABLE IF NOT EXISTS，可重复执行。
-- ----------------------------------------------------------------------------

-- ⚠️ 必须置顶（2026-10-26 实测踩坑）：Windows 上客户端默认字符集常是 GBK，
-- 中文 COMMENT 会被按 GBK 切词（反斜杠转义吃掉引号），报 ERROR 1064 near '?…'。
-- 本文件中文全在引号内，SET NAMES 即可修；执行命令建议同时显式带字符集：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_run_step.sql
SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- 1. agent_run · 一次运行
--    status 状态机：running → waiting_confirm（等人确认）→ done / failed / interrupted
--    interrupted 由启动扫描写入（服务重启时把遗留 running 收尾，见 F-05）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_run` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `run_id`         VARCHAR(64)  NOT NULL                COMMENT '运行ID（业务主键，Python 生成）',
  `scene_code`     VARCHAR(32)  NOT NULL                COMMENT '场景：restock-补货 fault-故障诊断 chat-对话',
  `subject_type`   VARCHAR(32)  NOT NULL DEFAULT 'vm'   COMMENT '对象类型：vm-设备 / plan-补货计划',
  `subject_id`     VARCHAR(64)  NOT NULL                COMMENT '对象ID（设备为 inner_code）',
  `request_id`     VARCHAR(64)           DEFAULT NULL   COMMENT '全链路追踪ID（网关/定时任务注入，缺失写 NULL 不编造）',
  `trigger_type`   TINYINT      NOT NULL DEFAULT 1      COMMENT '触发方式：1-用户 2-定时任务 3-人工干预',
  `status`         VARCHAR(16)  NOT NULL DEFAULT 'running' COMMENT '状态：running/waiting_confirm/done/failed/interrupted',
  `config_version` BIGINT                DEFAULT NULL   COMMENT '运行时的配置版本（agent_config.version，用于回放）',
  `stop_reason`    VARCHAR(200)          DEFAULT NULL   COMMENT '终止原因（interrupted/failed 必填：便于页面直接展示）',
  `started_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开始时间',
  `finished_at`    DATETIME              DEFAULT NULL   COMMENT '结束时间（终态才写）',
  `create_by`      VARCHAR(64)           DEFAULT NULL   COMMENT '创建者（用户ID / system）',
  `create_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `del_flag`       CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_run_id` (`run_id`),
  KEY `idx_agent_run_scene_status` (`scene_code`, `status`, `started_at`),
  KEY `idx_agent_run_subject` (`subject_type`, `subject_id`, `started_at`),
  KEY `idx_agent_run_request` (`request_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体运行（一次 run）';

-- ----------------------------------------------------------------------------
-- 2. agent_step · 一轮调度
--    uk(run_id, step_no)：中断恢复会**从头重跑被中断的节点**，同一 step_no 可能被写两次，
--    因此写入侧用 INSERT ... ON DUPLICATE KEY UPDATE（覆盖为最新一次），页面不会看到重复轮次。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_step` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `run_id`        VARCHAR(64)  NOT NULL                COMMENT '所属运行ID',
  `step_no`       INT          NOT NULL                COMMENT '轮次序号（从 1 开始）',
  `role_code`     VARCHAR(64)  NOT NULL                COMMENT '角色编码：diagnostician/dispatcher/reviewer/supervisor',
  `action`        VARCHAR(64)  NOT NULL                COMMENT '动作标识：diagnose/dispatch_propose/dispatch_confirm/review/finish',
  `summary`       VARCHAR(500)          DEFAULT NULL   COMMENT '本轮一句话结论（页面直接展示）',
  `evidence_json` JSON                   DEFAULT NULL   COMMENT '证据/提案 JSON（含 evidenceId → tool_call 映射）',
  `input_tokens`  INT          NOT NULL DEFAULT 0      COMMENT '本轮输入 token',
  `output_tokens` INT          NOT NULL DEFAULT 0      COMMENT '本轮输出 token',
  `duration_ms`   INT          NOT NULL DEFAULT 0      COMMENT '本轮耗时（毫秒）',
  `status`        VARCHAR(16)  NOT NULL DEFAULT 'ok'   COMMENT '状态：ok/error/timeout/skipped',
  `error_msg`     VARCHAR(500)           DEFAULT NULL   COMMENT '失败原因原文（页面展示，不得为空时写"未知"）',
  `create_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_step_run_no` (`run_id`, `step_no`),
  KEY `idx_agent_step_run` (`run_id`, `step_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体运行轮次（一步）';

-- ----------------------------------------------------------------------------
-- 3. agent_tool_call · 一次工具调用
--    call_seq：同一轮内第几次工具调用（中断重跑可能重复，用 uk 保证幂等覆盖）。
--    params_digest：参数摘要（**不落原始参数**，可能含手机号等 PII，AGENTS §7.8）。
--    row_count：工具返回行数（算成本与判断"是否被截断"的依据）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_tool_call` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `run_id`        VARCHAR(64)  NOT NULL                COMMENT '所属运行ID',
  `step_no`       INT          NOT NULL                COMMENT '所属轮次',
  `call_seq`      INT          NOT NULL DEFAULT 1      COMMENT '轮内序号（从 1 开始）',
  `tool_code`     VARCHAR(64)  NOT NULL                COMMENT '工具编码（与 agent_tool.tool_code 对应）',
  `params_digest` VARCHAR(500)          DEFAULT NULL   COMMENT '参数摘要（脱敏后，不存原始值）',
  `row_count`     INT          NOT NULL DEFAULT 0      COMMENT '返回行数',
  `duration_ms`   INT          NOT NULL DEFAULT 0      COMMENT '耗时（毫秒）',
  `result_status` VARCHAR(16)  NOT NULL DEFAULT 'ok'   COMMENT '结果：ok/error/timeout/truncated',
  `error_msg`     VARCHAR(500)           DEFAULT NULL   COMMENT '失败原因原文',
  `create_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_tool_call_seq` (`run_id`, `step_no`, `call_seq`),
  KEY `idx_agent_tool_call_tool` (`tool_code`, `create_time`),
  KEY `idx_agent_tool_call_run` (`run_id`, `step_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体工具调用（一次 call）';

-- ----------------------------------------------------------------------------
-- 验证语句（执行后自查）
-- ----------------------------------------------------------------------------
-- SHOW TABLES LIKE 'agent_%';
-- SHOW CREATE TABLE agent_run\G
-- 期望：三张表都存在；关键唯一键分别是 uk_agent_run_id / uk_agent_step_run_no / uk_agent_tool_call_seq

-- ----------------------------------------------------------------------------
-- 回滚（按依赖倒序；执行前确认已备份 agent_run/agent_step/agent_tool_call）
-- ----------------------------------------------------------------------------
-- DROP TABLE IF EXISTS `agent_tool_call`;
-- DROP TABLE IF EXISTS `agent_step`;
-- DROP TABLE IF EXISTS `agent_run`;
