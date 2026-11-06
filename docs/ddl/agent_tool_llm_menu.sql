-- =============================================================================
-- 工具中心（3-4）+ 模型档案 S14（3-5）· 页面菜单与按钮权限
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式（Windows 显式带字符集，否则中文可能被 GBK 转成 '?'）：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_tool_llm_menu.sql
-- 前置条件：
--   · agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在）；
--   · agent_audit_menu.sql（9007~9009）、agent_run_menu.sql（9010）、
--     agent_config_menu.sql（9011~9016）、agent_log_menu.sql（9017~9019）已执行；
--   · 故本文件只占 **9020~9025** 六个 ID。
-- 幂等性  ：**非幂等**——重复执行会因 menu_id 主键冲突报 1062（这是有意的：
--           静默跳过会让「菜单没生效」查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么需要这个脚本（AGENTS §2.2）：
--   前端**不硬编码新路由**，菜单由 RuoYi 动态路由从 sys_menu 下发；
--   页面组件路径必须与本仓库实际文件一致（component 不带 src/views 前缀）：
--     src/views/manage/agentTool/index.vue       → manage/agentTool/index
--     src/views/manage/agentLlmProfile/index.vue → manage/agentLlmProfile/index
--
-- 权限点口径（与后端 @PreAuthorize 一一对应，否则会出现“菜单可见、接口 403”）：
--   9020 C 工具中心       perms=manage:agentTool:list        （GET /manage/agent/tool/list）
--   9021 F 工具参数编辑   perms=manage:agentTool:edit        （PUT /manage/agent/tool/{id}）
--   9022 C 模型档案       perms=manage:agentLlmProfile:list  （GET /manage/agent/llm/profile/list、/{id}/probes）
--   9023 F 档案新增       perms=manage:agentLlmProfile:add   （POST /manage/agent/llm/profile）
--   9024 F 档案编辑       perms=manage:agentLlmProfile:edit  （PUT /{id}、PUT /{id}/enabled）
--   9025 F 连通测试       perms=manage:agentLlmProfile:probe （POST /{id}/probe）
--
-- 为什么「连通测试」单独一个权限点（不复用 edit）：
--   它会**真的向外部模型供应商发一次请求**（出网 + 成本 + 可能触发限流），并写一条探测记录；
--   而 edit 只是改库里的连接信息。把两者合并，等于给“只想改文案的人”也发了外网调用能力。
--   权限拆开是成本/出网治理的一部分，不是形式主义。
--
-- 为什么工具中心只设 list/edit 两个点、且 edit 不包含 code/read_only：
--   工具注册表的 `code` 必须能映射到代码实现，`read_only` 是安全边界（写工具禁止模型直调，
--   AGENTS §7.9）；后端 PUT 只落 enabled/timeout_ms/row_limit，若传入 code/read_only 等
--   与库内不同的值会直接 400。因此页面没有新增/删除按钮，也不存在对应权限点。
--
-- ⚠️ menu_id 取 9020~9025 的依据（本机实测，Batch A/B 执行记录）：
--   9000 目录 / 9001 补货工作台 / 9002~9006 补货按钮 / 9007 决策审计 / 9008~9009 审计按钮 /
--   9010 运行时间线 / 9011 智能体配置 / 9012~9016 配置按钮 / 9017 执行日志 / 9018 历史会话 /
--   9019 会话详情；故 9020 起连续 6 个 ID 为空。
--   若目标环境不同，先跑「第 0 步」占用检查；被占用就把 @menu_base 改成其他连续空位。
-- =============================================================================

-- ⚠️ 必须置顶：DBeaver/控制台默认字符集常是 GBK，中文列别名会被转成 '?' → ERROR 1064。
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 区间为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 区间必须为空（本文件占 9020~9025，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id BETWEEN 9020 AND 9025
ORDER BY menu_id;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- 参照：同目录下既有子菜单，确认 order_num 不撞车（现有最大 order_num=6「历史会话」）
SELECT '同目录既有子菜单（本文件用 order_num=7 / 8）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 变量：改这一行即可（@menu_base 必须指向连续 6 个空位）
-- ---------------------------------------------------------------------------
SET @menu_base := 9020;
SET @now := sysdate();

