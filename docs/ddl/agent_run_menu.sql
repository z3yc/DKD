-- =============================================================================
-- 运行时间线 · 页面菜单（排期任务 F-04 / M1）
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式：
--   mysql -h127.0.0.1 -uroot -p dkd < agent_run_menu.sql
-- 前置条件：agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在），
--           agent_audit_menu.sql 已执行（9007~9009 已被决策审计占用）。
-- 幂等性  ：非幂等（menu_id 主键冲突会报 1062，这是有意的——
--           静默跳过会让“菜单没生效”查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么复用 manage:agentAudit:list 而不新增权限点：
--   1) 本页与「决策审计」(9007) 是同一份数据的两个视角：审计看“决策”粒度
--      （agent_decision_log：谁在什么时候点了什么），时间线看“过程”粒度
--      （agent_run/step/tool_call：每轮派给谁、调了哪个工具、几行、多久），
--      两者用 run_id 关联（见 docs/ddl/agent_run_step.sql 文件头）。
--      能看审计的人就应该能看时间线；拆成两个权限点，实际效果只是多一次授权配置
--      和一处必然漏配（当前没有任何角色被单独授予时间线权限）。
--   2) 后端契约已定：AgentRunController 的三个接口复用
--      manage:agentAudit:list / manage:agentAudit:query
--      （docs/agent-fault-scenario-plan-2026-10-26.md F-03）。
--      前端菜单若用新权限点，会出现“菜单看得见、接口 403”的错位。
--   3) RuoYi 动态菜单按本行 perms 做前端按钮/路由权限判断；复用现有权限点即
--      不需要新增 menu_type='F' 的按钮权限，菜单行数保持 1 行，回滚面最小。
--
-- 为什么 menu_id 用 9010：
--   9000「AI 智能体」目录由 agent_restock_menu.sql 建好，其子菜单占 9001~9006；
--   9007~9009 已被 agent_audit_menu.sql（决策审计 + 2 个按钮）占用。
--   本文件是**增量**脚本，只新增 1 条菜单，不重复上面任何一行。
--
-- 页面组件路径必须与仓库实际文件一致：src/views/manage/agentRun/index.vue
--   → component 填 `manage/agentRun/index`（RuoYi 动态路由的组件路径不带 src/views）。
-- =============================================================================

-- ⚠️ 必须置顶（2026-10-26 实测踩坑）：Windows 上 DBeaver/控制台默认字符集常是 GBK，
-- 中文在传给 mysql 客户端时可能被转成 '?'，于是 `AS 检查项` 变成 `AS ?` → ERROR 1064 near '?…'。
-- 这里同时做两层防护：① SET NAMES 让服务端按 utf8mb4 切词；② 所有**非引号内**的中文（列别名）改成 ASCII。
-- 执行命令建议显式带字符集：mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_run_menu.sql
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 区间为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 区间必须为空（本文件只占 9010，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id BETWEEN 9010 AND 9010
ORDER BY menu_id;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- ---------------------------------------------------------------------------
-- 第 1 步：页面菜单（menu_type = 'C'）
--   order_num=3：同目录下补货工作台(1) / 决策审计(2) 之后。
--   perms 复用 manage:agentAudit:list，**刻意不新增按钮权限**（理由见文件头）。
--   icon=time：时间线语义，且 dkd-vue/src/assets/icons/svg 下确有该图标。
-- ---------------------------------------------------------------------------
SET @now := sysdate();

INSERT INTO sys_menu VALUES(
  9010,
  '运行时间线',
  9000,
  '3',
  'agentRun',
  'manage/agentRun/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentAudit:list',
  'time',
  'admin', @now, '', NULL,
  '智能体运行时间线只读回放：一次运行的每一轮（角色/动作/tokens/耗时）与每轮工具调用；补货/故障共用（F-04）'
);

-- ---------------------------------------------------------------------------
-- 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 1 行：menu_type='C'、component='manage/agentRun/index'、
--              perms='manage:agentAudit:list'、visible='0'、status='0'
SELECT '执行后校验：应返回 1 行（9010 / C / manage/agentRun/index）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id = 9010;

-- 应看到 9001 补货工作台 / 9007 决策审计 / 9010 运行时间线 三个子菜单
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- 角色可见性：管理员（RuoYi 的 admin 分支）自动看到全部菜单；
-- 其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页。
-- 本脚本**不**自动插 sys_role_menu：给哪个角色授权是运维决定，写死在脚本里
-- 会把页面发给不该看运行数据的人。若要手工补，形如：
--   INSERT INTO sys_role_menu(role_id, menu_id) VALUES(2, 9010);
SELECT '已授予本页的角色（0 行表示只有管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id = 9010;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 1 行（9010）：不改 9000 目录、不改 9001~9009 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不插 sys_role_menu：执行后**管理员立即可见**，其他角色需在
--    「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页
--    （脚本不替运维发权限，避免把运行数据发给不该看的角色）。
-- 3) 复用 manage:agentAudit:list 的副作用：已拥有决策审计权限的角色，
--    在勾选本页后无需新增权限点即可访问；未勾选本页时仍看不到菜单。
--    ⚠️ 详情接口 `GET /manage/agent/run/{runId}` 与 `/{runId}/steps` 后端要求
--    **已有的** manage:agentAudit:query（= 决策审计的 9009「留痕详情」按钮权限）。
--    只勾本页、不勾 9009 的角色能看到列表，但点「查看」会 403。
--    给角色授权时把 9007~9009 与 9010 一起勾选即可——注意这里**没有**新增权限点，
--    只是沿用审计页已有的那个。
-- 4) 发布顺序：先发布页面文件 dkd-vue/src/views/manage/agentRun/index.vue，
--    再执行本 SQL。反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 5) 本脚本不触碰 agent_run/agent_step/agent_tool_call 数据、也不建表；
--    三张表由 docs/ddl/agent_run_step.sql 单独执行（本脚本只建入口）。

-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-10-26：脚本创建并完成静态自查（字段顺序与 agent_audit_menu.sql 一致、
--   括号/引号配对、9010 未占用）；**真库尚未执行**——本机执行后请在此登记实际结果。

-- ---------------------------------------------------------------------------
-- 回滚
-- ---------------------------------------------------------------------------
-- 先删角色-菜单关联，再删菜单本体（顺序反了会留下指向不存在菜单的授权行）
-- DELETE FROM sys_role_menu WHERE menu_id = 9010;
-- DELETE FROM sys_menu      WHERE menu_id = 9010;
--
-- 回滚影响面：
--   · 只移除「运行时间线」入口，不动 9000 目录、不动 9001~9009 任何已有菜单；
--   · **不删** agent_run / agent_step / agent_tool_call 的任何数据——入口没了不代表
--     运行证据该消失（与决策审计回滚同一原则）；
--   · 前端页面文件 src/views/manage/agentRun/index.vue 保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性，不影响其在决策审计页
--     继续使用同一个 manage:agentAudit:list 权限。
-- =============================================================================
