-- ============================================================================
-- DKD 业务清晰化 · Phase B：数据修正 + 约束收口
--
-- 目的：把《DKD业务规范-清晰版》§1/§3/§4 的口径落到数据库：
--   1) 库存台账与货道缓存对齐（sku/容量/数量），并补一笔调整流水；
--   2) 清理历史重复活跃工单（同设备同类型只保留最新一张）；
--   3) 库存唯一键从 (vm_id,sku_id) 改为 (vm_id,channel_id)（按货道建库存）；
--   4) 活跃工单唯一索引（生成列方案，MySQL 无部分索引）；
--   5) 报表定时任务恢复为"正常"（历史初始化写成暂停，装完永不执行）；
--   6) 补货菜单权限点与网关对齐（manage:restock:* → manage:task:*）。
--
-- 状态：**未执行**。执行环境：MySQL 8.0.40，库 dkd；需要 root（dkd_agent 只有只读权限）。
-- 前置：① 备份（见下）；② 预检全部通过/差异已人工确认；③ 与代码版本 v0.3.0+ 配套。
-- 回滚：见文件末尾（DDL 可逆；数据以备份为准）。
--
-- 备份（在库外执行）：
--   mysqldump -uroot -p dkd tb_inventory tb_inventory_log tb_task tb_task_details tb_channel sys_job sys_menu > dkd_before_normalize.sql
-- ============================================================================

SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- 第 0 步：预检（只读，必须逐条确认）
-- ----------------------------------------------------------------------------
SELECT '0.1 重复活跃工单（预期：仅 QK8yhIRL/2 一组 4 条）；保留 max(task_id)=566' AS check_item;
SELECT inner_code, product_type_id, COUNT(*) AS dup_count,
       GROUP_CONCAT(task_id ORDER BY task_id) AS task_ids,
       MAX(task_id) AS keep_task_id
  FROM tb_task
 WHERE task_status IN (1, 2)
 GROUP BY inner_code, product_type_id
HAVING dup_count > 1;

SELECT '0.2 库存台账与货道的不一致（预期 3 行；sku/容量/数量均可能不同）' AS check_item;
SELECT i.id, i.vm_id, i.channel_id, i.sku_id AS inv_sku, c.sku_id AS ch_sku,
       i.current_stock, c.current_capacity, i.max_stock, c.max_capacity
  FROM tb_inventory i
  JOIN tb_channel c ON c.id = i.channel_id
 WHERE i.sku_id <> c.sku_id
    OR i.max_stock <> c.max_capacity
    OR i.current_stock <> c.current_capacity;

SELECT '0.2b 空货道（sku_id=0/NULL）却仍有库存行（预期 1 行：channel 4908；按清零处理，需人工确认是否删行）' AS check_item;
SELECT i.id, i.vm_id, i.channel_id, i.sku_id, i.current_stock, c.sku_id AS ch_sku, c.current_capacity
  FROM tb_inventory i JOIN tb_channel c ON c.id = i.channel_id
 WHERE (c.sku_id IS NULL OR c.sku_id = 0);

SELECT '0.3 无法关联货道的库存行（预期 0；非 0 需人工处理后再继续）' AS check_item;
SELECT i.id, i.vm_id, i.channel_id
  FROM tb_inventory i LEFT JOIN tb_channel c ON c.id = i.channel_id
 WHERE c.id IS NULL;

SELECT '0.4 同一货道重复库存行（预期 0；非 0 会阻塞 uk_vm_channel）' AS check_item;
SELECT vm_id, channel_id, COUNT(*) c FROM tb_inventory GROUP BY vm_id, channel_id HAVING c > 1;

SELECT '0.5 报表任务当前状态（100/101 预期为 1=暂停，需改为 0）' AS check_item;
SELECT job_id, job_name, cron_expression, status FROM sys_job WHERE job_id IN (100, 101);

-- ⚠️ 若 0.3/0.4 返回行，或 0.1 的保留策略需业务确认（默认保留最新），
--    先停下来处理，不要继续往下执行。

-- ----------------------------------------------------------------------------
-- 第 1 步：数据修正（可重复执行；每步独立）
-- ----------------------------------------------------------------------------

-- 1.1 先快照"修正前"的库存行（临时表，仅本会话可见；用于 1.2 精确补流水）
CREATE TEMPORARY TABLE tmp_inv_before AS
SELECT i.id, i.vm_id, i.sku_id, i.channel_id, i.current_stock, i.max_stock,
       c.sku_id AS ch_sku, c.max_capacity AS ch_max, c.current_capacity AS ch_current
  FROM tb_inventory i
  JOIN tb_channel c ON c.id = i.channel_id;

