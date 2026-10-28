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
 * P1-8 报表设备统计下推 SQL 的**形状回归测试**。
 *
 * <p>为什么需要（AGENTS §8：改造必须带防回归测试）：
 * 原实现把全量设备实体拉进内存逐台数状态/解析心跳 JSON，设备量上千后每次日报/周报
 * 都是一次全表物化。下推成两条聚合 SQL 后，最容易回退/写错的点有三个：
 * <ol>
 *   <li>在线判断丢了 {@code JSON_VALID} 前置——坏心跳会让整条 COUNT 直接报错，日报失败；
 *       保留它则坏 JSON 天然按离线统计，与 Java 版「解析失败记 debug 按离线」等价；</li>
 *   <li>故障口径退回 {@code vm_status != 1}——未投放(0)/已撤机(3) 是正常生命周期，
 *       算成故障会让故障率虚高（业务规范 §4.5，同 bug 曾在 Service 层修过一次）；</li>
 *   <li>聚合 SQL 被改回 {@code select *}——全量物化回归。</li>
 * </ol>
 *
 * <p>为什么不连真库：与 {@code ReportMapperSqlTest} 同一理由——Java 侧门禁是纯单测，
 * SQL 形状在文本层即可拦住；JSON 函数对拍（真库 EXPLAIN）留待 Phase C 验收。
 */
class VendingMachineMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/VendingMachineMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = VendingMachineMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH + "（dkd-manage 资源是否在测试类路径上？）");
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
        throw new AssertionError("VendingMachineMapper.xml 中找不到 statement id=" + statementId);
    }

    @Test
    @DisplayName("状态分组必须是 GROUP BY 聚合，不能退回 select * 全量物化")
    void statusCountMustBeGroupByAggregate()
    {
        String sql = sqlOf("countGroupByVmStatus").toLowerCase(Locale.ROOT);
        assertTrue(sql.contains("group by vm_status"), "必须按 vm_status 分组\nSQL=" + sql);
        assertTrue(sql.contains("count(*)"), "必须聚合计数\nSQL=" + sql);
        assertFalse(sql.contains("select *"), "不能退回全量拉取\nSQL=" + sql);
    }

    @Test
    @DisplayName("在线判断必须 JSON_VALID 前置（坏心跳按离线，而不是让 COUNT 报错）")
    void onlineCountMustGuardJsonValid()
    {
        String sql = sqlOf("countOnlineByRunningStatus").toLowerCase(Locale.ROOT);
        assertTrue(sql.contains("json_valid(running_status)"),
                "缺 JSON_VALID 前置：一条坏心跳 JSON_EXTRACT 直接抛错，整张日报失败\nSQL=" + sql);
        assertTrue(sql.contains("$.status"),
                "必须取心跳 JSON 的 $.status 字段（与设备列表页口径一致）\nSQL=" + sql);
        assertFalse(sql.contains("vm_status != 1") && sql.contains("select *"),
                "不能退回全量拉取\nSQL=" + sql);
    }

    @Test
    @DisplayName("两条聚合查询必须都存在（防止被误删后测试静默通过）")
    void aggregateStatementsExist()
    {
        assertNotNull(sqlOf("countGroupByVmStatus"));
        assertNotNull(sqlOf("countOnlineByRunningStatus"));
    }
}
