-- =============================================================================
-- 智能体补货分析定时任务注册（排期任务 1-9）
-- -----------------------------------------------------------------------------
-- 执行环境 : dkd 库（Java 管理端使用的库；RuoYi 的 sys_job 表在 dkd-parent/sql/ry_20231130.sql 中创建）
-- 执行方式 : ① 推荐——在管理端「系统监控 → 定时任务」新增，避免手工写 sys_job 写错 cron；
--            ② 或直接执行本文件（幂等：job_id=102 已存在时先删后插）。
-- 执行记录 : **本机已执行（2026-09-28，dkd 库 @ 127.0.0.1）**：首次执行后校验查询返回 1 行（
--            job_id=102 / '智能补货分析' / agentTask.restockAnalyze() / '0 0 6 * * ?' /
--            misfire_policy=2 / concurrent=1 / status=0）；**幂等重跑退出码 0 且仍为 1 行**。
--            **生产待执行**（上线时按 §3 验证语句自查；未执行前 06:00 不会自动分析）。
-- 幂等     : 是（本文件先 DELETE 同 job_id 再 INSERT；重复执行结果一致）
-- 回滚     : 见文件末尾「回滚语句」
-- 影响面   : 仅新增一条定时任务定义，**不触碰任何业务表**。
--            任务执行时由 Java 调用 Python `POST /agents/restock/analyze`；
--            停用方式：把 sys_job.status 置 '1'（暂停）或直接删除该行，无需停服务。
-- 前置条件 : ① `agent.enabled=true`；② Java 与 Python 两侧 `DKD_AGENT_SERVICE_SECRET` 一致；
--            ③ Python 服务已启动（不可用时本任务会重试并在 sys_job_log 留下失败记录）
-- =============================================================================

-- 1) 定时任务定义：每日 06:00 触发补货分析
--    先删同 job_id 再插：sys_job 的主键是 (job_id, job_name, job_group)，
--    用 REPLACE/无脑 INSERT 在本机重跑会报 1062；先删后插让本文件可重复执行。
--    此处 DELETE 的是**调度配置行**而非业务数据（sys_job 无 del_flag 列，RuoYi 的
--    「删除任务」本身就是物理删除），不属 AGENTS §7.4 软删红线的对象。
--    invoke_target 必须与 dkd-quartz 中的 bean 名 + 方法名一致：@Component("agentTask") + restockAnalyze()
DELETE FROM sys_job WHERE job_id = 102;

INSERT INTO sys_job
  (job_id, job_name, job_group, invoke_target, cron_expression, misfire_policy, concurrent, status,
   create_by, create_time, update_by, update_time, remark)
VALUES
  (102, '智能补货分析', 'DEFAULT', 'agentTask.restockAnalyze()', '0 0 6 * * ?', '2', '1', '0',
   'admin', SYSDATE(), '', NULL,
   '每日06:00触发智能体补货分析（/agents/restock/analyze）；分析在人工确认处中断，计划已落 agent_restock_plan');

-- 说明（为什么是这几个值，而不是照抄 RuoYi 示例）：
--   concurrent      = '1' 禁止并发（QuartzDisallowConcurrentExecution）。
--                     一次分析要读全量设备/订单，跑两遍只是重复烧 token 且互相抢同一主库；
--                     这也是排期 1-9「任务重入安全」的运行时保障（应用层则靠
--                     agent_restock_plan 的 (vm_id, plan_date) 唯一键 + 幂等 upsert）。
--   misfire_policy  = '2' 错过后立即补执行一次（FireAndProceed）。
--                     06:00 时 Java 正在重启/发布时不应“今天干脆不分析了”——
--                     早上运营上班要看到建议。不用 '1'（IgnoreMisfires）：停机 3 天会在启动瞬间
--                     连跑 3 次（每次真实调用 LLM 校准，产生费用）；'2' 只补一次。
--   status          = '0' 正常（注意 RuoYi 示例任务的 '1' 是**暂停**，照抄会让任务永远不跑）。
--   cron            = '0 0 6 * * ?' 每日 06:00（Windows/Linux 均为 Quartz 6 段式，含秒）。

-- 2) 执行后校验（应返回 1 行）
SELECT job_id, job_name, invoke_target, cron_expression, misfire_policy, concurrent, status
  FROM sys_job
 WHERE job_id = 102;

-- 3) 运行后可查看作业日志（status：0-成功 1-失败；失败时 exception_info 含中文原因与 requestId）
-- SELECT job_name, status, job_message, exception_info, create_time
--   FROM sys_job_log
--  WHERE job_name = '智能补货分析'
--  ORDER BY job_log_id DESC
--  LIMIT 5;

-- =============================================================================
-- 回滚语句（停用本任务，不影响已生成的补货计划数据）
-- =============================================================================
-- 方式 A（推荐，保留任务定义便于再启用）：把状态置为暂停
--   UPDATE sys_job SET status = '1', update_by = 'admin', update_time = SYSDATE() WHERE job_id = 102;
-- 方式 B（彻底移除任务定义）：
--   DELETE FROM sys_job WHERE job_id = 102;
-- 注意：两种方式都**只作用于任务调度**，不会删除 agent_restock_plan /
--       agent_decision_log 中的历史数据（审计与复盘数据必须保留）。
-- =============================================================================
