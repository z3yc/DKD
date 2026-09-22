-- ============================================================================
-- 索引变更建议（Phase 1 / 任务 1-2）
--
-- 状态：**待评审，尚未执行**（AGENTS §2.4：唯一约束与索引变更必须附 DDL 文件并经评审）。
-- 依据：docs/ddl/business_tables_survey.md §2（EXPLAIN 量化证据：同一查询在
--       “无索引”与“复合索引 + 范围比较”下预估扫描行数 199430 → 2）。
-- 执行环境：dkd 库（MySQL 8.0）。**大表请在低峰执行**（MySQL 8 的在线 DDL 默认
--       INPLACE + 不锁表，但会占用 IO；行数 > 百万且业务敏感时用 pt-osc/gh-ost）。
-- 回滚：见文件末尾（逐条对应）。
-- ============================================================================

-- 1) tb_order：近 N 天按设备/货道聚合（任务 1-2）、异常订单检测（任务 2-1）
--    为什么是 (inner_code, create_time) 而不是两个单列索引：
--    查询固定按 inner_code 等值/IN 过滤、再按 create_time 做范围，复合索引可以让
--    范围条件也走索引（实测 key_len 69 含 create_time，预估 rows=2）；拆成单列则
--    只能用到其中一列，仍要回表过滤。
ALTER TABLE tb_order ADD INDEX idx_order_inner_code_create_time (inner_code, create_time);

-- 2) tb_order：全局按状态的时间窗统计（报表/看板口径，与 (1) 互补）
ALTER TABLE tb_order ADD INDEX idx_order_status_create_time (status, create_time);

-- 3) tb_task：在途补货工单查询（任务 1-2 去重）/ 防重复建单校验
ALTER TABLE tb_task ADD INDEX idx_task_inner_status_type (inner_code, task_status, product_type_id);

-- 4) tb_inventory_log：库存异常变动检测（任务 2-1，Phase 2 使用，可提前建）
--    ⚠️ 该表不在本机 dkd 库的既有 DDL 归档中，执行前先用
--       SHOW COLUMNS FROM tb_inventory_log 确认列名（探查命令见勘查记录 §四）。
-- ALTER TABLE tb_inventory_log ADD INDEX idx_inventory_log_vm_create_time (vm_id, create_time);

-- ----------------------------------------------------------------------------
-- 验证（执行后逐条核对）
-- ----------------------------------------------------------------------------
-- SHOW INDEX FROM tb_order;
-- SHOW INDEX FROM tb_task;
-- 预期：EXPLAIN 的 key 命中新索引、rows 显著下降
-- EXPLAIN SELECT inner_code, channel_code, count(*), sum(amount) FROM tb_order
--   WHERE inner_code IN ('A1000001') AND create_time >= '2023-09-01'
--     AND create_time < '2023-09-16' AND status = 2
--   GROUP BY inner_code, channel_code LIMIT 2000\G

-- ----------------------------------------------------------------------------
-- 回滚（逐条对应，可单独执行）
-- ----------------------------------------------------------------------------
-- ALTER TABLE tb_order DROP INDEX idx_order_inner_code_create_time;
-- ALTER TABLE tb_order DROP INDEX idx_order_status_create_time;
-- ALTER TABLE tb_task  DROP INDEX idx_task_inner_status_type;
-- ALTER TABLE tb_inventory_log DROP INDEX idx_inventory_log_vm_create_time;
