-- =============================================================================
-- 智能体 · 知识库检索工具 seed（排期 3-6）
-- =============================================================================
-- 目的：把「知识库检索」注册为工具注册表中的一个只读工具，运营可在工具中心启停，
--       配置台自定义 chat 智能体也可勾选（Java meta 白名单需同步，见 AgentConfigDefaults）。
-- 前置：agent_tool 表已建（docs/ddl/agent_tool_llm_profile.sql），知识库表已建
--       （docs/ddl/agent_kb_mcp.sql）。
-- 幂等性：INSERT IGNORE（code 唯一）；重跑不覆盖运营已改的启停/超时/行数。
-- 回滚：
--   DELETE FROM agent_tool WHERE code='search_knowledge_base';
--   （仅限未上线/未人工修改环境；生产回滚前先确认无智能体配置引用该工具，
--     否则自定义智能体的 tools 会指向不存在工具——Python 侧会过滤 + WARN，不会崩）
-- =============================================================================

SET NAMES utf8mb4;

INSERT IGNORE INTO `agent_tool`
  (`code`, `name`, `description`, `scope`, `source`, `read_only`, `enabled`, `timeout_ms`, `row_limit`, `builtin`,
   `create_by`, `create_time`, `update_time`, `del_flag`)
VALUES
  ('search_knowledge_base', '知识库检索',
   '检索知识库 L2 文档语料片段（返回相似度/文档编码/摘要）；无命中时如实说明，不得编造依据',
   'chat', 'local', 1, 1, 10000, 2000, 1, 'system', NOW(), NOW(), '0');

-- 校验：应返回 1 行，read_only=1、enabled=1
-- SELECT code, enabled, read_only, timeout_ms, row_limit FROM agent_tool WHERE code='search_knowledge_base';

-- 执行记录
-- 2026-11-04：已在本机 dkd 库执行（root@127.0.0.1），agent_tool 9 行（8 旧 + search_knowledge_base）；
--   Python 重启后 /health.tool_registry_enabled=9、source=db；知识库工具在 scene=3 对话中被真实调用
--   （回答带 [KB:<docCode>] 引用，检索留痕写入 agent_kb_retrieval）。
