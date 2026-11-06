package com.dkd.manage.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.dkd.manage.service.impl.AgentConfigServiceImpl;

/**
 * 知识库工具白名单同步测试（排期 3-6）。
 *
 * <p>为什么单独测：配置台能勾选哪些工具，取决于 {@link AgentConfigDefaults#TOOL_WHITELIST_BY_RUNTIME}；
 * 如果 Python 侧新增了 {@code search_knowledge_base} 而 Java 白名单没同步，
 * 运营在配置台根本选不到它（页面下拉里没有），表现为"知识库能力配不出来"——
 * 而且两边都不会报错。这里把"契约已同步"钉成断言。
 *
 * <p>同时验证 meta 接口自动带出：meta 的 tools 段直接取白名单常量，不需要额外改代码。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentConfigDefaultsKbToolTest
{
    @Test
    @DisplayName("chat 工具白名单包含 search_knowledge_base（与 Python 侧新增工具同步）")
    void chatWhitelistContainsKnowledgeBaseTool()
    {
        List<String> chatTools = AgentConfigDefaults.TOOL_WHITELIST_BY_RUNTIME
                .get(AgentConfigDefaults.RUNTIME_CHAT);

        assertThat(chatTools).contains("search_knowledge_base");
        // 白名单必须保持"可读且稳定"的字典序：新增工具追加到末尾，避免顺序抖动影响前端 diff
        assertThat(chatTools).isSorted();
    }

    @Test
    @DisplayName("meta 接口（selectMeta）自动带出新工具，前端无需发版")
    void metaExposesKnowledgeBaseTool()
    {
        AgentConfigServiceImpl service = new AgentConfigServiceImpl();

        Map<String, Object> meta = service.selectMeta(AgentConfigDefaults.RUNTIME_CHAT);

        @SuppressWarnings("unchecked")
        List<String> tools = (List<String>) meta.get("tools");
        assertThat(tools).isNotNull().contains("search_knowledge_base");
    }
}
