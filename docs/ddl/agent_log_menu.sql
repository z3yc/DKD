-- =============================================================================
-- 执行日志页（S6）+ 历史会话列表页（S13）· 页面菜单与按钮权限
-- 排期任务：2-4（日志页）+ 2-5（会话列表），前端随 Batch A 落地
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式（Windows 显式带字符集，否则中文可能被 GBK 转成 '?'）：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_log_menu.sql
-- 前置条件：
--   · agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在）；
--   · agent_audit_menu.sql（9007~9009）、agent_run_menu.sql（9010）、
--     agent_config_menu.sql（9011~9016）已执行；
--   · 故本文件只占 **9017~9019** 三个 ID。
-- 幂等性  ：**非幂等**——重复执行会因 menu_id 主键冲突报 1062（这是有意的：
--           静默跳过会让「菜单没生效」查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么需要这个脚本（AGENTS §2.2）：
--   前端**不硬编码新路由**，菜单由 RuoYi 动态路由从 sys_menu 下发；
--   页面组件路径必须与本仓库实际文件一致（component 不带 src/views 前缀）：
--     src/views/manage/agentLog/index.vue          → manage/agentLog/index
--     src/views/manage/agentConversation/index.vue → manage/agentConversation/index
--
-- 权限点口径（与后端 @PreAuthorize 一一对应，否则会出现“菜单可见、接口 403”）：
--   9017 C 执行日志   perms=manage:agentLog:list               （GET /manage/agent/log/list）
--   9018 C 会话列表   perms=manage:agentConversation:list      （GET /manage/agent/conversation/list）
--   9019 F 会话详情   perms=manage:agentConversation:query     （GET /manage/agent/conversation/{conversationId}）
--
-- 为什么只加 1 个按钮权限（9019），不加 delete/edit：
--   两张表分别是「排障证据」（agent_log 只增）与「用户与智能体说过什么」（agent_conversation/
--   agent_message 事实记录）。后台一旦能改/能删，它们就不能再回答“当时到底发生了什么”。
--   这**不是还没做**，而是与决策审计页同一纪律（docs/ddl/agent_audit_menu.sql 文件头）。
--
-- ⚠️ menu_id 取 9017~9019 的依据（本机实测，Batch A 看板记录）：
--   9000 目录 / 9001 补货工作台 / 9002~9006 补货按钮 / 9007 决策审计 / 9008~9009 审计按钮 /
--   9010 运行时间线 / 9011 智能体配置 / 9012~9016 配置按钮；9017 起连续 3 个 ID 为空。
--   若目标环境不同，先跑「第 0 步」占用检查；被占用就把 @menu_base 改成其他连续空位。
-- =============================================================================

-- ⚠️ 必须置顶：DBeaver/控制台默认字符集常是 GBK，中文列别名会被转成 '?' → ERROR 1064。
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 区间为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 区间必须为空（本文件占 9017~9019，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id BETWEEN 9017 AND 9019
ORDER BY menu_id;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- 参照：同目录下既有子菜单，确认 order_num 不撞车（现有最大 order_num=4「智能体配置」）
SELECT '同目录既有子菜单（本文件用 order_num=5 / 6）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 变量：改这一行即可（@menu_base 必须指向连续 3 个空位）
-- ---------------------------------------------------------------------------
SET @menu_base := 9017;
SET @now := sysdate();

-- ---------------------------------------------------------------------------
-- ① 页面菜单「执行日志」（menu_type = 'C'，S6）
--    order_num=5：同目录下 补货工作台(1) / 决策审计(2) / 运行时间线(3) / 智能体配置(4) 之后。
--    icon=log：日志语义，且 dkd-vue/src/assets/icons/svg/log.svg 存在。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 0,
  '执行日志',
  9000,
  '5',
  'agentLog',
  'manage/agentLog/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentLog:list',
  'log',
  'admin', @now, '', NULL,
  '智能体执行日志只读查询：默认近 24h、最长 30 天，支持级别/状态/关键词/运行ID/追踪ID（排期 2-4 / S6）'
);

-- ---------------------------------------------------------------------------
-- ② 页面菜单「历史会话」（menu_type = 'C'，S13）
--    order_num=6：日志页之后。
--    icon=message：会话语义，且 dkd-vue/src/assets/icons/svg/message.svg 存在。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 1,
  '历史会话',
  9000,
  '6',
  'agentConversation',
  'manage/agentConversation/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentConversation:list',
  'message',
  'admin', @now, '', NULL,
  '智能体历史会话只读列表 + 消息时间线详情抽屉（复用 agent_conversation/agent_message，排期 2-5 / S13）'
);

