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
 * 执行日志 Mapper 的**形状回归测试**（Batch A · 排期 2-4）。
 *
 * <p>为什么要有这组断言：它守的是三条"以后一改就会悄悄退化"的约定——
 * <ol>
 *   <li><b>时间窗必须是无条件的范围比较</b>：写成 {@code <if test="window != null">} 会留下
 *       一个绕过 Service 护栏的口子（直接调 Mapper 就变成无界查询）；用
 *       {@code date_format()} 包裹索引列则会让 {@code idx_agent_log_create_time} 失效
 *       ——仓库里 {@code OrderMapper.xml:44} 就是被点名的反例（AGENTS §10）；</li>
 *   <li><b>SQL 全部 {@code #{}} 参数绑定</b>：runId / requestId / keyword 都来自前端，
 *       任何 {@code ${}} 拼接都是注入面（AGENTS §7.2）；</li>
 *   <li><b>Mapper 只有查询方法</b>：日志是只增不改的排障证据，后台不能有写入口。</li>
 * </ol>
 *
 * <p>纯 XML 文本断言、不连数据库——与本项目既有的 {@code ReportMapperSqlTest} /
 * {@code AgentDecisionLogMapperSqlTest} 同一套路（Java 侧门禁是纯单测）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentLogMapperSqlTest
{
    private static final String RESOURCE_PATH = "mapper/manage/AgentLogMapper.xml";

    private static Document document;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        try (InputStream in = AgentLogMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
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

    /** 原始 XML 文本（用于断言 &lt;include&gt;/&lt;if&gt; 这类 DOM 文本拿不到的结构）。 */
    private static String rawXml() throws Exception
    {
        try (InputStream in = AgentLogMapperSqlTest.class.getClassLoader().getResourceAsStream(RESOURCE_PATH))
        {
            assertNotNull(in, "类路径上找不到 " + RESOURCE_PATH);
            // 用 Spring 的 StreamUtils 而不是 InputStream.readAllBytes()：后者是 Java 9+ API，
            // 本项目按 Java 8 语法约束（AGENTS §2.1）。
            return org.springframework.util.StreamUtils.copyToString(in, java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    /** 取某个元素（含属性与子节点）的原始片段。 */
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
    @DisplayName("时间窗是无条件的范围比较，且不得用 date_format() 包裹索引列")
    void timeWindowMustBeUnconditionalRangeComparison() throws Exception
    {
        String where = textOf("sql", "agentLogWhere");

        assertFalse(where.toLowerCase().contains("date_format("),
                "WHERE 里出现了 date_format()：函数包裹索引列会让 idx_agent_log_create_time 失效\nSQL=" + where);
        // DOM 的 getTextContent() 已解码 XML 实体，所以断言 ``>=`` / ``<=`` 而不是源码里的 &gt;= / &lt;=
        assertTrue(where.contains("create_time >= #{window.beginTime}"),
                "缺少开始时间的范围条件\nSQL=" + where);
        assertTrue(where.contains("create_time <= #{window.endTime}"),
                "上界应为闭区间（Java 侧已补齐到请求精度末尾）\nSQL=" + where);

        // 关键：不能有 <if test="window ..."> 这类开关——漏传窗口必须是 fail-closed（查不到），
        // 而不是退化成无界查询把主库拖慢
        String whereBlock = rawBlock("sql", "agentLogWhere");
        assertFalse(whereBlock.contains("test=\"window"),
                "时间窗被 <if> 包住了：绕过 Service 直接调 Mapper 就会变成无界查询\nBLOCK=" + whereBlock);
    }

    @Test
    @DisplayName("过滤条件全部 #{} 绑定，不得出现 ${}（注入红线）")
    void filtersUseBoundParametersOnly() throws Exception
    {
        assertFalse(rawXml().contains("${"), "XML 里出现了 ${} 拼接（AGENTS §7.2 红线）\n");

        String where = textOf("sql", "agentLogWhere");
        assertTrue(where.contains("level = #{query.level}"), "level 应精确匹配且参数绑定\nSQL=" + where);
        assertTrue(where.contains("status = #{query.status}"), "status 应精确匹配且参数绑定\nSQL=" + where);
        assertTrue(where.contains("run_id = #{query.runId}"), "runId 应精确匹配且参数绑定\nSQL=" + where);
        assertTrue(where.contains("request_id = #{query.requestId}"), "requestId 应精确匹配且参数绑定\nSQL=" + where);
    }

    @Test
    @DisplayName("关键词只匹配 message / error_msg（不混进关联ID列，保持语义可预期）")
    void keywordMatchesMessageAndErrorOnly() throws Exception
    {
        String where = textOf("sql", "agentLogWhere");

        assertTrue(where.contains("message like concat('%', #{query.keyword}, '%')"),
                "关键词应匹配 message\nSQL=" + where);
        assertTrue(where.contains("error_msg like concat('%', #{query.keyword}, '%')"),
                "关键词应匹配 error_msg\nSQL=" + where);
        assertFalse(where.contains("run_id like"), "关键词不应混进 run_id（已有精确过滤）\nSQL=" + where);
        assertFalse(where.contains("request_id like"), "关键词不应混进 request_id（已有精确过滤）\nSQL=" + where);
    }

    @Test
    @DisplayName("列表按 create_time 倒序，且带 id 兜底次序")
    void listIsOrderedByTimeDesc() throws Exception
    {
        String list = textOf("select", "selectAgentLogList");

        assertTrue(list.contains("order by create_time desc"),
                "日志列表必须按时间倒序（排障先看最近发生的）\nSQL=" + list);
        assertTrue(list.contains("id desc"), "同一毫秒内需要 id 兜底次序（create_time 是 DATETIME(3)）\nSQL=" + list);
    }

    @Test
    @DisplayName("Mapper 接口只暴露查询方法（日志是只增不改的排障证据）")
    void mapperExposesQueriesOnly()
    {
        for (Method method : AgentLogMapper.class.getDeclaredMethods())
        {
            assertTrue(method.getName().startsWith("select"),
                    "Mapper 出现了非查询方法 " + method.getName() + " —— 能改的日志就不是证据（AGENTS §6.3）");
        }
    }
}
