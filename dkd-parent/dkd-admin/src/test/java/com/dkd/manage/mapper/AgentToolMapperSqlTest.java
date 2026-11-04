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
 * 工具注册表 Mapper 的**形状回归测试**（排期 3-4）。
 *
 * <p>守的核心是一条红线：**UPDATE SQL 不得触碰 code/source/read_only/builtin**。
 * 这四列是工具的身份事实，其中 {@code read_only=0} 表示写工具、不允许被模型直接调用
 * （AGENTS §7.9）。只在 Service 校验不够——SQL 层也要没有这个能力（纵深防御），
 * 否则将来任何一处"绕过 Service 直接调 Mapper"的代码都能改掉它。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentToolMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentToolMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentToolMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
        try (InputStream in = AgentToolMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
    @DisplayName("列表：过滤 del_flag + scope/source/enabled 参数绑定 + 稳定排序")
    void listIsSoftDeleteAwareAndOrdered() throws Exception
    {
        String where = textOf("sql", "agentToolWhere");
        assertTrue(where.contains("del_flag = '0'"), "查询未过滤已删除（AGENTS §7.4）\nSQL=" + where);
        assertTrue(where.contains("scope = #{scope}"), "scope 过滤应参数绑定\nSQL=" + where);
        assertTrue(where.contains("source = #{source}"), "source 过滤应参数绑定\nSQL=" + where);
        assertTrue(where.contains("enabled = #{enabled}"), "enabled 过滤应参数绑定\nSQL=" + where);

        String list = textOf("select", "selectAgentToolList");
        assertTrue(list.contains("order by scope asc, code asc"), "工具列表应按 scope, code 排序\nSQL=" + list);
    }

    @Test
    @DisplayName("UPDATE 只 set enabled/timeout_ms/row_limit，绝不触碰身份列（红线）")
    void updateOnlyTouchesMutableGuardrailColumns() throws Exception
    {
        String update = textOf("update", "updateAgentTool");

        assertTrue(update.contains("enabled = #{enabled}"), "应更新 enabled\nSQL=" + update);
        assertTrue(update.contains("timeout_ms = #{timeoutMs}"), "应更新 timeout_ms\nSQL=" + update);
        assertTrue(update.contains("row_limit = #{rowLimit}"), "应更新 row_limit\nSQL=" + update);
        assertTrue(update.contains("where id = #{id} and del_flag = '0'"), "更新必须带主键与软删条件\nSQL=" + update);

        // 身份列不得出现在 UPDATE 中：即使 Service 校验被绕过，SQL 也改不了它们
        String updateBlock = rawBlock("update", "updateAgentTool");
        assertFalse(updateBlock.contains("code = #{"),
                "UPDATE 出现了 code：工具编码不可修改（红线）\nBLOCK=" + updateBlock);
        assertFalse(updateBlock.contains("source = #{"),
                "UPDATE 出现了 source：工具来源不可修改\nBLOCK=" + updateBlock);
        assertFalse(updateBlock.contains("read_only = #{"),
                "UPDATE 出现了 read_only：把写工具改成只读等于绕过 AGENTS §7.9（红线）\nBLOCK=" + updateBlock);
        assertFalse(updateBlock.contains("builtin = #{"),
                "UPDATE 出现了 builtin：内置标记不可修改\nBLOCK=" + updateBlock);
    }

    @Test
    @DisplayName("SQL 全部 #{} 绑定，不出现 ${}")
    void noStringInterpolation() throws Exception
    {
        assertFalse(rawXml().contains("${"), "XML 里出现了 ${} 拼接（AGENTS §7.2 红线）");
    }

    @Test
    @DisplayName("Mapper 只有查询 + 受限更新，没有 insert/delete（工具行由 seed/MCP 写入）")
    void mapperExposesLimitedWritesOnly()
    {
        for (Method method : AgentToolMapper.class.getDeclaredMethods())
        {
            String name = method.getName();
            boolean allowed = name.startsWith("select") || "updateAgentTool".equals(name);
            assertTrue(allowed, "Mapper 出现了不允许的方法 " + name + "（工具行由 seed/MCP 写入，后台不增删）");
        }
    }
}
