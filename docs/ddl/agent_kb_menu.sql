-- =============================================================================
-- 知识库 L2（S11）· 页面菜单与按钮权限（排期任务 3-6）
-- =============================================================================
--
-- 执行环境：MySQL（库 `dkd`）；执行方式（Windows 显式带字符集，否则中文可能被 GBK 转成 '?'）：
--   mysql --default-character-set=utf8mb4 -h127.0.0.1 -uroot -p dkd < agent_kb_menu.sql
-- 前置条件：
--   · agent_restock_menu.sql 已执行（父目录 9000「AI 智能体」存在）；
--   · agent_audit_menu.sql（9007~9009）、agent_run_menu.sql（9010）、
--     agent_config_menu.sql（9011~9016）、agent_log_menu.sql（9017~9019）、
--     agent_tool_llm_menu.sql（9020~9025）已执行；
--   · 故本文件只占 **9026~9029** 四个 ID。
-- 幂等性  ：**非幂等**——重复执行会因 menu_id 主键冲突报 1062（这是有意的：
--           静默跳过会让「菜单没生效」查不出原因）。重跑前先执行末尾「回滚」段。
-- 回滚    ：见文件末尾（先删 sys_role_menu 再删 sys_menu）。
--
-- 为什么需要这个脚本（AGENTS §2.2）：
--   前端**不硬编码新路由**，菜单由 RuoYi 动态路由从 sys_menu 下发；
--   页面组件路径必须与本仓库实际文件一致（component 不带 src/views 前缀）：
--     src/views/manage/agentKb/index.vue → manage/agentKb/index
--
-- 权限点口径（与后端 @PreAuthorize 一一对应，否则会出现“菜单可见、接口 403”）：
--   9026 C 知识库     perms=manage:agentKb:list    （GET /manage/agent/kb/documents、/stats）
--   9027 F 新增文档   perms=manage:agentKb:ingest  （POST /manage/agent/kb/documents）
--   9028 F 检索测试   perms=manage:agentKb:search  （POST /manage/agent/kb/search）
--   9029 F 启停/编辑  perms=manage:agentKb:edit    （PUT /manage/agent/kb/documents/{docCode}/enabled）
--
-- 为什么检索测试单独一个权限点（不复用 list）：
--   检索测试会**真实触发一次召回**，并在 agent_kb_retrieval 里写一条只增记录（计入“检索次数”
--   与命中归因）。把它并进 list，等于让“只想看清单的人”也在真实检索记录里留痕，
--   污染“知识库被用了几次”这个客观口径。写权限（ingest/edit）同理属于语料变更面。
--
-- 为什么知识库写入仍归 Python（不是 Java 直接写库）：
--   文档/切片/检索记录属**智能体自有数据**（agent_kb_*，AGENTS §7.3）：切片与本地向量化
--   必须与检索侧实现同源，Java 在这里只做鉴权 + 代理 + 审计。菜单只负责“谁能进哪个入口”。
--
-- ⚠️ menu_id 取 9026~9029 的依据（本机实测，Batch A/B/C 执行记录）：
--   9000 目录 / 9001 补货工作台 / 9002~9006 补货按钮 / 9007 决策审计 / 9008~9009 审计按钮 /
--   9010 运行时间线 / 9011 智能体配置 / 9012~9016 配置按钮 / 9017 执行日志 / 9018 历史会话 /
--   9019 会话详情 / 9020 工具中心 / 9021 工具编辑 / 9022 模型档案 / 9023~9025 档案按钮；
--   故 9026 起连续 4 个 ID 为空。
--   若目标环境不同，先跑「第 0 步」占用检查；被占用就把 @menu_base 改成其他连续空位。
-- =============================================================================

-- ⚠️ 必须置顶：DBeaver/控制台默认字符集常是 GBK，中文列别名会被转成 '?' → ERROR 1064。
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- 第 0 步（必做）：目标 ID 区间为空 + 父目录已存在；两项都符合预期再继续
-- ---------------------------------------------------------------------------
SELECT '目标 ID 区间必须为空（本文件占 9026~9029，返回 0 行才继续）' AS check_item;
SELECT menu_id, menu_name, path, component
FROM sys_menu
WHERE menu_id BETWEEN 9026 AND 9029
ORDER BY menu_id;