-- 1.2 库存台账对齐货道：sku 与容量以货道为准，数量以"现场缓存"为准（等价于盘一次点）。
--     空货道（sku_id=0/NULL）特殊处理：货道已不卖货，台账数量清零且不告警；
--     这类行通常应人工确认后删除，脚本按"清零 + 不进 is_alert"的保守方式处理（保留可追溯流水）。
UPDATE tb_inventory i
  JOIN tb_channel c ON c.id = i.channel_id
   SET i.sku_id        = c.sku_id,
       i.max_stock     = c.max_capacity,
       i.current_stock = CASE
                           WHEN c.sku_id IS NULL OR c.sku_id = 0 THEN 0
                           ELSE LEAST(c.current_capacity, c.max_capacity)
                         END,
       i.is_alert      = CASE
                           WHEN c.sku_id IS NULL OR c.sku_id = 0 THEN 0
                           WHEN LEAST(c.current_capacity, c.max_capacity) <= i.min_stock THEN 1
                           ELSE 0
                         END,
       i.update_time   = NOW()
 WHERE i.sku_id <> c.sku_id
    OR i.max_stock <> c.max_capacity
    OR i.current_stock <> CASE
                            WHEN c.sku_id IS NULL OR c.sku_id = 0 THEN 0
                            ELSE LEAST(c.current_capacity, c.max_capacity)
                          END;

-- 1.3 为 1.2 真正改动过的行补一笔"调整"流水（可追溯；重跑不会重复插入）
INSERT INTO tb_inventory_log
  (vm_id, sku_id, channel_id, change_type, change_amount, before_stock, after_stock,
   business_type, operator_id, operator_name, create_time, remark)
SELECT b.vm_id, i.sku_id, b.channel_id, 4,
       (i.current_stock - b.current_stock),
       b.current_stock, i.current_stock,
       'MIGRATION', 0, 'system:migration', NOW(),
       'Phase B 库存台账对齐货道（change_amount=对齐后-对齐前；空货道按 0 计）'
  FROM tmp_inv_before b
  JOIN tb_inventory i ON i.id = b.id
 WHERE i.sku_id <> b.sku_id
    OR i.max_stock <> b.max_stock
    OR i.current_stock <> b.current_stock;

-- 1.4 删除历史重复活跃工单：同设备同类型只保留 task_id 最大的一张（默认策略，需业务确认）
UPDATE tb_task t
  JOIN (
        SELECT inner_code, product_type_id, MAX(task_id) AS keep_id
          FROM tb_task
         WHERE task_status IN (1, 2)
         GROUP BY inner_code, product_type_id
         HAVING COUNT(*) > 1
       ) k
    ON t.inner_code = k.inner_code
   AND t.product_type_id = k.product_type_id
   SET t.task_status = 3,
       t.update_time = NOW(),
       t.desc = CONCAT(IFNULL(t.desc, ''), ' [MIGRATION:重复活跃工单清理，保留 task_id=', k.keep_id, ']')
 WHERE t.task_status IN (1, 2)
   AND t.task_id <> k.keep_id;

-- 1.5 报表任务恢复为正常（0=正常，1=暂停；历史初始化写错导致装完永不执行）
UPDATE sys_job SET status = '0', update_time = NOW() WHERE job_id IN (100, 101);

-- 1.6 补货菜单权限点与网关对齐（网关判 manage:task:list / manage:task:add）
UPDATE sys_menu SET perms = 'manage:task:list', update_time = NOW()
 WHERE perms IN ('manage:restock:list');
UPDATE sys_menu SET perms = 'manage:task:add', update_time = NOW()
 WHERE perms IN ('manage:restock:confirm', 'manage:restock:adjust',
                 'manage:restock:skip', 'manage:restock:pause');

-- ----------------------------------------------------------------------------
-- 第 2 步：约束收口（DDL，可逆）
-- ----------------------------------------------------------------------------

-- 2.1 库存按货道唯一：先加新键，再删旧键（顺序不可反，否则中间态无唯一约束）
ALTER TABLE tb_inventory
  ADD UNIQUE KEY uk_vm_channel (vm_id, channel_id);
ALTER TABLE tb_inventory
  DROP INDEX uk_vm_sku;

