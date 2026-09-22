-- ----------------------------------------------------------------------------
-- dkd-agent 数据库账号与授权（任务 0-4）
-- ----------------------------------------------------------------------------
-- 目的    ：为 dkd-agent 创建最小权限账号，落实「业务表只读 + 写走 Java 回调」红线。
-- 依据    ：方案 V1.1 §3.4 / §4.1、AGENTS §1 约束 2、§7.3
-- 执行环境：dkd 库所在 MySQL（本机 8.0.40）；生产执行前需 DBA 评审
-- 执行方式：把 <AGENT_DB_PASSWORD> 替换为真实口令后执行（口令只存 .env 与环境变量，不入库）
--   mysql -h127.0.0.1 -uroot -p < create_agent_db_user.sql
-- 回滚    ：见文件末尾
--
-- 权限模型（**两级授权**，重要）：
--   ① 业务表 tb_*（白名单内）：仅 SELECT —— 业务事实的唯一所有者是 Java，Agent 只读；
--   ② 智能体自有表 agent_*：SELECT/INSERT/UPDATE —— 会话/留痕/计划数据由 Agent 自己维护，
--      不是"业务事实"，故不违反 §7.3；但**不授 DELETE**（AGENTS §7.4 软删除）与任何 DDL。
--   业务数据的一切写操作（建工单等）仍然只能回调 Java REST。
--
-- 为什么显式收回而非"不授权就安全"：
--   本机 MySQL 实例上除 dkd 外还有 my/gogs/itest/test/sky_take_out/db03/db04/tlias 等库，
--   必须确保该账号无法读取这些库（MySQL 新用户默认无权限，此处显式 REVOKE 防历史授权残留）。
-- ----------------------------------------------------------------------------

-- 1) 账号（仅允许本机/内网来源；生产请按实际网段收窄 host）
CREATE USER IF NOT EXISTS 'dkd_agent'@'127.0.0.1' IDENTIFIED BY '<AGENT_DB_PASSWORD>';
CREATE USER IF NOT EXISTS 'dkd_agent'@'localhost' IDENTIFIED BY '<AGENT_DB_PASSWORD>';

-- 2) 清空历史授权（幂等重跑安全；防止前次误授保留）
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'dkd_agent'@'127.0.0.1';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'dkd_agent'@'localhost';

-- 3) 业务表：只读，仅白名单（表名必须带 tb_ 前缀，AGENTS §1 约束 5）
GRANT SELECT ON dkd.tb_inventory          TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_inventory_log      TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_order              TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_task               TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_task_details       TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_task_type          TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_channel            TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_sku                TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_sku_class          TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_vending_machine    TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_vm_type            TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_node               TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_region             TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_emp                TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_job                TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_policy             TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT ON dkd.tb_partner            TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';

-- 4) 智能体自有表：可读可写，但**不含 DELETE**（软删除，AGENTS §7.4）
GRANT SELECT, INSERT, UPDATE ON dkd.agent_conversation  TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT, INSERT, UPDATE ON dkd.agent_message       TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT, INSERT, UPDATE ON dkd.agent_decision_log  TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
GRANT SELECT, INSERT, UPDATE ON dkd.agent_restock_plan  TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';
-- 2026-09-21 追加（排期 1-7）：自动分析暂停开关（单行表，DDL 见 agent_restock_pause.sql）
GRANT SELECT, INSERT, UPDATE ON dkd.agent_restock_pause TO 'dkd_agent'@'127.0.0.1', 'dkd_agent'@'localhost';

FLUSH PRIVILEGES;

-- ----------------------------------------------------------------------------
-- 验证矩阵（执行后必须逐条核对，AGENTS §8 要求覆盖拒绝路径）
-- 期望：1/2 成功，3~7 全部被拒绝
-- ----------------------------------------------------------------------------
-- 1) 允许：读白名单业务表
--    SELECT COUNT(*) FROM dkd.tb_inventory;
-- 2) 允许：写智能体自有表
--    INSERT INTO dkd.agent_decision_log (scene, action, result) VALUES (1,'verify',1);
-- 3) 拒绝（1142）：写业务表
--    INSERT INTO dkd.tb_inventory (vm_id, sku_id, channel_id) VALUES (0,0,0);
-- 4) 拒绝（1142）：读非白名单表
--    SELECT COUNT(*) FROM dkd.tb_report;
-- 5) 拒绝（1142）：读系统表
--    SELECT COUNT(*) FROM dkd.sys_user;
-- 6) 拒绝（1142）：跨库读（本机还有 8 个其他项目库）
--    SELECT COUNT(*) FROM test.users;
-- 7) 拒绝（1142）：删除（软删除红线）
--    DELETE FROM dkd.agent_decision_log WHERE id > 0;
-- 8) 拒绝（1142）：DDL
--    DROP TABLE dkd.agent_decision_log;
-- 9) 已实测（2026-09-21，排期 1-7）：agent_restock_pause 可 UPSERT；`DELETE FROM dkd.agent_restock_pause`
--    被拒（ERROR 1142），与 7) 同一红线
--
-- 权限自查：
--   SHOW GRANTS FOR 'dkd_agent'@'127.0.0.1';

-- ----------------------------------------------------------------------------
-- 回滚
-- ----------------------------------------------------------------------------
-- DROP USER IF EXISTS 'dkd_agent'@'127.0.0.1';
-- DROP USER IF EXISTS 'dkd_agent'@'localhost';
