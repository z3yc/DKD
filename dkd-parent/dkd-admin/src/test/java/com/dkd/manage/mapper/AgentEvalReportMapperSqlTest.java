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
 * 评测报告 Mapper 的形状回归测试（排期 5-1）。
 *
 * <p>守三条：
 * <ol>
 *   <li><b>只增不改</b>：只有 INSERT + SELECT，没有 UPDATE/DELETE（能改的报告不能当护栏证据）；</li>
 *   <li><b>按版本取最新且不过滤 status</b>：护栏必须能看到"最新是 blocked/failed"，
 *       只查 scored 会把"跑挂了"表现成"从未评测"；</li>
 *   <li><b>LIMIT 参数化且有限制</b>：列表 limit 由 Service 限制 1~100，SQL 用 #{}（AGENTS §7.2）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentEvalReportMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentEvalReportMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentEvalReportMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
        try (InputStream in = AgentEvalReportMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            // 用 Spring 的 StreamUtils 而不是 InputStream.readAllBytes()：后者是 Java 9+ API
            return org.springframework.util.StreamUtils.copyToString(in, java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("INSERT：落库字段含基线/计数/report_json，不含任何密钥列")
    void insertCarriesScoringColumns() throws Exception
    {
        String insert = textOf("insert", "insertAgentEvalReport");

        assertTrue(insert.contains("config_code") && insert.contains("config_version"), "应落库配置定位\nSQL=" + insert);
        assertTrue(insert.contains("report_json"), "应落库完整报告\nSQL=" + insert);
        assertTrue(insert.contains("baseline_accuracy") && insert.contains("baseline_n"),
                "应落库基线（可为 NULL，不伪造）\nSQL=" + insert);
        String raw = rawXml().toLowerCase();
        assertFalse(raw.contains("secret_value"), "出现 secret 列（密钥不得入库）");
        assertFalse(raw.contains("api_key_value"), "出现 api_key 列（密钥不得入库）");
    }

    @Test
    @DisplayName("按配置取最近 N 条：倒序 + LIMIT 参数化")
    void latestByCodeIsOrderedAndBounded()
    {
        String byCode = textOf("select", "selectLatestByCode");

        assertTrue(byCode.contains("config_code = #{configCode}"), "按配置编码过滤且参数绑定\nSQL=" + byCode);
        assertTrue(byCode.contains("order by create_time desc"), "列表必须按时间倒序\nSQL=" + byCode);
        assertTrue(byCode.toLowerCase().contains("limit #{limit}"), "LIMIT 必须参数化\nSQL=" + byCode);
    }

    @Test
    @DisplayName("按版本取最新：不过滤 status（护栏要能看到 blocked/failed），且 limit 1")
    void latestForVersionDoesNotFilterStatus()
    {
        String byVersion = textOf("select", "selectLatestForVersion");

        assertTrue(byVersion.contains("config_code = #{configCode} and config_version = #{configVersion}"),
                "按 (code, version) 定位且参数绑定\nSQL=" + byVersion);
        assertFalse(byVersion.contains("status ="), "不得过滤 status：否则'跑挂了'会被误报成'从未评测'\nSQL=" + byVersion);
        assertTrue(byVersion.toLowerCase().contains("limit 1"), "只取最新一条\nSQL=" + byVersion);
        assertTrue(byVersion.contains("order by create_time desc"), "必须按时间倒序\nSQL=" + byVersion);
    }

    @Test
    @DisplayName("XML 不得出现 ${}（全部 #{} 绑定）")
    void noStringInterpolation() throws Exception
    {
        assertFalse(rawXml().contains("${"), "XML 里出现了 ${} 拼接（AGENTS §7.2 红线）");
    }

    @Test
    @DisplayName("Mapper 只有 INSERT/SELECT（报告只增不改）")
    void mapperIsAppendOnly()
    {
        for (Method method : AgentEvalReportMapper.class.getDeclaredMethods())
        {
            String name = method.getName();
            boolean allowed = name.startsWith("select") || name.startsWith("insert");
            assertTrue(allowed, "评测报告 Mapper 出现了不允许的方法 " + name + "（报告只增不改）");
        }
    }
}
