package com.dkd.manage.mapper;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

/**
 * 报表聚合 SQL 的**形状回归测试**（FIX-3 防回归，排期 1-12 前置）。
 *
 * <p>为什么需要这个测试（AGENTS §8：修复 bug 必须带防回归测试）：
 * {@code ReportMapper.java:27-33} 声明了 {@code @Param("status"/"beginTime"/"endTime")}，
 * 但 {@code ReportMapper.xml} 里的四条聚合 SQL 只写了 {@code where status &gt;= 1}，
 * 三个入参**从来没被用过**。后果是「AI 报表分析」页的营收/订单数/榜单与所选时间窗
 * 完全无关（统计的是历史全量），并且把「出货失败(3)」「失效(4)」也算进营收——
 * 任务 1-12 的 Agent 对账因此永远对不上。这个 bug 靠人工评审发现，代价很大。
 *
 * <p>为什么用「SQL 形状断言」而不是跑真库：
 * 本项目的 Java 侧门禁是**纯单测**（不依赖 MySQL/Redis/Spring 上下文，见排期质量门禁表），
 * 而 Mapper 绑定的缺失恰恰可以在纯文本层被发现。这与 Python 侧
 * {@code tests/test_read_tools.py} 断言 SQL 形状的做法一致——同一类 bug 用同一类断言拦住。
 *
 * <p>覆盖的四个拒绝路径/不变量：
 * <ol>
 *   <li>四条聚合 SQL 都必须绑定 {@code #{status}}/{@code #{beginTime}}/{@code #{endTime}}；</li>
 *   <li>状态必须是**等值**（枚举语义），不能退回 {@code >=}；</li>
 *   <li>上界必须用 {@code <}（半开区间），避免跨期重复计数；</li>
 *   <li>禁止 {@code date_format()} 包裹索引列（AGENTS §10 点名的索引失效反例）。</li>
 * </ol>
 */
class ReportMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/ReportMapper.xml";

    /** 四条聚合查询的 id → 期望绑定的参数（与 ReportMapper.java 的 @Param 一一对应） */
    private static final Map<String, String[]> AGGREGATE_STATEMENTS = new LinkedHashMap<>();

    static
    {
        AGGREGATE_STATEMENTS.put("sumRevenueByStatusAndDateRange",
                new String[] { "#{status}", "#{beginTime}", "#{endTime}" });
        AGGREGATE_STATEMENTS.put("countOrdersByStatusAndDateRange",
                new String[] { "#{status}", "#{beginTime}", "#{endTime}" });
        AGGREGATE_STATEMENTS.put("topNodesByRevenue",
                new String[] { "#{status}", "#{beginTime}", "#{endTime}" });
        AGGREGATE_STATEMENTS.put("topProductsBySales",
                new String[] { "#{status}", "#{beginTime}", "#{endTime}" });
    }

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = ReportMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH + "（dkd-manage 资源是否在测试类路径上？）");
            DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
            document = builder.parse(in);
        }
    }

    /** 取出某个 statement 的 SQL 文本（去掉多余空白，便于断言子串；select/insert/update 都支持）。 */
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
        throw new AssertionError("ReportMapper.xml 中找不到 statement id=" + statementId);
    }

    @Test
    @DisplayName("四条聚合 SQL 必须绑定 status/beginTime/endTime（FIX-3 核心回归点）")
    void aggregateStatementsMustBindAllDeclaredParams()
    {
        for (Map.Entry<String, String[]> entry : AGGREGATE_STATEMENTS.entrySet())
        {
            String sql = sqlOf(entry.getKey());
            for (String param : entry.getValue())
            {
                assertTrue(sql.contains(param),
                        entry.getKey() + " 未绑定 " + param + "（接口声明了却不用，报表数字会与时间窗无关）\nSQL=" + sql);
            }
        }
    }

    @Test
    @DisplayName("状态必须是等值匹配，不能退回 status >= （枚举语义：2=出货成功）")
    void statusMustBeEqualityNotGreaterOrEqual()
    {
        for (String id : AGGREGATE_STATEMENTS.keySet())
        {
            String sql = sqlOf(id);
            assertTrue(sql.contains("status = #{status}"),
                    id + " 的状态条件不是等值匹配；用 >= 会把「出货失败(3)」「失效(4)」算进营收\nSQL=" + sql);
            assertFalse(sql.contains("status >= 1"),
                    id + " 退回了写死的 status >= 1（这正是 FIX-3 修掉的 bug）\nSQL=" + sql);
        }
    }

    @Test
    @DisplayName("时间上界必须是半开区间 < ，避免相邻两期重复计数")
    void upperBoundMustBeExclusive()
    {
        for (String id : AGGREGATE_STATEMENTS.keySet())
        {
            String sql = sqlOf(id);
            assertTrue(sql.contains("create_time >= #{beginTime}"), id + " 缺少开始时间范围条件\nSQL=" + sql);
            assertTrue(sql.contains("create_time < #{endTime}"),
                    id + " 的上界必须是 < #{endTime}（endTime 为下一期零点，用 <= 会跨期重复计数）\nSQL=" + sql);
            assertFalse(sql.contains("create_time <= #{endTime}"), id + " 用了 <= 上界\nSQL=" + sql);
        }
    }

    @Test
    @DisplayName("禁止 date_format() 包裹索引列（AGENTS §10 索引失效反例）")
    void mustNotWrapIndexedColumnWithFunction()
    {
        for (String id : AGGREGATE_STATEMENTS.keySet())
        {
            String sql = sqlOf(id).toLowerCase();
            assertFalse(sql.contains("date_format("),
                    id + " 对 create_time 用了 date_format()，会让索引失效（AGENTS §10）\nSQL=" + sql);
        }
    }

    @Test
    @DisplayName("四条聚合查询必须都存在（防止被误删后测试静默通过）")
    void allAggregateStatementsExist()
    {
        for (String id : AGGREGATE_STATEMENTS.keySet())
        {
            assertNotNull(sqlOf(id), id + " 不存在");
        }
        assertEquals(4, AGGREGATE_STATEMENTS.size(), "聚合查询数量变化时请同步本测试");
    }

    /**
     * 低库存口径（业务规范 §4.5）：`count(distinct vm_id)` where `current_stock <= min_stock`。
     *
     * <p>原 SQL 是 `count(*) ... where current_stock = min_stock`：
     * ① 统计的是库存**行数**，一台设备多个货道会被重复计数（指标名却叫“库存不足设备数”）；
     * ② 只算“恰好等于 min_stock”，库存已跌破阈值（含 0 缺货）反而不算不足。
     */
    @Test
    @DisplayName("低库存设备数按 vm 去重，且口径为 current_stock <= min_stock")
    void lowStockDevicesMustBeDistinctVmAndCoverBelowThreshold()
    {
        String sql = sqlOf("countLowStockDevices").toLowerCase(Locale.ROOT);
        assertTrue(sql.contains("count(distinct vm_id)"),
                "库存不足设备数必须按设备去重（count(distinct vm_id)），不能数库存行\nSQL=" + sql);
        assertTrue(sql.contains("current_stock <= min_stock"),
                "低库存口径必须是 current_stock <= min_stock（含缺货），不能只算恰好等于\nSQL=" + sql);
        assertFalse(sql.contains("current_stock = min_stock"),
                "退回了只统计“恰好等于阈值”的旧口径\nSQL=" + sql);
    }

    /**
     * 同日重复生成必须 upsert（业务规范 §3.5）：唯一键 `uk_type_date (report_type, report_date)`
     * 撞车时覆盖指标，而不是抛 1062 被吞（原 Service 双层 catch 会把失败伪装成成功）。
     */
    @Test
    @DisplayName("report 写入必须是 ON DUPLICATE KEY UPDATE upsert")
    void reportInsertMustBeUpsert()
    {
        String sql = sqlOf("insertOrIgnoreReport").toLowerCase(Locale.ROOT);
        assertTrue(sql.contains("on duplicate key update"),
                "同名同日重跑必须是 upsert，否则唯一键 1062 会让报表无法更新\nSQL=" + sql);
        assertTrue(sql.contains("total_revenue = values(total_revenue)"),
                "upsert 必须覆盖营收指标\nSQL=" + sql);
        assertTrue(sql.contains("status = values(status)"),
                "upsert 必须覆盖生成状态（成功/失败）\nSQL=" + sql);
        assertFalse(sql.contains("insert ignore"),
                "INSERT IGNORE 会静默丢弃新数据，不是覆盖\nSQL=" + sql);
    }
}
