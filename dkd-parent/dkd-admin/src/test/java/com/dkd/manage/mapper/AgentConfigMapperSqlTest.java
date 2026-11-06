package com.dkd.manage.mapper;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

/**
 * 智能体配置 Mapper 的**形状回归测试**（排期 2-2）。
 *
 * <p>本文件守的是几条「改坏了也不会报错、只会静默失效」的约定：
 * <ol>
 *   <li>更新必须带 {@code version = #{version}} 条件 + {@code version = version + 1}
 *       ——去掉版本条件，乐观锁就变成装饰；</li>
 *   <li>更新是**全列覆盖**（没有 {@code <if test="xxx != null">}）——否则运营把 temperature
 *       从 1.2 清空回默认时会静默不生效；</li>
 *   <li>查询必须过滤 {@code del_flag='0'}（AGENTS §7.4）；</li>
 *   <li>不得出现 {@code ${}} 拼接（配置值来自人工输入，属不可信输入，AGENTS §7.2）；</li>
 *   <li>历史查询按 version 倒序（页面「版本历史」先看最新）。</li>
 * </ol>
 *
 * <p>纯 XML 文本断言、不连数据库，与既有 {@code AgentDecisionLogMapperSqlTest} 同一套路。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
class AgentConfigMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentConfigMapper.xml";

    private static Document document;

    private static String rawXml;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentConfigMapperSqlTest.class.getClassLoader()
                .getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            // 用 Spring 的 StreamUtils 而不是 InputStream.readAllBytes()：后者是 Java 9+ API，
            // 本项目编译目标是 Java 8（AGENTS §2.1 的硬边界）。
            rawXml = org.springframework.util.StreamUtils.copyToString(in,
                    java.nio.charset.StandardCharsets.UTF_8);
        }
        DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
        document = builder.parse(new java.io.ByteArrayInputStream(
                rawXml.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
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

    private static String rawBlock(String tagName, String id)
    {
        String head = "<" + tagName + " id=" + '"' + id + '"';
        int start = rawXml.indexOf(head);
        assertTrue(start >= 0, "原始 XML 里找不到 " + tagName + " id=" + id);
        int end = rawXml.indexOf("</" + tagName + ">", start);
        assertTrue(end > start, "原始 XML 里 " + id + " 没有闭合标签");
        return rawXml.substring(start, end);
    }

    @Test
    @DisplayName("更新必须带版本条件与 version+1（乐观锁不是装饰）")
    void updateUsesOptimisticLock()
    {
        String update = textOf("update", "updateAgentConfig");
        assertTrue(update.contains("version = version + 1"),
                "更新必须自增版本，否则 agent_run.config_version 回放会指向同一版本\nSQL=" + update);
        assertTrue(update.contains("version = #{version}"),
                "更新必须用提交版本做 CAS\nSQL=" + update);
        assertTrue(update.contains("del_flag = '0'"),
                "更新必须过滤已删除行\nSQL=" + update);
    }

    @Test
    @DisplayName("更新全列覆盖（可空列不允许 <if> 跳过，否则清空语义失效）")
    void updateOverwritesNullableColumns()
    {
        String block = rawBlock("update", "updateAgentConfig");
        assertFalse(block.contains("<if"),
                "更新语句里出现了动态 <if>：temperature/max_tokens/params_json 等清空为 NULL 会静默不生效\nBLOCK="
                        + block);
        for (String column : new String[] { "temperature = #{temperature}", "max_tokens = #{maxTokens}",
                "params_json = #{paramsJson}", "disabled_reason = #{disabledReason}", "system_prompt = #{systemPrompt}" })
        {
            assertTrue(block.contains(column), "更新必须无条件覆盖 " + column + "\nBLOCK=" + block);
        }
    }

    @Test
    @DisplayName("查询默认过滤 del_flag（AGENTS §7.4）")
    void queriesFilterDelFlag()
    {
        assertTrue(textOf("select", "selectAgentConfigById").contains("del_flag = '0'"),
                "详情查询未过滤已删除");
        assertTrue(textOf("sql", "agentConfigWhere").contains("del_flag = '0'"),
                "列表查询未过滤已删除");
        assertTrue(textOf("select", "countAgentConfigByName").contains("del_flag = '0'"),
                "名称唯一性校验未过滤已删除（否则删掉的旧名会一直占位）");
    }

    @Test
    @DisplayName("参数绑定全部 #{}，SQL 语句不得出现 ${}（配置值属不可信输入）")
    void noStringSubstitution()
    {
        // 只看 SQL 语句本身：注释里提到 ${} 不算违规（否则改注释就会误报）
        String sqlOnly = rawXml.replaceAll("(?s)<!--.*?-->", "");
        assertFalse(sqlOnly.contains("${"), "Mapper 里出现了 ${} 拼接：配置值来自人工输入，必须 #{} 绑定");
        String insert = textOf("insert", "insertAgentConfig");
        assertTrue(insert.contains("#{paramsJson}"), "params_json 必须用 #{} 绑定\nSQL=" + insert);
        assertTrue(insert.contains("#{systemPrompt}"), "system_prompt 必须用 #{} 绑定\nSQL=" + insert);
    }

    @Test
    @DisplayName("历史查询按 version 倒序，且只有 INSERT/查询（只增不改）")
    void historyIsAppendOnlyAndOrdered()
    {
        String historyList = textOf("select", "selectAgentConfigHistoryByCode");
        assertTrue(historyList.contains("order by version desc"),
                "版本历史应按版本倒序（页面先看最新）\nSQL=" + historyList);
        String historyInsert = textOf("insert", "insertAgentConfigHistory");
        assertTrue(historyInsert.contains("snapshot"), "历史插入必须写 snapshot\nSQL=" + historyInsert);
        assertFalse(rawXml.contains("<update id=\"updateAgentConfigHistory\""),
                "历史表是审计证据，不应有 update 语句");
        assertFalse(rawXml.contains("<delete id=\"deleteAgentConfigHistory\""),
                "历史表是审计证据，不应有 delete 语句");
    }
}
