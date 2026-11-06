-- =============================================================================
-- 运营驾驶舱 S7 · 页面菜单与权限（排期任务 6-1）
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式（Windows 显式带字符集，否则中文可能被 GBK 转成 '?'）：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_dashboard_menu.sql
-- 前置条件：
--   · agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在）；
--   · 9017~9034 已分别被日志/会话/工具/档案/知识库/MCP 菜单占用（见各 ddl 文件执行记录）；
--   · 故本文件只占 **9035** 一个 ID。
-- 幂等性  ：**非幂等**——重复执行会因 menu_id 主键冲突报 1062（这是有意的：
--           静默跳过会让「菜单没生效」查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么需要这个脚本（AGENTS §2.2）：
--   前端**不硬编码新路由**，菜单由 RuoYi 动态路由从 sys_menu 下发；
--   页面组件路径必须与本仓库实际文件一致（component 不带 src/views 前缀）：
--     src/views/manage/agentDashboard/index.vue → manage/agentDashboard/index
--
-- 为什么新设权限点 manage:agentDashboard:list（不复用审计/配置的 list）：
--   驾驶舱展示的是**跨域的运营效果数据**（采纳率/降级率/成本/失败率/待办/双跑对比），
--   与「审计留痕」（manage:agentAudit:list）、「配置台」（manage:agentConfig:list）不是同一授权面：
--   能看配置的人不一定该看全公司成本与失败率。后端 @PreAuthorize 同名，
--   菜单若复用别的权限点会出现“菜单可见、接口 403”或“该看的人看不到”。
--
-- 本页只读：驾驶舱不提供任何写入口（没有 add/edit/delete 按钮，也不设对应权限点）。
-- 数据来自 agent_* 表与评测报告的聚合，口径唯一来源是 docs/agent-metrics-dictionary.md（V1）。
--
-- ⚠️ menu_id 取 9035 的依据（本机实测，Batch A~E 执行记录）：
--   9000 目录 / 9001 补货工作台 / 9002~9006 补货按钮 / 9007~9009 审计 / 9010 运行时间线 /
--   9011~9016 配置台 / 9017~9019 执行日志+会话 / 9020~9021 工具中心 / 9022~9025 模型档案 /
--   9026~9029 知识库 / 9030~9034 MCP；故 9035 为空。
--   若目标环境不同，先跑「第 0 步」占用检查；被占用就把 @menu_id 改成其他空位。
-- =============================================================================

-- ⚠️ 必须置顶：DBeaver/控制台默认字符集常是 GBK，中文列别名会被转成 '?' → ERROR 1064。
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 必须为空（本文件占 9035，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id = 9035;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- 参照：同目录下既有子菜单，确认 order_num 不撞车（现有最大 order_num=10「MCP 接入」）
SELECT '同目录既有子菜单（本文件用 order_num=11）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 变量：改这一行即可（@menu_id 必须指向空位）
-- ---------------------------------------------------------------------------
SET @menu_id := 9035;
SET @now := sysdate();

-- ---------------------------------------------------------------------------
-- ① 页面菜单「运营驾驶舱」（menu_type = 'C'，6-1 / S7）
--    order_num=11：MCP 接入(10) 之后，作为「AI 智能体」目录的运营汇总入口。
--    icon=dashboard：驾驶舱语义，且 dkd-vue/src/assets/icons/svg/dashboard.svg 存在。
--    perms=manage:agentDashboard:list：与后端 @PreAuthorize 同名，否则会出现“菜单可见、接口 403”。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_id,
  '运营驾驶舱',
  9000,
  '11',
  'agentDashboard',
  'manage/agentDashboard/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentDashboard:list',
  'dashboard',
  'admin', @now, '', NULL,
  '智能体运营驾驶舱（S7）：采纳/降级/成本/失败四卡 + 告警条 + 待我处理 + Agent vs 规则对比（排期 6-1）'
);

-- ---------------------------------------------------------------------------
-- ② 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 1 行：menu_type='C'、component='manage/agentDashboard/index'、
--              perms='manage:agentDashboard:list'、visible='0'、status='0'
SELECT '执行后校验：应返回 1 行（9035 / C / manage/agentDashboard/index）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id = @menu_id;

-- 应看到 9030 MCP 接入 / 9035 运营驾驶舱 等子菜单
SELECT '同目录子菜单：应出现 9035 运营驾驶舱（order_num=11）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 角色授权模板（可选，按需执行）
--
-- RuoYi 超级管理员（role_id = 1）绕过菜单权限校验，因此不授权也能看到菜单；
-- 其他角色需要显式授权。驾驶舱含全公司成本/失败率等经营数据，
-- **不要**图省事授权给所有角色（与审计页同一原则：能看证据 ≠ 能看经营面）。
-- 把 @role_id 换成真实角色 ID（SELECT role_id, role_name FROM sys_role;）
-- ---------------------------------------------------------------------------
-- SET @role_id := 2;
-- INSERT INTO sys_role_menu(role_id, menu_id) VALUES(@role_id, 9035);

-- 校验：已授予本页的角色（0 行表示只有管理员可见，属预期）
SELECT '已授予本页的角色（0 行 = 仅管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id = @menu_id
ORDER BY rm.role_id;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 1 行（9035）：不改 9000 目录、不改 9001~9034 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不自动插 sys_role_menu：给哪个角色授权是运维决定。管理员执行后立即可见，
--    其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页。
-- 3) 发布顺序：先发布前端文件（api/manage/agentDashboard.js、views/manage/agentDashboard/index.vue），
--    再执行本 SQL。反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 4) 本脚本不建表、不触碰任何业务数据：驾驶舱只读聚合 agent_restock_plan / agent_decision_log /
--    agent_message / agent_run / agent_log / agent_eval_report 等既有表。
--    ⚠️ 若后端实测发现某指标数据源未就绪，页面会显示「—」（字典 §0 R-6：不填演示数字），
--    这属预期，不要用造数把它“修绿”。
-- 5) 菜单入口需重新登录（或清 RuoYi 菜单缓存）才在前端出现——菜单是登录时下发的。
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-11-04：脚本创建并完成静态自查（9035 与既有 9000~9034 不重叠、
--   字段顺序与 agent_mcp_menu.sql 一致、单语句 INSERT、
--   icon=dashboard 文件存在、component 路径与仓库文件逐一核对）；
--   **真库尚未执行**——本机（9017~9034 已占）执行后请在此登记实际结果。

-- ---------------------------------------------------------------------------
-- 回滚（先删角色关联再删菜单本体；顺序反了会留下指向不存在菜单的授权行）
-- ---------------------------------------------------------------------------
-- DELETE FROM sys_role_menu WHERE menu_id = 9035;
-- DELETE FROM sys_menu      WHERE menu_id = 9035;
--
-- 回滚影响面：
--   · 只移除「运营驾驶舱」入口，不动 9000 目录、不动其它菜单；
--   · **不删**任何 agent_* 数据（驾驶舱本来就是只读聚合，回滚不影响业务与智能体运行）；
--   · 前端页面文件保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性。
-- =============================================================================

-- 执行记录（2026-11-04）：已在本机 dkd 库执行（root@127.0.0.1，MySQL 8.0.40）。
--   agent_compare_report：表创建成功，compare 脚本 --persist 写入 1 行（n=0，驾驶舱读取 available=false）。
--   驾驶舱菜单 9035：插入成功，perms=manage:agentDashboard:list，component=manage/agentDashboard/index；重新登录后可见。