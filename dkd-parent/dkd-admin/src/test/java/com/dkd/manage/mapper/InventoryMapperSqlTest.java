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
 * P1-11 预警候选合并 SQL 的形状回归测试（纯文本层，不连真库）。
 *
 * <p>防什么：selectAlertCandidates 是「低库存 ∪ 缺货」的单条 OR 合并查询，
 * 替代原先两次无条件全表 + Service 内存 Set 去重。容易退化的点：
 * <ol>
 *   <li>丢掉 OR 第二支——min_stock 为 NULL 的缺货行（不满足 &lt;= min_stock）会漏报；</li>
 *   <li>退回 select * 或丢 order by——排序保证「缺货（0 即最小值）在前」与旧合并序等价；</li>
 *   <li>被改成函数包裹列等索引失效写法。</li>
 * </ol>
 */
class InventoryMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/InventoryMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = InventoryMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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
        throw new AssertionError("InventoryMapper.xml 中找不到 statement id=" + statementId);
    }

    @Test
    @DisplayName("预警候选必须是 OR 合并：低库存 ∪ 缺货，两支都不能丢")
    void alertCandidatesMustMergeLowAndOutOfStockWithOr()
    {
        String sql = sqlOf("selectAlertCandidates").toLowerCase(Locale.ROOT);
        assertTrue(sql.contains("i.current_stock <= i.min_stock"),
                "缺低库存分支（min_stock 为 NULL 的行靠 OR 第二支兜底）\nSQL=" + sql);
        assertTrue(sql.contains("or i.current_stock = 0"),
                "缺缺货分支——丢掉会漏报 min_stock 为 NULL 的缺货行\nSQL=" + sql);
        assertTrue(sql.contains("order by i.current_stock asc"),
                "排序保证缺货（0 即最小值）在前，与旧合并序等价\nSQL=" + sql);
        assertFalse(sql.contains("date_format("),
                "禁止函数包裹索引列（AGENTS §10）\nSQL=" + sql);
    }

    @Test
    @DisplayName("旧的两条全表查询保留原口径（/low、/outofstock 接口仍在用）")
    void legacyLowAndOutOfStockQueriesKeepCaliber()
    {
        String low = sqlOf("selectLowInventoryList").toLowerCase(Locale.ROOT);
        assertTrue(low.contains("i.current_stock <= i.min_stock"), "低库存口径不能变\nSQL=" + low);
        String out = sqlOf("selectOutOfStockList").toLowerCase(Locale.ROOT);
        assertTrue(out.contains("i.current_stock = 0"), "缺货口径不能变\nSQL=" + out);
    }
}
