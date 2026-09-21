-- ----------------------------------------------------------------------------
-- DKD 智能体服务 · Phase 0 建表脚本（任务 0-5）
-- ----------------------------------------------------------------------------
-- 目的    ：为 dkd-agent（Python 侧）提供会话、留痕、补货计划三类持久化表。
-- 依据    ：《docs/DKD智能体接入方案-LangChain-LangGraph.md》V1.1 §4.2、§5.5
-- 执行环境：测试库 dkd（MySQL 8.0.40），生产执行前需 DBA 评审
-- 执行方式：mysql -h<host> -u<user> -p dkd < agent_tables.sql
-- 幂等性  ：本脚本可重复执行（CREATE TABLE IF NOT EXISTS）
-- 回滚    ：见文件末尾「回滚语句」；四表互无外键约束，可独立回滚
-- 锁表评估：四表均为新建空表，无锁表风险
--
-- 设计约定（与存量表的差异，已核对 mapper XML）：
--   1. 存量业务表（tb_task/tb_inventory/tb_channel/tb_sku/tb_vending_machine 等）
--      实测只有 create_time / update_time，仅 tb_node 带 create_by/update_by/remark；
--      本批新表按 AGENTS.md §2.4 规范补全 create_time/update_time/create_by/update_by
--      + del_flag（比存量更严，属规范要求）。
--   2. status 统一用 TINYINT 数字码（与 tb_task.task_status 的 Long 风格一致），
--      码值在列注释中固化；Python 侧用 IntEnum 双端对齐。
--   3. items / review_metrics / input_context / llm_output 用 JSON 类型
--      （MySQL 5.7+ 支持；本环境 8.0.40）。
--   4. 时间字段由应用侧写入（DateUtils / Python datetime），不使用 DB 默认值，
--      与 RuoYi 惯例一致。
-- ----------------------------------------------------------------------------


