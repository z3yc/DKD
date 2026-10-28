-- ============================================================================
-- agent_message 限额/报表查询索引（P1-12）
--
-- 背景：app/audit.py 的 enforce_daily_quota 在**每次对话请求前**触发 1~2 次
--       ``WHERE create_time >= now()-1d AND del_flag='0'`` 的窗口聚合
--       （per-user + global 各一次，COUNT + SUM）；app/api/ops.py 的
--       ``/agents/usage/summary`` 报表也是同一形状。agent_message 的
--       create_time 原先无索引 → 每请求两次近全表扫描，高频对话时熔断检查
--       自己变成热点（docs/DKD系统性梳理-2026-10-28.md §五 P1-12）。
--
-- 配套改造（代码侧，同分支提交）：
--   1. per-user 查询保持每请求精确执行 → 走 (user_id, create_time) 复合索引；
--   2. global 查询加 30s 进程内 TTL 缓存（app/audit.py::_global_usage）
--      → 每 30s 至多 1 次，走 create_time 单列索引。
--
-- 执行环境：agent 库（MySQL 8.0，即 dkd-agent 的自有审计库，非业务 dkd 库）。
--           索引选择度说明：user_id 取值少（运营人员数量级），复合索引仍能把
--           范围条件收窄到单用户的近期行；global 查询靠 (2) 的单列索引。
-- 大表请在低峰执行（MySQL 8 在线 DDL 默认 INPLACE；行数 > 百万用 pt-osc/gh-ost）。
-- 回滚：见文件末尾（逐条对应）。
-- ============================================================================

-- 1) per-user 限额查询：WHERE user_id = ? AND create_time >= now()-1d
ALTER TABLE agent_message ADD INDEX idx_agent_message_user_time (user_id, create_time);

-- 2) global 限额/用量报表查询：WHERE create_time >= now()-1d
ALTER TABLE agent_message ADD INDEX idx_agent_message_create_time (create_time);

-- ============================================================================
-- 回滚（逐条对应上方变更）：
-- ALTER TABLE agent_message DROP INDEX idx_agent_message_user_time;
-- ALTER TABLE agent_message DROP INDEX idx_agent_message_create_time;
-- ============================================================================
