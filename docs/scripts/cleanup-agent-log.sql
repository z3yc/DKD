-- ============================================================================
-- agent_log · 30 天保留期清理模板（排期 2-4 交付；**本文件不会被 Python 自动执行**）
-- ----------------------------------------------------------------------------
-- 执行环境：MySQL 8.0（本机 dkd 库已建表，DDL 见 docs/ddl/agent_config.sql）
-- 执行者  ：Java 服务账号 / 运维 DBA —— **不是 Python 服务**
--
-- ⚠️ 权限前提（不可绕过，AGENTS §7.3）：
--   dkd-agent 使用的 `dkd_agent` 账号对 agent_log **只被授予 SELECT + INSERT**，
--   DELETE 会被 MySQL 拒绝（ERROR 1142）。这是有意为之：保留期清理是运维动作，
--   不是 Agent 的运行时职责。因此本脚本只能由 Java 侧定时任务或运维手工执行。
--   Python 侧配置 `DKD_AGENT_LOG_RETENTION_DAYS`（默认 30）仅作为**口径声明**，
--   不产生任何 DELETE。
--
-- ⚠️ 为什么不放在应用启动时删：
--   ① Agent 账号无权限；② 大表一次性 DELETE 会长时间持锁、撑大 undo、污染 buffer pool，
--   并可能拉大主从延迟；③ 启动期删数据会让“服务启动慢”与“库不可用”混在一起。
--
-- 策略：按 create_time < NOW() - INTERVAL 30 DAY **分批**删除（默认每批 5000 行），
--       循环至影响行数为 0；优先安排在业务低峰窗口。
--       查询条件只命中索引列 create_time（idx_agent_log_create_time），
--       禁止 `date_format(create_time, ...)` 这类包裹索引列的写法。
--
-- 回滚：DELETE 是物理删除，**不可回滚**。执行前必须先备份（见「回滚/备份」段）。
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0) 前置：确认库名/账号/保留天数（按需修改）
-- ----------------------------------------------------------------------------
-- USE dkd;
-- SET @retention_days = 30;

-- 本机核对表是否就绪（无表时先按 docs/ddl/agent_config.sql 建表）
-- SHOW TABLES LIKE 'agent_log';
-- SHOW CREATE TABLE agent_log\G


-- ----------------------------------------------------------------------------
-- 1) 预检：统计待清理规模（先看量，再决定批大小与时间窗口）
--    建议 > 100 万行时：① 批大小降到 2000~5000；② 拆多个低峰窗口执行；③ 评估加分区。
-- ----------------------------------------------------------------------------
SELECT COUNT(*) AS expired_rows
  FROM agent_log
 WHERE create_time < DATE_SUB(NOW(), INTERVAL 30 DAY);

-- 边界抽查（确认没有把“未来时间/时钟漂移”写进来的行误删）
SELECT MIN(create_time) AS oldest_time, MAX(create_time) AS newest_time
  FROM agent_log;


-- ----------------------------------------------------------------------------
-- 2) 备份：先导出一份可回灌的副本（二选一）
-- ----------------------------------------------------------------------------
-- 2.1 mysqldump（推荐；整表备份，可完整回灌）
--   mysqldump -h<host> -u<backup_user> -p --single-transaction \
--     --default-character-set=utf8mb4 dkd agent_log > backup_agent_log_$(date +%F).sql
--
-- 2.2 或只备份即将删除的行到影子表（节省空间；回灌时 INSERT 回 agent_log）
--   CREATE TABLE IF NOT EXISTS agent_log_bak_$(date +%Y%m) LIKE agent_log;
--   INSERT INTO agent_log_bak_$(date +%Y%m)
--     SELECT * FROM agent_log
--      WHERE create_time < DATE_SUB(NOW(), INTERVAL 30 DAY)
--      LIMIT 5000;   -- 与删除同样的分批节奏，反复执行直至 0 行


-- ----------------------------------------------------------------------------
-- 3) 分批删除（**循环执行**，直到 affected_rows = 0；建议用 Java 定时任务驱动）
--
--    Java/MyBatis 形态：每轮执行下面的 DELETE，拿 update 返回的影响行数，
--    rows = 0 时结束；轮次之间 sleep 200ms~1s，避免持续打满 IO。
--    注意：DELETE ... ORDER BY create_time LIMIT n 在部分 MySQL 版本会给出
--    “不支持 ORDER BY 的 DELETE”告警；本库为 MySQL 8.0，可正常执行。
--    若运维偏好保守写法，可省去 ORDER BY（仍走 create_time 范围扫描）。
-- ----------------------------------------------------------------------------
DELETE FROM agent_log
 WHERE create_time < DATE_SUB(NOW(), INTERVAL 30 DAY)
 ORDER BY create_time
 LIMIT 5000;

-- 核对是否删干净（应为 0）
SELECT COUNT(*) AS remaining_expired
  FROM agent_log
 WHERE create_time < DATE_SUB(NOW(), INTERVAL 30 DAY);


-- ----------------------------------------------------------------------------
-- 4) 表空间收尾（可选、独立窗口执行）
--    InnoDB 的 DELETE 只释放行标记，不把空间还给 OS；若表长期只增不减，
--    评估 `OPTIMIZE TABLE agent_log`（**会重建表并持锁**，必须低峰执行、先备份）
--    或改为按月 RANGE PARTITION：保留期到了 DROP PARTITION，代价远低于大批量 DELETE。
--    本期不做分区（排期 4-4 再评估），这里只登记风险与备选。
-- ----------------------------------------------------------------------------
-- OPTIMIZE TABLE agent_log;
-- ALTER TABLE agent_log RENAME TO agent_log_truncated;
-- CREATE TABLE agent_log LIKE agent_log_truncated;   -- 重建空表，再按保留边界回灌


-- ----------------------------------------------------------------------------
-- 5) 回滚 / 事故处理
-- ----------------------------------------------------------------------------
--   · DELETE 无法回滚：唯一恢复途径是用第 2 步的备份回灌。
--   · 误删恢复（mysqldump 形态）：
--       mysql -h<host> -u<root> -p --default-character-set=utf8mb4 dkd \
--         < backup_agent_log_<日期>.sql
--     若表中已有新数据，回灌前先确认主键 id 不冲突（id 为自增，回灌旧 id 需显式
--     写入并调整 AUTO_INCREMENT；建议回灌到临时表后按 create_time 范围补写）。
--   · 杀循环：若 DELETE 长时间不返回，终止对应会话（KILL <connection_id>）并先
--     提交/回滚该事务；分批策略下每批已提交，不会出现“删一半”的不一致状态。
--   · 主从延迟：存在只读库时监控 Seconds_Behind_Master，超过阈值暂停后续批次。


-- ============================================================================
-- 变更记录
-- ----------------------------------------------------------------------------
-- 2026-11-04（排期 2-4）：初版。只提供模板与说明，不执行、不连库；
--   执行入口属排期 4-4（保留清理），需 Java/运维在真实环境按本文件操作。
-- ============================================================================