-- ---------------------------------------------------------------------------
-- ① 页面菜单「工具中心」（menu_type = 'C'，3-4）
--    order_num=7：同目录下 执行日志(5) / 历史会话(6) 之后。
--    icon=tool：工具语义，且 dkd-vue/src/assets/icons/svg/tool.svg 存在。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 0,
  '工具中心',
  9000,
  '7',
  'agentTool',
  'manage/agentTool/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentTool:list',
  'tool',
  'admin', @now, '', NULL,
  '智能体工具注册表：查看清单、启停工具、调整超时与行数上限（排期 3-4）'
);

-- ---------------------------------------------------------------------------
-- ② 按钮权限「工具参数编辑」（menu_type = 'F'，parent=9020）
--    只对应后端 PUT /manage/agent/tool/{id}（落 enabled/timeout_ms/row_limit 三项）。
--    刻意没有新增/删除权限：工具由代码（或 MCP 接入层）发布，页面上“新建”一个没有实现的
--    code 只会在运行时报错。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 1,
  '工具参数编辑',
  @menu_base + 0,
  '1',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentTool:edit',
  '#',
  'admin', @now, '', NULL,
  '工具启停/超时/行数上限（PUT /manage/agent/tool/{id}，仅这三项可写）'
);

-- ---------------------------------------------------------------------------
-- ③ 页面菜单「模型档案」（menu_type = 'C'，3-5 / S14）
--    order_num=8：工具中心之后。
--    icon=star：模型/能力语义，且 dkd-vue/src/assets/icons/svg/star.svg 存在。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 2,
  '模型档案',
  9000,
  '8',
  'agentLlmProfile',
  'manage/agentLlmProfile/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentLlmProfile:list',
  'star',
  'admin', @now, '', NULL,
  '模型档案（S14）：连接信息/启停/默认档案/测试连通/探测记录；密钥只存环境变量名（排期 3-5）'
);

-- ---------------------------------------------------------------------------
-- ④ 按钮权限（menu_type = 'F'，parent=9022）
--    9023 新增  → 「新增档案」按钮（POST /manage/agent/llm/profile）
--    9024 编辑  → 「编辑」「启用/停用」（PUT /{id}、PUT /{id}/enabled）
--    9025 连通测试 → 「测试连通」（POST /{id}/probe，会真实调用外部模型，成本/出网面更大）
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 3,
  '档案新增',
  @menu_base + 2,
  '1',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentLlmProfile:add',
  '#',
  'admin', @now, '', NULL,
  '新增模型档案（POST /manage/agent/llm/profile）'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 4,
  '档案编辑',
  @menu_base + 2,
  '2',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentLlmProfile:edit',
  '#',
  'admin', @now, '', NULL,
  '编辑档案与启停（PUT /{id}、PUT /{id}/enabled）'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 5,
  '连通测试',
  @menu_base + 2,
  '3',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentLlmProfile:probe',
  '#',
  'admin', @now, '', NULL,
  '连通测试（POST /{id}/probe）：会真实调用外部模型并写探测记录，成本/出网面与编辑分开授权'
);

-- ---------------------------------------------------------------------------
-- 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 6 行：2 个 C（9020/9022）+ 4 个 F（9021、9023~9025）
SELECT '执行后校验：应返回 6 行（9020 C + 9021 F + 9022 C + 9023~9025 F）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id BETWEEN @menu_base AND @menu_base + 5
ORDER BY menu_id;

-- 应看到 9017 执行日志 / 9018 历史会话 / 9020 工具中心 / 9022 模型档案 等子菜单
SELECT '同目录子菜单：应出现 9020 工具中心、9022 模型档案' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- 权限点自查：应返回 6 行（2 个 list + edit/add/edit/probe）
SELECT '权限点清单（应 6 行，与后端 @PreAuthorize 同名）' AS check_item;
SELECT menu_id, menu_name, perms
FROM sys_menu
WHERE menu_id BETWEEN @menu_base AND @menu_base + 5
ORDER BY menu_id;