-- 2.2 活跃工单唯一：生成列在活跃时为 "inner_code:product_type_id"，非活跃为 NULL，
--     利用"唯一索引允许多个 NULL"实现"仅对未完成工单唯一"。
ALTER TABLE tb_task
  ADD COLUMN active_dedup_key VARCHAR(64)
    GENERATED ALWAYS AS (
      CASE
        WHEN task_status IN (1, 2)
          THEN CONCAT(inner_code, ':', IFNULL(product_type_id, 0))
        ELSE NULL
      END
    ) STORED
    COMMENT '仅对未完成工单非空：用于唯一约束实现"同设备同类型至多一张活跃工单"';

CREATE UNIQUE INDEX uk_task_active_dedup ON tb_task (active_dedup_key);

-- ----------------------------------------------------------------------------
-- 第 3 步：验证（全部应为 0 / 符合预期）
-- ----------------------------------------------------------------------------
SELECT '3.1 重复活跃工单（应 0 行）' AS check_item;
SELECT inner_code, product_type_id, COUNT(*) c FROM tb_task
 WHERE task_status IN (1, 2) GROUP BY inner_code, product_type_id HAVING c > 1;

SELECT '3.2 库存/货道 sku 与容量不一致（应 0 行）' AS check_item;
SELECT i.id FROM tb_inventory i JOIN tb_channel c ON c.id = i.channel_id
 WHERE i.sku_id <> c.sku_id OR i.max_stock <> c.max_capacity;

SELECT '3.3 库存/货道数量差异（应为 0；仓外销售未对账时允许有差异，需能解释）' AS check_item;
SELECT i.id, i.current_stock, c.current_capacity
  FROM tb_inventory i JOIN tb_channel c ON c.id = i.channel_id
 WHERE i.current_stock <> c.current_capacity;

SELECT '3.4 索引状态' AS check_item;
SHOW INDEX FROM tb_inventory;
SHOW INDEX FROM tb_task;

SELECT '3.5 报表任务状态（应均为 0）' AS check_item;
SELECT job_id, job_name, status FROM sys_job WHERE job_id IN (100, 101);

SELECT '3.6 本次迁移流水（应只对应 1.1 改过的行）' AS check_item;
SELECT * FROM tb_inventory_log WHERE business_type = 'MIGRATION' ORDER BY id DESC;

-- ----------------------------------------------------------------------------
-- 第 4 步：回滚（按需执行；数据回滚以备份为准）
-- ----------------------------------------------------------------------------
-- 4.1 DDL 回滚
-- ALTER TABLE tb_task DROP INDEX uk_task_active_dedup;
-- ALTER TABLE tb_task DROP COLUMN active_dedup_key;
-- ALTER TABLE tb_inventory ADD UNIQUE KEY uk_vm_sku (vm_id, sku_id);   -- 若回滚前已出现同 vm+sku 多行会失败
-- ALTER TABLE tb_inventory DROP INDEX uk_vm_channel;
--
-- 4.2 数据回滚
-- 库存/流水：以备份表为准（建议备份时同时建 snapshot 表，而不是只留 dump）：
--   CREATE TABLE tb_inventory_bak_20261028 AS SELECT * FROM tb_inventory;
--   CREATE TABLE tb_task_bak_20261028 AS SELECT * FROM tb_task;
--   CREATE TABLE tb_inventory_log_bak_20261028 AS SELECT * FROM tb_inventory_log;
--   CREATE TABLE sys_job_bak_20261028 AS SELECT * FROM sys_job;
--   CREATE TABLE sys_menu_bak_20261028 AS SELECT * FROM sys_menu;
--   -- 确认后回滚：
--   -- TRUNCATE tb_inventory; INSERT INTO tb_inventory SELECT * FROM tb_inventory_bak_20261028;
--   -- UPDATE tb_task t JOIN tb_task_bak_20261028 b ON b.task_id=t.task_id
--   --    SET t.task_status=b.task_status, t.desc=b.desc, t.update_time=b.update_time;
--   -- UPDATE sys_job j JOIN sys_job_bak_20261028 b ON b.job_id=j.job_id SET j.status=b.status;
--   -- UPDATE sys_menu m JOIN sys_menu_bak_20261028 b ON b.menu_id=m.menu_id SET m.perms=b.perms;
--
-- 4.3 代码回滚：`git revert <本次提交>`（代码与 DDL 配套，二者不可只回滚一边）
-- ============================================================================