-- ----------------------------------------------------------------------------
-- 1. agent_conversation · 会话元数据
--    用途：一次对话的会话主体；LangGraph thread_id 与业务会话的映射。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_conversation` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `conversation_id` VARCHAR(64)  NOT NULL                COMMENT '业务会话ID（= LangGraph thread_id，UUID）',
  `user_id`         BIGINT       NOT NULL                COMMENT '发起用户ID（来自网关注入的 X-Agent-User）',
  `user_name`       VARCHAR(64)           DEFAULT NULL   COMMENT '发起用户名称（冗余，便于审计列表展示）',
  `region_id`       BIGINT                DEFAULT NULL   COMMENT '用户所属区域（网关注入，用于权限对齐）',
  `scene`           TINYINT      NOT NULL DEFAULT 1      COMMENT '场景：1-通用问答 2-补货 3-诊断 4-运营分析',
  `title`           VARCHAR(200)          DEFAULT NULL   COMMENT '会话标题（首条用户消息摘要）',
  `status`          TINYINT      NOT NULL DEFAULT 1      COMMENT '状态：1-进行中 2-已结束 3-已归档',
  `message_count`   INT          NOT NULL DEFAULT 0      COMMENT '消息条数（冗余计数，避免列表页 N+1）',
  `create_by`       VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time`     DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`       VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time`     DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`        CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_conversation_cid` (`conversation_id`),
  KEY `idx_agent_conversation_user_time` (`user_id`, `create_time`),
  KEY `idx_agent_conversation_scene_time` (`scene`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体会话元数据';


-- ----------------------------------------------------------------------------
-- 2. agent_message · 消息明细
--    用途：LangGraph checkpointer 的业务投影（可读、可审计、可做成本计量）。
--    计量口径：token 消耗按用户/场景聚合本表，无需另建计量表。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_message` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `conversation_id` VARCHAR(64)  NOT NULL                COMMENT '会话ID（逻辑关联 agent_conversation.conversation_id）',
  `seq`             INT          NOT NULL                COMMENT '会话内序号，从 1 递增（同会话唯一）',
  `msg_role`        TINYINT      NOT NULL                COMMENT '角色：1-用户 2-助手 3-工具结果 4-系统',
  `content`         MEDIUMTEXT            DEFAULT NULL   COMMENT '消息正文',
  `tool_calls`      JSON                  DEFAULT NULL   COMMENT '工具调用明细（名称/入参/结果摘要）',
  `model`           VARCHAR(64)           DEFAULT NULL   COMMENT '模型标识（如 deepseek-chat）',
  `tokens_in`       INT          NOT NULL DEFAULT 0      COMMENT '输入 token 数（成本计量）',
  `tokens_out`      INT          NOT NULL DEFAULT 0      COMMENT '输出 token 数（成本计量）',
  `latency_ms`      INT          NOT NULL DEFAULT 0      COMMENT '本消息耗时（毫秒）',
  `request_id`      VARCHAR(64)           DEFAULT NULL   COMMENT '贯穿 网关→Python→工具→回调 的追踪ID',
  `create_by`       VARCHAR(64)           DEFAULT NULL   COMMENT '创建者',
  `create_time`     DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`       VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time`     DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`        CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_message_conv_seq` (`conversation_id`, `seq`),
  KEY `idx_agent_message_time` (`create_time`),
  KEY `idx_agent_message_request` (`request_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体消息明细';


-- ----------------------------------------------------------------------------
-- 3. agent_decision_log · 决策留痕（审计核心表，AGENTS.md §6.3 强制）
--    用途：每次写操作决策必留痕——输入上下文 / LLM 输出 / 动作 / 结果 / 置信度。
--    注意：手机号、支付信息等敏感字段落库前必须脱敏（AGENTS.md §7.8）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_decision_log` (
  `id`             BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
  `request_id`     VARCHAR(64)            DEFAULT NULL   COMMENT '全链路追踪ID',
  `scene`          TINYINT       NOT NULL                COMMENT '场景：1-通用问答 2-补货 3-诊断 4-运营分析',
  `user_id`        BIGINT                 DEFAULT NULL   COMMENT '触发用户ID（定时任务为 NULL，见 trigger_type）',
  `trigger_type`   TINYINT       NOT NULL DEFAULT 1      COMMENT '触发方式：1-用户会话 2-定时任务 3-人工干预',
  `input_context`  JSON                   DEFAULT NULL   COMMENT '输入上下文（脱敏后）',
  `llm_output`     JSON                   DEFAULT NULL   COMMENT 'LLM 结构化输出',
  `action`         VARCHAR(64)            DEFAULT NULL   COMMENT '动作标识：restock_plan.create / task.create / diagnose.answer …',
  `target_type`    VARCHAR(32)            DEFAULT NULL   COMMENT '目标对象类型：vm / sku / task / plan',
  `target_id`      VARCHAR(64)            DEFAULT NULL   COMMENT '目标对象ID（如设备 inner_code、工单 task_id）',
  `result`         TINYINT       NOT NULL DEFAULT 0      COMMENT '结果：0-待定 1-成功 2-失败 3-被拒绝（校验未通过）',
  `error_msg`      VARCHAR(500)           DEFAULT NULL   COMMENT '失败/拒绝原因（原样保留业务异常 message）',
  `confidence`     DECIMAL(5,4)           DEFAULT NULL   COMMENT '置信度 0.0000~1.0000（自动建单放量的依据）',
  `cost_tokens`    INT           NOT NULL DEFAULT 0      COMMENT '本次决策消耗 token 合计',
  `create_by`      VARCHAR(64)            DEFAULT NULL   COMMENT '创建者',
  `create_time`    DATETIME               DEFAULT NULL   COMMENT '创建时间',
  `update_by`      VARCHAR(64)            DEFAULT NULL   COMMENT '更新者',
  `update_time`    DATETIME               DEFAULT NULL   COMMENT '更新时间',
  `del_flag`       CHAR(1)       NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  KEY `idx_agent_decision_scene_time` (`scene`, `create_time`),
  KEY `idx_agent_decision_target` (`target_type`, `target_id`),
  KEY `idx_agent_decision_request` (`request_id`),
  KEY `idx_agent_decision_result` (`result`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能体决策留痕（审计）';


-- ----------------------------------------------------------------------------
-- 4. agent_restock_plan · 补货计划（建议→调整/跳过→建单→复盘 全生命周期）
--    粒度：按设备整单（items 内含多货道明细）——依据 TaskServiceImpl.insertTaskDto
--          的防重校验为「同设备+同工单类型+进行中」，按货道拆单必然建单失败。
--
--    幂等设计（对应方案 V1.1 §5.5，任务 1-8）：
--      uk_agent_restock_plan_vm_date (vm_id, plan_date) 保证「同一设备同一天只有一份计划」。
--      - 定时任务重跑 → INSERT ... ON DUPLICATE KEY UPDATE，且仅当 status IN (1,2) 允许覆盖 items；
--        已进入 4-已建单 / 5-已复盘 的计划不得被重跑覆盖（应用侧校验）。
--      - 运营重复点击「确认建单」→ 应用侧先判 status，仅 1/2 放行，避免撞 Java 侧防重异常。
--      - 无可用接单人 → status=6，不调建单接口，进入人工处理队列。
--
--    status 状态机：1-建议 ──┬─→ 2-已调整 ──┬─→ 4-已建单 ──→ 5-已复盘
--                           │              │
--                           └─→ 3-已跳过    └─→ 6-待指派（无匹配接单人，待人工处理）
--    合法迁移：1→2/3/4/6，2→3/4/6，6→2/4，4→5（终态 3/5 不可再变更）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_restock_plan` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  `plan_date`       DATE         NOT NULL                COMMENT '计划归属日期（分析日，06:00 任务当日）',
  `vm_id`           BIGINT       NOT NULL                COMMENT '售货机ID（tb_vending_machine.id）',
  `inner_code`      VARCHAR(64)  NOT NULL                COMMENT '设备编号（tb_vending_machine.inner_code）',
  `region_id`       BIGINT                DEFAULT NULL   COMMENT '设备所属区域（接单人分配依据）',
  `node_id`         BIGINT                DEFAULT NULL   COMMENT '点位ID（点位画像/分组展示用）',
  `items`           JSON                  DEFAULT NULL   COMMENT '补货明细数组：[{skuId,skuName,channelId,channelCode,currentQuantity,maxCapacity,suggestedQuantity,priority,estimatedDays,reason}]',
  `sku_count`       INT          NOT NULL DEFAULT 0      COMMENT '涉及货道数（列表页展示，避免解析 JSON）',
  `total_quantity`  INT          NOT NULL DEFAULT 0      COMMENT '建议补货总量',
  `status`          TINYINT      NOT NULL DEFAULT 1      COMMENT '状态：1-建议 2-已调整 3-已跳过 4-已建单 5-已复盘 6-待指派',
  `adjust_reason`   VARCHAR(500)          DEFAULT NULL   COMMENT '人工调整/跳过原因（跳过时必填，方案 §1-7）',
  `adjusted_by`     BIGINT                DEFAULT NULL   COMMENT '最近一次人工干预的用户ID',
  `adjusted_time`   DATETIME              DEFAULT NULL   COMMENT '最近一次人工干预时间',
  `assignee_id`     BIGINT                DEFAULT NULL   COMMENT '接单人ID（tb_emp.emp_id，按设备区域匹配）',
  `assignee_name`   VARCHAR(64)           DEFAULT NULL   COMMENT '接单人姓名',
  `task_id`         BIGINT                DEFAULT NULL   COMMENT '已创建工单ID（tb_task.task_id）',
  `task_code`       VARCHAR(64)           DEFAULT NULL   COMMENT '工单编号（tb_task.task_code）',
  `review_metrics`  JSON                  DEFAULT NULL   COMMENT '7日复盘指标：{suggestedQty,actualQty,deviationRate,stockoutFlag,reviewTime}',
  `review_time`     DATETIME              DEFAULT NULL   COMMENT '复盘时间',
  `create_by`       VARCHAR(64)           DEFAULT NULL   COMMENT '创建者（定时任务写 system）',
  `create_time`     DATETIME              DEFAULT NULL   COMMENT '创建时间',
  `update_by`       VARCHAR(64)           DEFAULT NULL   COMMENT '更新者',
  `update_time`     DATETIME              DEFAULT NULL   COMMENT '更新时间',
  `del_flag`        CHAR(1)      NOT NULL DEFAULT '0'    COMMENT '删除标记：0-存在 1-删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_restock_plan_vm_date` (`vm_id`, `plan_date`),
  KEY `idx_agent_restock_plan_status_date` (`status`, `plan_date`),
  KEY `idx_agent_restock_plan_region` (`region_id`, `plan_date`),
  KEY `idx_agent_restock_plan_task` (`task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='智能补货计划';


-- ----------------------------------------------------------------------------
-- 验证语句（执行后自查）
-- ----------------------------------------------------------------------------
-- SHOW TABLES LIKE 'agent_%';
-- SHOW CREATE TABLE agent_restock_plan\G
-- SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES
--   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'agent_%';
-- 只读账号越权验证（应由 MySQL 拒绝）：
--   INSERT INTO agent_decision_log (scene) VALUES (1);


-- ----------------------------------------------------------------------------
-- 回滚语句（逐表独立，可单独执行）
-- ----------------------------------------------------------------------------
-- 注意：回滚将丢失会话/留痕/计划数据。生产回滚前需先备份：
--   mysqldump -h<host> -u<user> -p dkd agent_conversation agent_message \
--     agent_decision_log agent_restock_plan > backup_agent_tables_$(date +%F).sql
--
-- DROP TABLE IF EXISTS `agent_restock_plan`;
-- DROP TABLE IF EXISTS `agent_decision_log`;
-- DROP TABLE IF EXISTS `agent_message`;
-- DROP TABLE IF EXISTS `agent_conversation`;