-- ---------------------------------------------------------------------------
-- 角色授权模板（可选，按需执行）
--
-- RuoYi 超级管理员（role_id = 1）绕过菜单权限校验，因此不授权也能看到菜单；
-- 其他角色需要显式授权。模型档案含外网调用能力、工具中心影响模型能做什么，
-- **不要**图省事授权给所有角色。
-- 把 @role_id 换成真实角色 ID（SELECT role_id, role_name FROM sys_role;）
-- ---------------------------------------------------------------------------
-- SET @role_id := 2;
-- INSERT INTO sys_role_menu(role_id, menu_id)
--   SELECT @role_id, menu_id FROM sys_menu
--   WHERE menu_id BETWEEN 9020 AND 9025;

-- 校验：已授予本页的角色（0 行表示只有管理员可见，属预期）
SELECT '已授予本页的角色（0 行 = 仅管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id BETWEEN 9020 AND 9025
ORDER BY rm.role_id, rm.menu_id;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 6 行（9020~9025）：不改 9000 目录、不改 9001~9019 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不自动插 sys_role_menu：给哪个角色授权是运维决定。管理员执行后立即可见，
--    其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页（含按钮）：
--    · 只勾 9020 不勾 9021 → 工具清单可见、启停/编辑 403；
--    · 只勾 9022 不勾 9023/9024/9025 → 档案可见、新增/编辑/测试连通 403。
-- 3) 发布顺序：先发布前端文件（api/manage/agentTool.js、api/manage/agentLlmProfile.js、
--    views/manage/agentTool/**、views/manage/agentLlmProfile/**），再执行本 SQL。
--    反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 4) 本脚本不建表、不触碰任何 agent_* 数据：
--    三张表（agent_tool / agent_llm_profile / agent_llm_probe）见
--    docs/ddl/agent_tool_llm_profile.sql（含 8 个本地工具 seed 与默认 DeepSeek 档案）。
-- 5) 菜单入口需重新登录（或清 RuoYi 菜单缓存）才在前端出现——菜单是登录时下发的。
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-11-04：脚本创建并完成静态自查（9020~9025 与既有 9000~9019 不重叠、
--   字段顺序与 agent_config_menu.sql 一致、6 行均为单语句 INSERT、icon 文件均存在、
--   component 路径与仓库文件逐一核对）。
-- 2026-11-04：**已在本机 dkd 库执行**（root@127.0.0.1，MySQL 8.0.40，
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_tool_llm_menu.sql"`）。
--   执行前 9020~9025 为空；执行后校验 6 行（9020 工具中心 C / 9021 编辑 F /
--   9022 模型档案 C / 9023 新增 F / 9024 编辑 F / 9025 连通测试 F），parent/perms 正确；
--   `sys_role_menu` 未插（仅管理员可见，属预期）。菜单需重新登录后可见。

-- ---------------------------------------------------------------------------
-- 回滚（先删角色关联再删菜单本体；顺序反了会留下指向不存在菜单的授权行）
-- ---------------------------------------------------------------------------
-- DELETE FROM sys_role_menu WHERE menu_id BETWEEN 9020 AND 9025;
-- DELETE FROM sys_menu      WHERE menu_id BETWEEN 9020 AND 9025;
--
-- 回滚影响面：
--   · 只移除「工具中心」「模型档案」两个入口与 4 个按钮权限，
--     不动 9000 目录、不动 9001~9019 已有菜单；
--   · **不删** agent_tool / agent_llm_profile / agent_llm_probe 的任何数据——
--     入口没了不代表工具注册表、连接信息与探测历史该消失（与审计页回滚同一原则）；
--   · 工具开关与模型档案仍按库内当前值对运行期生效（不因回滚菜单而恢复默认）；
--   · 前端页面文件保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性。
-- =============================================================================
