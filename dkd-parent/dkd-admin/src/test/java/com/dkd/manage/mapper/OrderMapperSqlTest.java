package com.dkd.manage.mapper;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.util.Locale;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

/**
 * 订单列表 SQL 的**形状回归测试**（P1-9 防回归）。
 *
 * <p>防什么：{@code selectOrderList} 的时间过滤原先用
 * {@code date_format(create_time,'%y%m%d')} 包裹索引列——函数作用在列上会让
 * {@code tb_order.create_time} 索引失效、按时间查单退化为全表扫描。
 * 同仓 ReportMapper / AgentRunMapper / AgentDecisionLogMapper / InventoryLogMapper
 * 均已统一半开区间写法并在文件头注释禁用旧写法，此处是漏网之鱼（AGENTS §10）。
 *
 * <p>为什么纯文本断言而不连真库：与 {@code ReportMapperSqlTest} 同一理由——
 * Java 侧门禁不依赖 MySQL；索引是否真被命中属 DBA/EXPLAIN 验收范畴（Phase C）。
 */
class OrderMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/OrderMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = OrderMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
            document = builder.parse(in);
        }
    }

    private static String sqlOf(String statementId)
    {
        String[] tags = new String[] { "select", "insert", "update", "delete" };
        for (String tag : tags)
        {
            NodeList nodes = document.getElementsByTagName(tag);
            for (int i = 0; i < nodes.getLength(); i++)
            {
                Element element = (Element) nodes.item(i);
                if (statementId.equals(element.getAttribute("id")))
                {
                    return element.getTextContent().replaceAll("\\s+", " ").trim();
                }
            }
        }
        throw new AssertionError("OrderMapper.xml 中找不到 statement id=" + statementId);
    }

    @Test
    @DisplayName("禁止 date_format() 包裹 create_time（索引失效，P1-9 修掉的 bug）")
    void mustNotWrapCreateTimeWithFunction()
    {
        String sql = sqlOf("selectOrderList").toLowerCase(Locale.ROOT);
        assertFalse(sql.contains("date_format("),
                "对 create_time 用 date_format() 会让索引失效退化为全表扫描\nSQL=" + sql);
    }

    @Test
    @DisplayName("时间过滤必须是半开区间：>= beginTime 且 < endTime + 1 天")
    void timeRangeMustBeHalfOpenInterval()
    {
        String sql = sqlOf("selectOrderList");
        assertTrue(sql.contains("create_time >= #{params.beginTime}"),
                "下界必须是 >= #{params.beginTime}\nSQL=" + sql);
        assertTrue(sql.contains("create_time < DATE_ADD(#{params.endTime}, INTERVAL 1 DAY)"),
                "上界必须是 < endTime + 1 天（前端传 yyyy-MM-dd，直接 < endTime 会漏掉结束日当天的订单）\nSQL=" + sql);
    }
}