SELECT '父目录 9000「AI 智能体」必须已存在（agent_restock_menu.sql 执行过）' AS check_item;
SELECT menu_id, menu_name, menu_type
FROM sys_menu
WHERE menu_id = 9000;

-- 参照：同目录下既有子菜单，确认 order_num 不撞车（现有最大 order_num=8「模型档案」）
SELECT '同目录既有子菜单（本文件用 order_num=9）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 变量：改这一行即可（@menu_base 必须指向连续 4 个空位）
-- ---------------------------------------------------------------------------
SET @menu_base := 9026;
SET @now := sysdate();

-- ---------------------------------------------------------------------------
-- ① 页面菜单「知识库」（menu_type = 'C'，3-6 / S11）
--    order_num=9：工具中心(7) / 模型档案(8) 之后。
--    icon=chart：语料与分析语义，且 dkd-vue/src/assets/icons/svg/chart.svg 存在。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 0,
  '知识库',
  9000,
  '9',
  'agentKb',
  'manage/agentKb/index',
  '',
  1, 0, 'C', '0', '0',
  'manage:agentKb:list',
  'chart',
  'admin', @now, '', NULL,
  '知识库 L2（S11）：文档语料清单/入库/启停/检索测试；L3 案例库未启用·数据未达标（FIX-22，排期 3-6）'
);

-- ---------------------------------------------------------------------------
-- ② 按钮权限（menu_type = 'F'，parent=9026）
--    9027 新增文档 → 「新增文档」抽屉入库（POST /documents；切片+向量化在 Python 侧）
--    9028 检索测试 → 「检索」按钮（POST /search；会写一条只增检索记录，故独立授权）
--    9029 启停/编辑 → 行内「启用/停用」（PUT /documents/{docCode}/enabled）
--    本期**不提供删除**：知识库文档只增/可停用（检索记录与引用是排障证据，删文档会让
--    已有引用悬空）；语料下线用停用，语义与审计口径都更清楚。
-- ---------------------------------------------------------------------------
INSERT INTO sys_menu VALUES(
  @menu_base + 1,
  '新增文档',
  @menu_base + 0,
  '1',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentKb:ingest',
  '#',
  'admin', @now, '', NULL,
  '新增知识库文档并入库（POST /manage/agent/kb/documents；切片与向量化在 Python 侧）'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 2,
  '检索测试',
  @menu_base + 0,
  '2',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentKb:search',
  '#',
  'admin', @now, '', NULL,
  '检索测试（POST /manage/agent/kb/search）：只读召回，但每次都会写一条检索记录'
);

INSERT INTO sys_menu VALUES(
  @menu_base + 3,
  '启停文档',
  @menu_base + 0,
  '3',
  '',
  '',
  '',
  1, 0, 'F', '0', '0',
  'manage:agentKb:edit',
  '#',
  'admin', @now, '', NULL,
  '文档启停（PUT /manage/agent/kb/documents/{docCode}/enabled）：停用即退出检索，不删数据'
);

-- ---------------------------------------------------------------------------
-- 第 2 步：校验
-- ---------------------------------------------------------------------------
-- 应返回 4 行：1 个 C（9026）+ 3 个 F（9027~9029）
SELECT '执行后校验：应返回 4 行（9026 C + 9027~9029 F）' AS check_item;
SELECT menu_id, menu_name, parent_id, order_num, path, component,
       menu_type, perms, visible, status
FROM sys_menu
WHERE menu_id BETWEEN @menu_base AND @menu_base + 3
ORDER BY menu_id;

-- 应看到 9020 工具中心 / 9022 模型档案 / 9026 知识库 等子菜单
SELECT '同目录子菜单：应出现 9026 知识库（order_num=9）' AS check_item;
SELECT menu_id, menu_name, order_num, menu_type, path, component
FROM sys_menu
WHERE parent_id = 9000
ORDER BY order_num, menu_id;

