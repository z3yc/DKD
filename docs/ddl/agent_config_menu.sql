-- =============================================================================
-- 智能体配置台 · 页面菜单与按钮权限（排期任务 2-6）
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式（Windows 显式带字符集，否则中文可能被 GBK 转成 '?'）：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_config_menu.sql
-- 前置条件：
--   · agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在）；
--   · agent_audit_menu.sql（9007~9009）、agent_run_menu.sql（9010）已执行，本文件只占 9011~9016。
-- 幂等性  ：**非幂等**——重复执行会因 menu_id 主键冲突报 1062（这是有意的：
--           静默跳过会让「菜单没生效」查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么需要这个脚本（AGENTS §2.2）：
--   前端**不硬编码新路由**，菜单由 RuoYi 动态路由从 sys_menu 下发。
--   页面组件路径必须与本仓库实际文件一致：
--     src/views/manage/agentConfig/index.vue → component 填 `manage/agentConfig/index`
--   （动态路由的 component 不带 src/views 前缀）。
--
-- 为什么本页要新增 6 条记录（1 个 C + 5 个 F）而不是复用现有权限点：
--   配置台能改**运行期行为**（提示词/模型/工具/参数/启停），与「运行时间线」「决策审计」
--   这类只读页不是同一风险面：能看审计的人不一定该能改所有智能体的行为。
--   因此按契约 §4.1 独立设 5 个权限点，与后端 @PreAuthorize 一一对应：
--     manage:agentConfig:list / query / add / edit / rollback
--   其中「启停」复用 edit（契约 §4.1 明确 PUT /{id}/enabled 要求 edit），
--   不另立 manage:agentConfig:enable —— 多一个权限点只会多一处漏配，
--   而且启停与编辑同属“改配置的人”。
--
-- ⚠️ menu_id 取 9011~9016 的依据（本机实测，2026-10-26 前归档）：
--   9000 目录 / 9001 补货工作台 / 9002~9006 补货按钮 / 9007 决策审计 /
--   9008~9009 审计按钮 / 9010 运行时间线；故 9011 起连续 6 个 ID 可用。
--   若目标环境不同，先跑「第 0 步」占用检查；被占用就把 @menu_base 改成其他连续空位。
-- =============================================================================

-- ⚠️ 必须置顶：DBeaver/控制台默认字符集常是 GBK，中文列别名会被转成 '?' → ERROR 1064。
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 区间为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 区间必须为空（本文件占 9011~9016，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id BETWEEN 9011 AND 9016
ORDER BY menu_id;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- 参照：同目录下既有子菜单（补货工作台/决策审计/运行时间线），确认 order_num 不撞车
SELECT menu_id, menu_name, order_num, menu_type
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 变量：改这一行即可（@menu_base 必须指向连续 6 个空位）
-- ---------------------------------------------------------------------------
SET @menu_base := 9011;
SET @now := sysdate();

-- ---------------------------------------------------------------------------
-- ① 页面菜单「智能体配置」（menu_type = 'C'）
--    order_num=4：同目录下 补货工作台(1) / 决策审计(2) / 运行时间线(3) 之后。
--    icon=tool：配置台语义，且 dkd-vue/src/assets/icons/svg 下确有该图标。
--    perms=manage:agentConfig:list：与后端列表接口一致，否则会出现“菜单可见、接口 403”。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 0,
  '智能体配置',
  9000,
  '4',
  'agentConfig',
  'manage/agentConfig/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentConfig:list',
  'tool',
  'admin', @now, '', NULL,
  '智能体配置台：列表/详情/编辑/新增/停用/版本回滚（排期 2-6）'
);

-- ---------------------------------------------------------------------------
-- ② 按钮权限（menu_type = 'F'；与前端 v-hasPermi 一一对应，也与后端 @PreAuthorize 同名）
--
--    9012 配置查询 query   → 详情抽屉、版本历史（GET /{id}、/{id}/history、/meta）
--    9013 配置新增 add     → 「新增智能体」按钮（POST /manage/agent/config）
--    9014 配置编辑 edit    → 「编辑」「保存」；PUT /{id}
--    9015 版本回滚 rollback→ 版本历史里的「回滚」（POST /{id}/rollback）
--    9016 启停配置 edit    → 「启用/停用」（PUT /{id}/enabled，契约 §4.1 要求 edit）
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(@menu_base+1, '配置查询',   @menu_base+0, '1', '', '', '', 1, 0, 'F', '0', '0', 'manage:agentConfig:query',    '#', 'admin', @now, '', NULL, '');
INSERT INTO sys_menu VALUES(@menu_base+2, '配置新增',   @menu_base+0, '2', '', '', '', 1, 0, 'F', '0', '0', 'manage:agentConfig:add',      '#', 'admin', @now, '', NULL, '');
INSERT INTO sys_menu VALUES(@menu_base+3, '配置编辑',   @menu_base+0, '3', '', '', '', 1, 0, 'F', '0', '0', 'manage:agentConfig:edit',     '#', 'admin', @now, '', NULL, '');
INSERT INTO sys_menu VALUES(@menu_base+4, '版本回滚',   @menu_base+0, '4', '', '', '', 1, 0, 'F', '0', '0', 'manage:agentConfig:rollback', '#', 'admin', @now, '', NULL, '');
INSERT INTO sys_menu VALUES(@menu_base+5, '启停配置',   @menu_base+0, '5', '', '', '', 1, 0, 'F', '0', '0', 'manage:agentConfig:edit',     '#', 'admin', @now, '', NULL, '启停复用 edit：契约 §4.1 的 PUT /{id}/enabled 要求 manage:agentConfig:edit');

