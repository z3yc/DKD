-- ============================================================================
-- agent_restock_plan.status 注释变更 · 新增取值 7-建单中（排期任务 1-8）
-- 归档：docs/ddl/agent_restock_plan_status_ordering.sql   创建日期：2026-09-21
-- 执行环境：本机 dkd 库（MySQL 8.0）；生产需按 AGENTS §8 走 DDL 评审 + 低峰执行
-- 幂等：是（重复执行只是把同一 COMMENT 再写一次；MODIFY COLUMN 不触碰数据）
-- 回滚：见文件末尾「回滚语句」
-- 变更类型：**仅列注释（COMMENT）**，不改类型/索引/约束/默认值
-- ----------------------------------------------------------------------------
-- 为什么需要这个取值（而不是“并发靠应用层锁”）：
--   确认建单 = 先读计划 → 回调 Java 建单 → 回写计划状态，三步非幂等。
--   唯一键 uk_agent_restock_plan_vm_date 约束的是「计划」（同一设备同一天一份），
--   而重复建单产生的是**两张工单**——唯一键拦不住它。
--   进程内锁也拦不住：本项目 3-9 演练就包含“Python 宕机重启”，1-9 的定时任务与
--   1-7 的人工确认又是两条独立入口，内存锁在多进程/重启后形同不存在。
--   因此把“谁正在建单”写进本行状态，用条件 UPDATE（CAS）做 DB 级排他：
--
--     UPDATE agent_restock_plan SET status = 7, update_by = ?, update_time = now()
--      WHERE vm_id = ? AND plan_date = ? AND del_flag = '0' AND status = <期望状态>;
--     -- 影响行数 1 → 我抢到了，去回调 Java；0 → 别人在抢或状态已变，返回 409
--
--   status 取值与状态机（与 docs/ddl/agent_tables.sql 顶部注释一致）：
--     1-建议 2-已调整 3-已跳过 4-已建单 5-已复盘 6-待指派 7-建单中
--     1/2/6 → 7（CAS 抢占）→ 4（建单成功）；失败时持有者 CAS 释放回原状态。
--     7 期间人工操作（确认/调整/跳过/指派/恢复）一律拒绝，避免“边建单边改数量”。
--
-- 风险与运维注意：
--   * 7 是**瞬时状态**（正常在 10s 回调超时内结束）。若进程在回调中途被杀，
--     该行会停在 7：这是**有意的保守行为**——此时 Java 侧可能已建单，
--     自动回退成 1/2 会造成运营重复点一次而双建单。
--     处置：人工核对 tb_task（该 inner_code + product_type_id=2 + status in 1,2）
--     后，再决定释放（回 1-建议）还是补写 task_id（置 4-已建单）。已登记 3-9 演练项。
-- ============================================================================

-- 执行前自查：确认当前注释与取值分布（应无 7 的行）
SELECT COUNT(*) AS rows_with_status_7 FROM agent_restock_plan WHERE status = 7;

ALTER TABLE `agent_restock_plan`
  MODIFY COLUMN `status` TINYINT NOT NULL DEFAULT 1
  COMMENT '状态：1-建议 2-已调整 3-已跳过 4-已建单 5-已复盘 6-待指派 7-建单中（建单占位，1-8 乐观锁）';

-- 执行后验证（注释与索引均应与预期一致）
-- SHOW FULL COLUMNS FROM agent_restock_plan LIKE 'status';
-- SELECT status, COUNT(*) FROM agent_restock_plan GROUP BY status ORDER BY status;
-- SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS
--   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_restock_plan'
--   ORDER BY INDEX_NAME, SEQ_IN_INDEX;

-- ----------------------------------------------------------------------------
-- 回滚语句（语义回滚：7 是应用层写入的，回滚前必须先把可能存在的 7 行处理掉）
-- ----------------------------------------------------------------------------
-- ① 先把残留的 7 行按其真实业务含义复位（谨慎：先查 tb_task 确认是否已建单）：
--      -- 已建单的（Java 侧已存在在途工单）→ 4-已建单
--      UPDATE agent_restock_plan p
--        JOIN tb_task t ON t.inner_code = p.inner_code AND t.product_type_id = 2
--         SET p.status = 4
--       WHERE p.status = 7 AND t.task_status IN (1, 2) AND p.del_flag = '0';
--      -- 未建单的 → 1-建议（需人工重新确认）
--      UPDATE agent_restock_plan SET status = 1, update_time = now()
--       WHERE status = 7 AND del_flag = '0';
-- ② 再回滚注释（列类型/约束本来就没变，所以这是一次幂等的 MODIFY）：
--      ALTER TABLE `agent_restock_plan`
--        MODIFY COLUMN `status` TINYINT NOT NULL DEFAULT 1
--        COMMENT '状态：1-建议 2-已调整 3-已跳过 4-已建单 5-已复盘 6-待指派';
-- ③ 代码回滚需同步退掉 CAS：
--      app/graphs/restock_state.py::PLAN_STATUS_ORDERING 与 ALLOWED_TRANSITIONS 的 7 项；
--      app/graphs/restock_plan_store.py::claim_for_order / release_order_claim 的调用点；
--      否则应用会继续写 7，而注释已不认识它（注释与实际脱节比没有注释更糟）。
-- ============================================================================
