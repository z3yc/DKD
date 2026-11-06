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
 * 驾驶舱 Mapper 的形状回归测试（排期 6-1）。
 *
 * <p>守三条（都是"一改就静默退化"的约定）：
 * <ol>
 *   <li><b>每条聚合都带时间窗口</b>：漏一个 window 条件就是全表扫描（单主库红线，AGENTS §1/§10）；</li>
 *   <li><b>过滤条件不得用 date_format 包裹索引列</b>（AGENTS §10 明确禁止）；</li>
 *   <li><b>comparison 只读最新一行</b>（order by generated_at desc + limit 1）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentDashboardMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentDashboardMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentDashboardMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
        try (InputStream in = AgentDashboardMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            // 用 Spring 的 StreamUtils 而不是 InputStream.readAllBytes()：后者是 Java 9+ API
            return org.springframework.util.StreamUtils.copyToString(in, java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("每个聚合都带时间窗口（漏一个就是全表扫描）")
    void everyAggregateIsWindowed()
    {
        String[] windowedSelects = { "selectAdoptionAggregate", "selectDegradationAggregate", "selectCostAggregate",
                "selectDailyCost", "selectFailureAggregate", "selectErrorLogCount", "selectPendingAggregate" };
        for (String id : windowedSelects)
        {
            String sql = textOf("select", id);
            // DOM 的 getTextContent() 已解码 XML 实体，所以断言 ``>=``/``<=``。
            // DATE 列（plan_date）用 DATE(#{windowStart}) 对参数取日期，仍属"带窗口"
            assertTrue(sql.contains(">= #{windowStart}") || sql.contains(">= DATE(#{windowStart})"),
                    id + " 缺少 windowStart 条件\nSQL=" + sql);
            assertTrue(sql.contains("<= #{windowEnd}") || sql.contains("<= DATE(#{windowEnd})"),
                    id + " 缺少 windowEnd 条件\nSQL=" + sql);
            int whereIndex = sql.indexOf(" where ");
            assertTrue(whereIndex > 0, id + " 没有 WHERE 子句\nSQL=" + sql);
            int whereEnd = sql.length();
            for (String stop : new String[] { " group by ", " order by " })
            {
                int stopIndex = sql.indexOf(stop, whereIndex);
                if (stopIndex > whereIndex && stopIndex < whereEnd)
                {
                    whereEnd = stopIndex;
                }
            }
            String wherePart = sql.substring(whereIndex, whereEnd).toLowerCase();
            // date_format 只能出现在 SELECT/GROUP BY（daily 分组），出现在 WHERE 会让索引失效
            assertFalse(wherePart.contains("date_format("),
                    id + " 的过滤条件用了 date_format 包裹索引列（AGENTS §10）\nSQL=" + sql);
        }
    }

    @Test
    @DisplayName("plan_date 是 DATE 列：对参数取 DATE() 比较，避免字符串边界失配")
    void planDateComparedWithDateFunction()
    {
        String degradation = textOf("select", "selectDegradationAggregate");
        assertTrue(degradation.contains("plan_date >= DATE(#{windowStart})"),
                "plan_date 下界应对参数取 DATE()\nSQL=" + degradation);
        assertTrue(degradation.contains("plan_date <= DATE(#{windowEnd})"),
                "plan_date 上界应对参数取 DATE()\nSQL=" + degradation);

        String pending = textOf("select", "selectPendingAggregate");
        assertTrue(pending.contains("status in (1, 2, 6)"), "待处理必须是 status 1/2/6\nSQL=" + pending);
        assertTrue(pending.contains(">= #{thresholdMinutes}"), "超时阈值必须参数化\nSQL=" + pending);
    }

    @Test
    @DisplayName("失败率分母只含终态运行（running/waiting_confirm 不进分母）")
    void failureDenominatorIsTerminalRunsOnly()
    {
        String failure = textOf("select", "selectFailureAggregate");

        assertTrue(failure.contains("status in ('done', 'failed', 'interrupted')"),
                "分母必须只含终态运行\nSQL=" + failure);
        assertFalse(failure.contains("'running'"), "running 不能进分母\nSQL=" + failure);
        assertFalse(failure.contains("waiting_confirm"), "waiting_confirm 不能进分母\nSQL=" + failure);
    }

    @Test
    @DisplayName("对比卡只读最新一行（order by generated_at desc + limit 1）")
    void compareReadsLatestRowOnly()
    {
        String compare = textOf("select", "selectLatestCompareReport");

        assertTrue(compare.contains("order by generated_at desc"), "对比报告必须按生成时间倒序\nSQL=" + compare);
        assertTrue(compare.toLowerCase().contains("limit 1"), "对比卡只取最新一行\nSQL=" + compare);
    }

    @Test
    @DisplayName("全部 #{} 绑定，无 ${}；Mapper 只有查询方法")
    void noInterpolationAndQueriesOnly() throws Exception
    {
        assertFalse(rawXml().contains("${"), "XML 里出现了 ${} 拼接（AGENTS §7.2 红线）");

        for (Method method : AgentDashboardMapper.class.getDeclaredMethods())
        {
            assertTrue(method.getName().startsWith("select"),
                    "驾驶舱 Mapper 只应有查询方法，出现：" + method.getName());
        }
    }
}