-- ---------------------------------------------------------------------------
-- 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 6 行：1 个 C（9011）+ 5 个 F（9012~9016），parent_id 均为 9011
-- 注意：只有 C 行有 path/component；F 行的 path/component 为空、perms 非空
SELECT '执行后校验：应返回 6 行（9011 C + 9012~9016 F）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id BETWEEN @menu_base AND @menu_base + 5
ORDER BY menu_id;

-- 应看到 9001 补货工作台 / 9007 决策审计 / 9010 运行时间线 / 9011 智能体配置
SELECT '同目录子菜单：应出现 9011 智能体配置（order_num=4）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- 权限点自查：应返回 5 行（query/add/edit×2/rollback），与后端 @PreAuthorize 同名
SELECT '权限点清单（应 5 行，含 edit 两行）' AS check_item;
SELECT menu_id, menu_name, perms
FROM sys_menu
WHERE menu_id BETWEEN @menu_base + 1 AND @menu_base + 5
ORDER BY menu_id;

-- ---------------------------------------------------------------------------
-- 角色授权模板（可选，按需执行）
--
-- RuoYi 超级管理员（role_id = 1）绕过菜单权限校验，因此不授权也能看到菜单；
-- 其他角色需要显式授权。配置台能改运行期行为，**不要**图省事授权给所有角色。
-- 把 @role_id 换成真实角色 ID（SELECT role_id, role_name FROM sys_role;）
-- ---------------------------------------------------------------------------
-- SET @role_id := 2;
-- INSERT INTO sys_role_menu(role_id, menu_id)
--   SELECT @role_id, menu_id FROM sys_menu
--   WHERE menu_id BETWEEN 9011 AND 9016;

-- 校验：已授予本页菜单的角色（0 行表示只有管理员可见，属预期）
SELECT '已授予本页的角色（0 行 = 仅管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id BETWEEN 9011 AND 9016
ORDER BY rm.role_id, rm.menu_id;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 6 行（9011~9016）：不改 9000 目录、不改 9001~9010 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不自动插 sys_role_menu：给哪个角色授权是运维决定。管理员执行后立即可见，
--    其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页（含 5 个按钮，
--    只勾菜单不勾按钮会出现“列表可见、详情/编辑 403”）。
-- 3) 权限点必须与后端 @PreAuthorize 同名（manage:agentConfig:*）；前端按钮已用
--    v-hasPermi 绑定同名权限点。若权限点不一致，表现为“按钮可见、接口 403”。
-- 4) 发布顺序：先发布前端文件（views/manage/agentConfig/** 与 api/manage/agentConfig.js），
--    再执行本 SQL。反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 5) 本脚本不建表、不触碰任何 agent_* 数据：表结构见 docs/ddl/agent_config.sql，
--    seed 见 docs/ddl/agent_config_seed.sql（均由排期 2-1 落地）。
-- 6) 本页**不提供删除**（契约 §4.4）：前端没有删除按钮，菜单也不设 delete 权限点。
--    若某环境历史上有自定义配置要下线，用停用（PUT /{id}/enabled，必填原因）。
-- 7) 菜单入口需重新登录（或清 RuoYi 菜单缓存）才在前端出现——菜单是登录时下发的。
-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-11-03：脚本创建并完成静态自查（ID 区间 9011~9016 与 agent_run_menu.sql 的
--   9010 不重叠、字段顺序与 agent_restock_menu.sql 一致、6 行均为单语句 INSERT）。
-- 2026-11-04：**已在本机 dkd 库执行**（MySQL 8.0.40，root@127.0.0.1，
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_config_menu.sql"`）。
--   执行前实测：9000~9010 已存在、9011~9016 为空；执行后校验返回 **6 行**
--   （9011 C `manage:agentConfig:list` + 9012 query / 9013 add / 9014 edit / 9015 rollback /
--   9016 启停(edit)），parent_id 层级正确（9011 父 9000，按钮父 9011）；
--   `sys_role_menu` 未插（仅管理员可见，属预期——其他角色需按上方说明手工授权）。
--   前端页面需重新登录或清菜单缓存后才出现。

-- ---------------------------------------------------------------------------
-- 回滚（先删角色关联再删菜单本体；顺序反了会留下指向不存在菜单的授权行）
-- ---------------------------------------------------------------------------
-- DELETE FROM sys_role_menu WHERE menu_id BETWEEN 9011 AND 9016;
-- DELETE FROM sys_menu      WHERE menu_id BETWEEN 9011 AND 9016;
--
-- 回滚影响面：
--   · 只移除「智能体配置」入口与 5 个按钮权限，不动 9000 目录、不动 9001~9010 已有菜单；
--   · **不删** agent_config / agent_config_history 任何数据——入口没了不代表配置该消失；
--     智能体的运行期行为仍按库里当前配置生效（Python 侧 TTL 30s 内自然保持不变）；
--   · 前端页面文件保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性。
-- =============================================================================
