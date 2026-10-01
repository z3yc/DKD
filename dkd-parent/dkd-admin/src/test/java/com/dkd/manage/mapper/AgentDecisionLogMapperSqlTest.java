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
 * 决策审计 Mapper 的**形状回归测试**（排期任务 3-7）。
 *
 * <p>为什么要有这组断言：本文件要守的不是“SQL 能跑”，而是三条一改就会**悄悄退化**的约定——
 *
 * <ol>
 *   <li><b>时间过滤不得用 {@code date_format()} 包裹索引列</b>（AGENTS §10、§8 CR 清单）。
 *       本仓库既有的 {@code OrderMapper.xml:44} / {@code InventoryLogMapper.xml:46} 就是这么写的，
 *       属于已被点名的反例。实测（2026-10-01，FORCE INDEX 隔离变量）：
 *       {@code scene = ? AND 时间范围} 下，范围比较能拿到 {@code key_len=7, rows=1}，
 *       而 {@code date_format(...)} 只能拿到 {@code key_len=1, rows=55}——
 *       即只有前者能把 create_time 真正推进索引。审计表随每次对话增长，退化代价只会越来越大。</li>
 *   <li><b>查询必须过滤 {@code del_flag}</b>（AGENTS §7.4：查询默认过滤已删除）。</li>
 *   <li><b>列表不得带两个 JSON 上下文列</b>：这张表按对话增长，列表塞大 JSON 会让分页越来越慢；
 *       而“列表与详情查的列不一样”这种差异，只靠人看 SQL 很难在以后的改动中守住。</li>
 * </ol>
 *
 * <p>纯 XML 文本断言、不连数据库 —— 与本项目既有的 {@code ReportMapperSqlTest} 同一套路
 * （Java 侧门禁是纯单测，不依赖 MySQL）。
 */
class AgentDecisionLogMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentDecisionLogMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentDecisionLogMapperSqlTest.class.getClassLoader()
                .getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
            document = builder.parse(in);
        }
    }

    /** 取某个 &lt;sql&gt; / &lt;select&gt; 节点的文本（去空白，便于断言子串）。 */
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

    @Test
    @DisplayName("时间过滤必须是范围比较，且不得用 date_format() 包裹索引列")
    void timeFilterMustUseRangeComparisonWithoutFunctionOnColumn()
    {
        String where = textOf("sql", "agentDecisionLogWhere");

        assertFalse(where.toLowerCase().contains("date_format("),
                "WHERE 里出现了 date_format()：函数包裹索引列会让 (scene, create_time) 索引只能用到第一列\nSQL=" + where);
        // 注意：DOM 的 getTextContent() 已经**解码**了 XML 实体，所以这里要断言 ``>=`` / ``<``
        // 而不是源码里写的 ``&gt;=`` / ``&lt;``（第一版就踩了这个，断言直接红）。
        assertTrue(where.contains("create_time >= #{params.beginTime}"),
                "缺少开始时间的范围条件\nSQL=" + where);
        assertTrue(where.contains("create_time < DATE_ADD(#{params.endTime}, INTERVAL 1 DAY)"),
                "上界应为对**参数**做 DATE_ADD 的半开区间（对索引列做函数会让索引失效）\nSQL=" + where);
    }

    @Test
    @DisplayName("查询默认过滤 del_flag（AGENTS §7.4）")
    void queriesMustFilterDelFlag()
    {
        String where = textOf("sql", "agentDecisionLogWhere");
        assertTrue(where.contains("del_flag = '0'"), "查询未过滤已删除数据\nSQL=" + where);

        String detail = textOf("select", "selectAgentDecisionLogById");
        assertTrue(detail.contains("del_flag = '0'"), "详情查询未过滤已删除数据\nSQL=" + detail);
    }

    @Test
    @DisplayName("列表不得返回两个 JSON 上下文列；详情必须返回")
    void listExcludesJsonColumnsAndDetailIncludesThem()
    {
        String listVo = textOf("sql", "selectAgentDecisionLogListVo");
        assertFalse(listVo.contains("input_context"),
                "列表不该带 input_context（该表随对话增长，列表塞大 JSON 会让分页越来越慢）\nSQL=" + listVo);
        assertFalse(listVo.contains("llm_output"),
                "列表不该带 llm_output\nSQL=" + listVo);

        String detailVo = textOf("sql", "selectAgentDecisionLogDetailVo");
        assertTrue(detailVo.contains("input_context"), "详情应包含 input_context（审计回放需要）\nSQL=" + detailVo);
        assertTrue(detailVo.contains("llm_output"), "详情应包含 llm_output\nSQL=" + detailVo);
    }

    @Test
    @DisplayName("Mapper 接口只暴露查询方法（留痕是审计证据，不得在后台提供写入口）")
    void mapperExposesQueriesOnly()
    {
        for (Method method : AgentDecisionLogMapper.class.getDeclaredMethods())
        {
            String name = method.getName();
            assertTrue(name.startsWith("select"),
                    "Mapper 出现了非查询方法 " + name + " —— 能改的留痕就不是留痕（AGENTS §6.3）");
        }
    }
}