-- ---------------------------------------------------------------------------
-- 角色授权模板（可选，按需执行）
--
-- RuoYi 超级管理员（role_id = 1）绕过菜单权限校验，因此不授权也能看到菜单；
-- 其他角色需要显式授权。知识库影响智能体“为什么/怎么办”类回答的依据，
-- **不要**图省事授权给所有角色；ingest/search 建议只给语料维护人。
-- 把 @role_id 换成真实角色 ID（SELECT role_id, role_name FROM sys_role;）
-- ---------------------------------------------------------------------------
-- SET @role_id := 2;
-- INSERT INTO sys_role_menu(role_id, menu_id)
--   SELECT @role_id, menu_id FROM sys_menu
--   WHERE menu_id BETWEEN 9026 AND 9029;

-- 校验：已授予本页的角色（0 行表示只有管理员可见，属预期）
SELECT '已授予本页的角色（0 行 = 仅管理员可见，属预期）' AS check_item;
SELECT rm.role_id, r.role_name, rm.menu_id
FROM sys_role_menu rm
LEFT JOIN sys_role r ON r.role_id = rm.role_id
WHERE rm.menu_id BETWEEN 9026 AND 9029
ORDER BY rm.role_id, rm.menu_id;

-- ---------------------------------------------------------------------------
-- 影响面（执行前必读）
-- ---------------------------------------------------------------------------
-- 1) 只新增 sys_menu 4 行（9026~9029）：不改 9000 目录、不改 9001~9025 任何菜单，
--    因此既有用户的菜单树与角色授权不受影响。
-- 2) 不自动插 sys_role_menu：给哪个角色授权是运维决定。管理员执行后立即可见，
--    其他角色需在「系统管理 → 角色管理 → 修改 → 菜单权限」勾选本页（含按钮）：
--    · 只勾 9026 不勾 9027/9028/9029 → 清单可见、新增/检索/启停 403。
-- 3) 发布顺序：先发布前端文件（api/manage/agentKb.js、views/manage/agentKb/index.vue），
--    再执行本 SQL。反了会出现“菜单可见、点击白屏/404”（动态路由取不到组件）。
-- 4) 本脚本不建表、不触碰任何 agent_* 数据：
--    三张表（agent_kb_document / agent_kb_chunk / agent_kb_retrieval）见
--    docs/ddl/agent_kb_mcp.sql（已在本机执行，见该文件执行记录）。
-- 5) L3 案例库**没有表、没有后端、没有入口**：页面按 FIX-22 如实显示
--    「未启用 · 数据未达标」，判定依据见 docs/agent-knowledge-corpus-inventory.md §3。
-- 6) 菜单入口需重新登录（或清 RuoYi 菜单缓存）才在前端出现——菜单是登录时下发的。
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 执行记录
-- ---------------------------------------------------------------------------
-- 2026-11-04：**已在本机 dkd 库执行**（root@127.0.0.1，MySQL 8.0.40，
--   `mysql --default-character-set=utf8mb4 ... dkd --execute="source F:/DKD/docs/ddl/agent_kb_menu.sql"`）。
--   执行前 9026~9029 为空；执行后校验 4 行（9026 知识库 C / 9027 新增文档 F / 9028 检索测试 F /
--   9029 启停 F），parent/perms 正确；`sys_role_menu` 未插（仅管理员可见，属预期）。菜单需重新登录后可见。

-- ---------------------------------------------------------------------------
-- 回滚（先删角色关联再删菜单本体；顺序反了会留下指向不存在菜单的授权行）
-- ---------------------------------------------------------------------------
-- DELETE FROM sys_role_menu WHERE menu_id BETWEEN 9026 AND 9029;
-- DELETE FROM sys_menu      WHERE menu_id BETWEEN 9026 AND 9029;
--
-- 回滚影响面：
--   · 只移除「知识库」入口与 3 个按钮权限，不动 9000 目录、不动 9001~9025 已有菜单；
--   · **不删** agent_kb_document / agent_kb_chunk / agent_kb_retrieval 的任何数据——
--     入口没了不代表语料与检索留痕该消失；智能体侧读知识库的行为也不受影响；
--   · 前端页面文件保留无害，只是无路由可达；
--   · 若已给角色授权，回滚会一并清掉该角色对本页的可见性。
-- =============================================================================