-- ---------------------------------------------------------------------------
-- ③ 按钮权限「会话详情」（menu_type = 'F'，parent=9018）
--
--    只挂一个 F：列表以外的动作只有“打开详情抽屉”，对应后端
--    GET /manage/agent/conversation/{conversationId}（manage:agentConversation:query）。
--    ⚠️ 只勾 9018 不勾 9019 的角色：能看列表，点「查看详情」会 403——给角色授权时一起勾。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 2,
  '会话详情',
  @menu_base + 1,
  '1',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentConversation:query',
  '#',
  'admin', @now, '', NULL,
  '会话详情抽屉：会话头 + 消息时间线（GET /manage/agent/conversation/{conversationId}）'
);

-- ⚠️ 刻意**没有**新增/修改/删除权限：agent_log 是只增的排障证据，
--    agent_conversation/agent_message 是会话事实记录（计划表 ⚠1 明确复用现有两表）。
--    将来若有人要加这类权限，先回答“改了之后它还能叫证据/事实记录吗”。

-- ---------------------------------------------------------------------------
-- 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 3 行：2 个 C（9017/9018）+ 1 个 F（9019）；C 行有 path/component，F 行 path 为空
SELECT '执行后校验：应返回 3 行（9017 C + 9018 C + 9019 F）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id BETWEEN @menu_base AND @menu_base + 2
ORDER BY menu_id;

-- 应看到 9001 补货工作台 / 9007 决策审计 / 9010 运行时间线 / 9011 智能体配置 /
-- 9017 执行日志 / 9018 历史会话 六个子菜单
SELECT '同目录子菜单：应出现 9017 执行日志、9018 历史会话' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 角色授权模板（可选，按需执行）
--
-- RuoYi 超级管理员（role_id = 1）绕过菜单权限校验，因此不授权也能看到菜单；
-- 其他角色需要显式授权。日志/会话属运维排障数据，**不要**图省事授权给所有角色。
-- 把 @role_id 换成真实角色 ID（SELECT role_id, role_name FROM sys_role;）
-- ---------------------------------------------------------------------------
-- SET @role_id := 2;
-- INSERT INTO sys_role_menu(role_id, menu_id)
--   SELECT @role_id, menu_id FROM sys_menu
--   WHERE menu_id BETWEEN 9017 AND 9019;

-- 校验：已授予本页的角色（0 行表示只有管理员可见，属预期）
SELECT '已授予本页的角色（0 行 = 仅管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id BETWEEN 9017 AND 9019
ORDER BY rm.role_id, rm.menu_id;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 3 行（9017~9019）：不改 9000 目录、不改 9001~9016 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不自动插 sys_role_menu：给哪个角色授权是运维决定。管理员执行后立即可见，
--    其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页（含 9019 按钮，
--    只勾菜单不勾按钮会出现“列表可见、详情 403”）。
-- 3) 执行日志页的两个跨页按钮**不新增权限点**：
--      · 「关联运行」复用已有的 manage:agentAudit:list（运行时间线 9010 的权限，见 agent_run_menu.sql），
--        没有该权限的人不显示按钮，避免点进去 403；
--      · 「会话」复用本文件新增的 manage:agentConversation:list。
-- 4) 发布顺序：先发布前端文件（views/manage/agentLog/**、views/manage/agentConversation/**、
--    api/manage/agentLog.js、api/manage/agentConversation.js），再执行本 SQL。
--    反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 5) 本脚本不建表、不触碰任何 agent_* 数据：
--    · agent_log 表见 docs/ddl/agent_config.sql 第 3 段（已建）；
--    · agent_conversation / agent_message 见 docs/ddl/agent_tables.sql（已建，本批复用不新增表）。
-- 6) 菜单入口需重新登录（或清 RuoYi 菜单缓存）才在前端出现——菜单是登录时下发的。
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-11-04：脚本创建并完成静态自查（9017~9019 与既有 9000~9016 不重叠、
--   字段顺序与 agent_config_menu.sql 一致、3 行均为单语句 INSERT、icon 文件均存在、
--   component 路径与仓库文件逐一核对）。
-- 2026-11-04：**已在本机 dkd 库执行**（root@127.0.0.1，MySQL 8.0.40，
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_log_menu.sql"`）。
--   执行前 9017~9019 为空；执行后校验 3 行（9017 执行日志 C / 9018 历史会话 C / 9019 会话详情 F），
--   parent/perms 正确；`sys_role_menu` 未插（仅管理员可见，属预期）。菜单需重新登录后可见。

-- ---------------------------------------------------------------------------
-- 回滚（先删角色关联再删菜单本体；顺序反了会留下指向不存在菜单的授权行）
-- ---------------------------------------------------------------------------
-- DELETE FROM sys_role_menu WHERE menu_id BETWEEN 9017 AND 9019;
-- DELETE FROM sys_menu      WHERE menu_id BETWEEN 9017 AND 9019;
--
-- 回滚影响面：
--   · 只移除「执行日志」「历史会话」两个入口与「会话详情」按钮权限，
--     不动 9000 目录、不动 9001~9016 已有菜单；
--   · **不删** agent_log / agent_conversation / agent_message 的任何数据——
--     入口没了不代表日志与会话记录该消失（与决策审计回滚同一原则）；
--   · 前端页面文件保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性。
-- =============================================================================
