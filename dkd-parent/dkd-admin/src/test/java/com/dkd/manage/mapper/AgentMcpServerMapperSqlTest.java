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
 * MCP server 配置 Mapper 的**形状回归测试**（排期 3-7）。
 *
 * <p>守三条：
 * <ol>
 *   <li><b>UPDATE 不碰 status / last_probe_* / code</b>：前三列是 Python 探测后回写的运行时结论
 *       （双写会让"最近探测结果"无法解释），code 是配置身份（Python 按 code 调 probe/sync）；</li>
 *   <li><b>没有密钥列</b>：env_keys_json 只存变量名映射，SQL 里不得出现 secret/api_key_value；</li>
 *   <li><b>没有 DELETE</b>：停用即可，物理删除会让探测记录/工具同步结果悬空（AGENTS §7.4 精神）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentMcpServerMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentMcpServerMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentMcpServerMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
        try (InputStream in = AgentMcpServerMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
    @DisplayName("列表：过滤 del_flag、enabled 参数绑定、按最近更新倒序")
    void listIsSoftDeleteAwareAndOrdered()
    {
        String where = textOf("sql", "agentMcpServerWhere");
        assertTrue(where.contains("del_flag = '0'"), "查询未过滤已删除（AGENTS §7.4）\nSQL=" + where);
        assertTrue(where.contains("enabled = #{enabled}"), "enabled 过滤应参数绑定\nSQL=" + where);

        String list = textOf("select", "selectAgentMcpServerList");
        assertTrue(list.contains("order by update_time desc, id desc"), "列表应按最近更新倒序\nSQL=" + list);
    }

    @Test
    @DisplayName("UPDATE 只覆盖配置列：不碰 code / status / last_probe_*（Python 回写列）")
    void updateOnlyTouchesConfigColumns() throws Exception
    {
        String update = textOf("update", "updateAgentMcpServer");
        assertTrue(update.contains("env_keys_json = #{envKeysJson}"), "应更新 env_keys_json\nSQL=" + update);
        assertTrue(update.contains("read_only_only = #{readOnlyOnly}"), "应更新 read_only_only\nSQL=" + update);
        assertTrue(update.contains("where id = #{id} and del_flag = '0'"), "更新必须带主键与软删条件");

        String updateBlock = rawBlock("update", "updateAgentMcpServer");
        assertFalse(updateBlock.contains("code = #{"),
                "UPDATE 出现了 code：server 编码是配置身份，不可修改\nBLOCK=" + updateBlock);
        assertFalse(updateBlock.contains("status = #{"),
                "UPDATE 出现了 status：该列由 Python 探测后回写，Java 双写会让最近探测结果无法解释\nBLOCK=" + updateBlock);
        assertFalse(updateBlock.contains("last_probe_time = #{"),
                "UPDATE 出现了 last_probe_time：探测结论归 Python 写\nBLOCK=" + updateBlock);
        assertFalse(updateBlock.contains("last_probe_msg = #{"),
                "UPDATE 出现了 last_probe_msg：探测结论归 Python 写\nBLOCK=" + updateBlock);
    }

    @Test
    @DisplayName("没有密钥列：只允许 env_keys_json（变量名映射）")
    void noSecretColumns() throws Exception
    {
        String raw = rawXml().toLowerCase();
        assertTrue(raw.contains("env_keys_json"), "应承载环境变量名映射");
        assertFalse(raw.contains("secret_value"), "出现 secret_value 列（密钥不得入库，AGENTS §7.1）");
        assertFalse(raw.contains("api_key_value"), "出现 api_key_value 列（密钥不得入库）");
        assertFalse(raw.contains("${"), "XML 里出现了 ${} 拼接（AGENTS §7.2 红线）");
    }

    @Test
    @DisplayName("Mapper 只有查询/新增/更新（无 DELETE：停用即可）")
    void mapperExposesNoDelete()
    {
        for (Method method : AgentMcpServerMapper.class.getDeclaredMethods())
        {
            String name = method.getName();
            boolean allowed = name.startsWith("select") || name.startsWith("insert") || name.startsWith("update")
                    || name.startsWith("count");
            assertTrue(allowed, "MCP server Mapper 出现了不允许的方法 " + name + "（配置不开放物理删除）");
        }
    }
}
