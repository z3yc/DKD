-- =============================================================================
-- MCP 接入（S12）· 页面菜单与按钮权限（排期任务 3-7）
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式（Windows 显式带字符集，否则中文可能被 GBK 转成 '?'）：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_mcp_menu.sql
-- 前置条件：
--   · agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在）；
--   · agent_audit_menu.sql（9007~9009）、agent_run_menu.sql（9010）、
--     agent_config_menu.sql（9011~9016）、agent_log_menu.sql（9017~9019）、
--     agent_tool_llm_menu.sql（9020~9025）、agent_kb_menu.sql（9026~9029）已执行；
--   · 故本文件只占 **9030~9034** 五个 ID。
-- 幂等性  ：**非幂等**——重复执行会因 menu_id 主键冲突报 1062（这是有意的：
--           静默跳过会让「菜单没生效」查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么需要这个脚本（AGENTS §2.2）：
--   前端**不硬编码新路由**，菜单由 RuoYi 动态路由从 sys_menu 下发；
--   页面组件路径必须与本仓库实际文件一致（component 不带 src/views 前缀）：
--     src/views/manage/agentMcp/index.vue → manage/agentMcp/index
--
-- 权限点口径（与后端 @PreAuthorize 一一对应，否则会出现“菜单可见、接口 403”）：
--   9030 C MCP 接入     perms=manage:agentMcp:list   （GET /manage/agent/mcp/list、GET /{id}/tools）
--   9031 F 新增 server  perms=manage:agentMcp:add    （POST /manage/agent/mcp）
--   9032 F 编辑/启停    perms=manage:agentMcp:edit   （PUT /{id}、PUT /{id}/enabled）
--   9033 F 探测         perms=manage:agentMcp:probe  （POST /{id}/probe）
--   9034 F 同步         perms=manage:agentMcp:probe  （POST /{id}/sync）
--
-- 为什么「同步」复用 probe 权限而不是再立一个：
--   同步与探测是同一安全面的两个动作（都要真实连一次外部 MCP server、都会改写工具注册表），
--   能探测的人本来就该能同步；多一个权限点只会多一处漏配，不会带来任何隔离收益。
--
-- 为什么只有这 5 行、没有 delete/query 按钮：
--   · 不提供删除：停用即可（已同步工具标记停用、不物理删除，保留调用留痕关联）；
--   · 页面编辑直接使用列表行数据（列表已返回全部可编辑字段），**不调用 GET /{id}**；
--     若后端未来给 GET /{id} 配独立的 query 权限，也只影响将来的详情页，不影响本页。
--
-- ⚠️ menu_id 取 9030~9034 的依据（本机实测，Batch A/B/C 执行记录）：
--   9000 目录 / 9001 补货工作台 / 9002~9006 补货按钮 / 9007 决策审计 / 9008~9009 审计按钮 /
--   9010 运行时间线 / 9011 智能体配置 / 9012~9016 配置按钮 / 9017 执行日志 / 9018 历史会话 /
--   9019 会话详情 / 9020 工具中心 / 9021 工具编辑 / 9022 模型档案 / 9023~9025 档案按钮 /
--   9026 知识库 / 9027~9029 知识库按钮；故 9030 起连续 5 个 ID 为空。
--   若目标环境不同，先跑「第 0 步」占用检查；被占用就把 @menu_base 改成其他连续空位。
-- =============================================================================

-- ⚠️ 必须置顶：DBeaver/控制台默认字符集常是 GBK，中文列别名会被转成 '?' → ERROR 1064。
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 区间为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 区间必须为空（本文件占 9030~9034，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id BETWEEN 9030 AND 9034
ORDER BY menu_id;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- 参照：同目录下既有子菜单，确认 order_num 不撞车（现有最大 order_num=9「知识库」）
SELECT '同目录既有子菜单（本文件用 order_num=10）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 变量：改这一行即可（@menu_base 必须指向连续 5 个空位）
-- ---------------------------------------------------------------------------
SET @menu_base := 9030;
SET @now := sysdate();

-- ---------------------------------------------------------------------------
-- ① 页面菜单「MCP 接入」（menu_type = 'C'，3-7 / S12）
--    order_num=10：知识库(9) 之后。
--    icon=server：外部 server 接入语义，且 dkd-vue/src/assets/icons/svg/server.svg 存在。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 0,
  'MCP 接入',
  9000,
  '10',
  'agentMcp',
  'manage/agentMcp/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentMcp:list',
  'server',
  'admin', @now, '', NULL,
  'MCP server 接入（S12）：配置/探测/同步/工具清单；只接只读工具、默认停用、密钥只存环境变量名（排期 3-7）'
);

