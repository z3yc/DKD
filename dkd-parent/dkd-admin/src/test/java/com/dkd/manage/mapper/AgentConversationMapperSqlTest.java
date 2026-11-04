package com.dkd.manage.mapper;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.lang.reflect.Method;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

/**
 * 会话/消息 Mapper 的**形状回归测试**（Batch A · 排期 2-5）。
 *
 * <p>守四条一改就会悄悄退化的约定：
 * <ol>
 *   <li><b>会话列表不 join 全表聚合</b>：单主库下 {@code left join (select ... group by ...)}
 *       会让每次翻页都聚合整张 {@code agent_message}，越用越慢。聚合必须是按当前页 ID 的
 *       {@code IN} 批量查询（见 {@code AgentConversationMapper} 的注释）；</li>
 *   <li><b>时间窗无条件、范围比较、不用 date_format()</b>（同一套护栏，AGENTS §10）；</li>
 *   <li><b>消息时间线必须 LIMIT</b>（长会话无界返回在单主库上是红线）；</li>
 *   <li><b>查询过滤 del_flag 且 Mapper 只有查询方法</b>（AGENTS §7.3/§7.4）。</li>
 * </ol>
 *
 * <p>纯 XML 文本断言、不连数据库（与 {@code AgentDecisionLogMapperSqlTest} 同一套路）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentConversationMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentConversationMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentConversationMapperSqlTest.class.getClassLoader()
                .getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
            document = builder.parse(in);
        }
    }

    private static String textOf(String tagName, String id)
    {
        NodeList nodes = document.getElementsByTagName(tagName);
        for (int i = 0; i < nodes.getLength(); i++)
        {
            Element element = (Element) nodes.item(i);
            if (id.equals(element.getAttribute("id")))
            {
                return element.getTextContent().replaceAll("\\s+", " ").trim();
            }
        }
        throw new AssertionError(RESOURCE_PATH + " 中找不到 " + tagName + " id=" + id);
    }

    private static String rawXml() throws Exception
    {
        try (InputStream in = AgentConversationMapperSqlTest.class.getClassLoader()
                .getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            // 用 Spring 的 StreamUtils 而不是 InputStream.readAllBytes()：后者是 Java 9+ API
            return org.springframework.util.StreamUtils.copyToString(in, java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    private static String rawBlock(String tagName, String id) throws Exception
    {
        String raw = rawXml();
        String head = "<" + tagName + " id=" + '"' + id + '"';
        int start = raw.indexOf(head);
        assertTrue(start >= 0, "原始 XML 里找不到 " + tagName + " id=" + id);
        int end = raw.indexOf("</" + tagName + ">", start);
        assertTrue(end > start, "原始 XML 里 " + id + " 没有闭合标签");
        return raw.substring(start, end);
    }

    @Test
    @DisplayName("会话列表：过滤 del_flag + 时间窗范围比较，不得 date_format()")
    void conversationQueryIsWindowedAndSoftDeleteAware() throws Exception
    {
        String where = textOf("sql", "agentConversationWhere");

        assertTrue(where.contains("del_flag = '0'"), "会话查询未过滤已删除数据（AGENTS §7.4）\nSQL=" + where);
        assertFalse(where.toLowerCase().contains("date_format("),
                "WHERE 里出现了 date_format()：函数包裹索引列会让会话索引失效（AGENTS §10）\nSQL=" + where);
        assertTrue(where.contains("create_time >= #{window.beginTime}"),
                "缺少开始时间的范围条件\nSQL=" + where);
        assertTrue(where.contains("create_time <= #{window.endTime}"),
                "上界应为闭区间（Java 侧已补齐到请求精度末尾）\nSQL=" + where);

        String whereBlock = rawBlock("sql", "agentConversationWhere");
        assertFalse(whereBlock.contains("test=\"window"),
                "时间窗被 <if> 包住了：绕过 Service 直接调 Mapper 就会变成无界查询\nBLOCK=" + whereBlock);
    }

    @Test
    @DisplayName("会话列表不 join 全表聚合（单主库：聚合必须按当前页 ID 批量查）")
    void conversationListMustNotJoinAggregate() throws Exception
    {
        String listBlock = rawBlock("select", "selectAgentConversationList");
        assertFalse(listBlock.toLowerCase().contains(" join "),
                "会话列表不要 join 聚合子查询（每次翻页都会聚合整张 agent_message）\nBLOCK=" + listBlock);

        String aggregate = textOf("select", "selectAgentMessageAggregates");
        assertTrue(aggregate.contains("conversation_id in"),
                "聚合必须按会话ID列表过滤（IN），不能全表 group by\nSQL=" + aggregate);
        assertTrue(aggregate.contains("group by conversation_id"),
                "聚合需要 group by conversation_id\nSQL=" + aggregate);
        assertTrue(aggregate.contains("tool_calls is not null"),
                "工具调用数口径应为 tool_calls 非空\nSQL=" + aggregate);
        assertTrue(aggregate.contains("sum(tokens_in)") && aggregate.contains("sum(tokens_out)"),
                "token 合计应来自 sum(tokens_in/out)\nSQL=" + aggregate);
    }

    @Test
    @DisplayName("会话聚合同样过滤 del_flag，且不得用 ${} 拼接 IN 列表")
    void aggregateIsSoftDeleteAwareAndParameterized() throws Exception
    {
        String aggregate = textOf("select", "selectAgentMessageAggregates");
        assertTrue(aggregate.contains("del_flag = '0'"), "聚合未过滤已删除消息（AGENTS §7.4）\nSQL=" + aggregate);

        String aggregateBlock = rawBlock("select", "selectAgentMessageAggregates");
        assertTrue(aggregateBlock.contains("<foreach"), "IN 列表应使用 foreach 展开\nBLOCK=" + aggregateBlock);
        assertFalse(aggregateBlock.contains("${"), "IN 列表不得用 ${} 拼接（AGENTS §7.2）\nBLOCK=" + aggregateBlock);
    }

    @Test
    @DisplayName("消息时间线：按 seq 升序 + 必带 LIMIT（长会话不能无界返回）")
    void messageTimelineIsOrderedAndBounded() throws Exception
    {
        String messages = textOf("select", "selectAgentMessagesByConversationId");

        assertTrue(messages.contains("conversation_id = #{conversationId}"),
                "消息查询必须按会话ID精确过滤且参数绑定\nSQL=" + messages);
        assertTrue(messages.contains("del_flag = '0'"), "消息查询未过滤已删除（AGENTS §7.4）\nSQL=" + messages);
        assertTrue(messages.contains("order by seq asc"), "时间线必须按会话内序号升序\nSQL=" + messages);
        assertTrue(messages.toLowerCase().contains("limit #{limit}"),
                "消息查询必须有 LIMIT（无界查询在单主库上是红线）\nSQL=" + messages);
    }

    @Test
    @DisplayName("会话详情按 conversation_id 查询且过滤 del_flag、只取一条")
    void detailQueryIsExactAndBounded()
    {
        String detail = textOf("select", "selectAgentConversationByConversationId");

        assertTrue(detail.contains("conversation_id = #{conversationId}"),
                "详情必须按业务会话ID精确查询\nSQL=" + detail);
        assertTrue(detail.contains("del_flag = '0'"), "详情查询未过滤已删除（AGENTS §7.4）\nSQL=" + detail);
        assertTrue(detail.toLowerCase().contains("limit 1"), "详情应 limit 1\nSQL=" + detail);
    }

    @Test
    @DisplayName("XML 里不得出现 ${}（全部 #{} 绑定）")
    void noStringInterpolationAnywhere() throws Exception
    {
        assertFalse(rawXml().contains("${"), "XML 里出现了 ${} 拼接（AGENTS §7.2 红线）");
    }

    @Test
    @DisplayName("Mapper 接口只暴露查询方法（会话/消息由智能体服务写入）")
    void mapperExposesQueriesOnly()
    {
        for (Method method : AgentConversationMapper.class.getDeclaredMethods())
        {
            assertTrue(method.getName().startsWith("select"),
                    "Mapper 出现了非查询方法 " + method.getName() + " —— 管理端不应改会话/消息（AGENTS §7.3）");
        }
    }
}