-- ---------------------------------------------------------------------------
-- ② 按钮权限（menu_type = 'F'，parent=9030）
--    9031 新增 server   → 新增抽屉（POST /manage/agent/mcp）
--    9032 编辑/启停     → 编辑抽屉 + 行内启用开关（PUT /{id}、PUT /{id}/enabled）
--    9033 探测          → 「探测」（POST /{id}/probe；真实连接外部 server，并自动同步只读工具）
--    9034 同步          → 「同步」（POST /{id}/sync；与探测同安全面，复用 probe 权限）
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 1,
  '新增 server',
  @menu_base + 0,
  '1',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentMcp:add',
  '#',
  'admin', @now, '', NULL,
  '新增 MCP server（POST /manage/agent/mcp；默认 enabled=0，需安全复核后再启用）'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 2,
  '编辑/启停',
  @menu_base + 0,
  '2',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentMcp:edit',
  '#',
  'admin', @now, '', NULL,
  '编辑 MCP server 与启用/停用（PUT /{id}、PUT /{id}/enabled）'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 3,
  '探测',
  @menu_base + 0,
  '3',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentMcp:probe',
  '#',
  'admin', @now, '', NULL,
  '探测 MCP server（POST /{id}/probe）：真实连接外部服务，成功会自动同步只读工具'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 4,
  '同步工具',
  @menu_base + 0,
  '4',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentMcp:probe',
  '#',
  'admin', @now, '', NULL,
  '同步工具清单（POST /{id}/sync）：与探测同安全面，复用 manage:agentMcp:probe'
);

-- ---------------------------------------------------------------------------
-- 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 5 行：1 个 C（9030）+ 4 个 F（9031~9034，其中 9033/9034 共用 probe 权限）
SELECT '执行后校验：应返回 5 行（9030 C + 9031~9034 F）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id BETWEEN @menu_base AND @menu_base + 4
ORDER BY menu_id;

-- 应看到 9026 知识库 / 9030 MCP 接入 等子菜单
SELECT '同目录子菜单：应出现 9030 MCP 接入（order_num=10）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 角色授权模板（可选，按需执行）
--
-- RuoYi 超级管理员（role_id = 1）绕过菜单权限校验，因此不授权也能看到菜单；
-- 其他角色需要显式授权。MCP 是**安全面最大的入口**（外部网络/进程、可绕过本地 sql_guard），
-- 建议只授给负责 MCP 安全复核的运维/开发角色，不要把 probe 权限发给普通运营。
-- 把 @role_id 换成真实角色 ID（SELECT role_id, role_name FROM sys_role;）
-- ---------------------------------------------------------------------------
-- SET @role_id := 2;
-- INSERT INTO sys_role_menu(role_id, menu_id)
--   SELECT @role_id, menu_id FROM sys_menu
--   WHERE menu_id BETWEEN 9030 AND 9034;

-- 校验：已授予本页的角色（0 行表示只有管理员可见，属预期）
SELECT '已授予本页的角色（0 行 = 仅管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id BETWEEN 9030 AND 9034
ORDER BY rm.role_id, rm.menu_id;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 5 行（9030~9034）：不改 9000 目录、不改 9001~9029 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不自动插 sys_role_menu：给哪个角色授权是运维决定。管理员执行后立即可见，
--    其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页（含按钮）：
--    · 只勾 9030 不勾 9033/9034 → 列表可见、探测/同步 403（此时只能看配置）。
-- 3) 发布顺序：先发布前端文件（api/manage/agentMcp.js、views/manage/agentMcp/index.vue），
--    再执行本 SQL。反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 4) 本脚本不建表、不触碰任何 agent_* 数据：
--    三张表（agent_mcp_server / agent_mcp_tool / agent_mcp_probe）见
--    docs/ddl/agent_kb_mcp.sql（已在本机执行，见该文件执行记录）。
-- 5) 本页**没有密钥输入/回显**：envKeys 只存「HTTP 头名 → 环境变量名」，密钥值在部署环境；
--    后端 DTO 也不接收 apiKey 字段。
-- 6) 菜单入口需重新登录（或清 RuoYi 菜单缓存）才在前端出现——菜单是登录时下发的。
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-11-04：**已在本机 dkd 库执行**（root@127.0.0.1，MySQL 8.0.40，
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_mcp_menu.sql"`）。
--   执行前 9030~9034 为空；执行后校验 5 行（9030 MCP 接入 C / 9031 新增 F / 9032 编辑启停 F /
--   9033 探测 F / 9034 同步 F，复用 probe 权限），parent/perms 正确；
--   `sys_role_menu` 未插（仅管理员可见，属预期）。菜单需重新登录后可见。

-- ---------------------------------------------------------------------------
-- 回滚（先删角色关联再删菜单本体；顺序反了会留下指向不存在菜单的授权行）
-- ---------------------------------------------------------------------------
-- DELETE FROM sys_role_menu WHERE menu_id BETWEEN 9030 AND 9034;
-- DELETE FROM sys_menu      WHERE menu_id BETWEEN 9030 AND 9034;
--
-- 回滚影响面：
--   · 只移除「MCP 接入」入口与 4 个按钮权限，不动 9000 目录、不动 9001~9029 已有菜单；
--   · **不删** agent_mcp_server / agent_mcp_tool / agent_mcp_probe 的任何数据——
--     入口没了不代表配置、同步结果与探测留痕该消失；MCP 工具仍按库内 enabled 对运行期生效；
--   · 前端页面文件保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性。
-- =============================================================================
