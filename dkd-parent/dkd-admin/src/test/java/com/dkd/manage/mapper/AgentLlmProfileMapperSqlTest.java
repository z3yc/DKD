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
 * 模型档案 / 探测记录 Mapper 的形状回归测试（排期 3-5）。
 *
 * <p>守三条：
 * <ol>
 *   <li><b>没有任何密钥列</b>：表里只有 {@code api_key_env}（变量名），SQL 也不得出现
 *       "secret"/"api_key_value" 之类列名——否则说明有人加了存密钥的列（AGENTS §7.1）；</li>
 *   <li><b>UPDATE 不 set code</b>：编码被 {@code agent_llm_probe.profile_code} 引用，改编码会让
 *       历史探测记录指向不存在的档案；让 SQL 层也改不了；</li>
 *   <li><b>is_default 互斥可落地</b>：{@code clearDefaultFlag} 必须是"清其它默认行"的单条 UPDATE，
 *       且支持 excludeId（编辑时不能把自己清掉）。</li>
 * </ol>
 *
 * <p>纯 XML 文本断言、不连数据库（与项目既有 SQL 形状测试同一套路）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentLlmProfileMapperSqlTest
{
    private static final String PROFILE_RESOURCE = "mapper/manage/AgentLlmProfileMapper.xml";

    private static final String PROBE_RESOURCE = "mapper/manage/AgentLlmProbeMapper.xml";

    private static Document profileDocument;

    private static Document probeDocument;

    @BeforeAll
    static void parseMapperXml() throws Exception
    {
        profileDocument = parse(PROFILE_RESOURCE);
        probeDocument = parse(PROBE_RESOURCE);
    }

    private static Document parse(String resource) throws Exception
    {
        try (InputStream in = AgentLlmProfileMapperSqlTest.class.getClassLoader().getResourceAsStream(resource))
        {
            assertNotNull(in, "类路径上找不到 " + resource);
            DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
            return builder.parse(in);
        }
    }

    private static String rawXml(String resource) throws Exception
    {
        try (InputStream in = AgentLlmProfileMapperSqlTest.class.getClassLoader().getResourceAsStream(resource))
        {
            assertNotNull(in, "类路径上找不到 " + resource);
            // 用 Spring 的 StreamUtils 而不是 InputStream.readAllBytes()：后者是 Java 9+ API
            return org.springframework.util.StreamUtils.copyToString(in, java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    private static String textOf(Document document, String tagName, String id)
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
        throw new AssertionError("找不到 " + tagName + " id=" + id);
    }

    private static String rawBlockOfUpdate() throws Exception
    {
        String raw = rawXml(PROFILE_RESOURCE);
        String head = "<update id=\"updateAgentLlmProfile\"";
        int start = raw.indexOf(head);
        assertTrue(start >= 0, "找不到 updateAgentLlmProfile 节点");
        int end = raw.indexOf("</update>", start);
        return raw.substring(start, end);
    }

    @Test
    @DisplayName("档案列表/详情：过滤 del_flag 且全部 #{} 绑定")
    void profileQueriesAreSoftDeleteAwareAndParameterized()
    {
        String where = textOf(profileDocument, "sql", "agentLlmProfileWhere");
        assertTrue(where.contains("del_flag = '0'"), "档案查询未过滤已删除（AGENTS §7.4）\nSQL=" + where);
        assertTrue(where.contains("code like concat('%', #{code}, '%')"), "code 应模糊匹配且参数绑定\nSQL=" + where);

        String byId = textOf(profileDocument, "select", "selectAgentLlmProfileById");
        assertTrue(byId.contains("del_flag = '0'"), "详情查询未过滤已删除\nSQL=" + byId);

        String byCode = textOf(profileDocument, "select", "selectAgentLlmProfileByCode");
        assertTrue(byCode.contains("code = #{code}"), "按编码查询应参数绑定\nSQL=" + byCode);

        String count = textOf(profileDocument, "select", "countAgentLlmProfileByCode");
        assertTrue(count.contains("del_flag = '0'"), "唯一性统计未过滤已删除\nSQL=" + count);
        assertTrue(count.contains("id != #{excludeId}"), "编辑时唯一性统计要排除自身\nSQL=" + count);
    }

    @Test
    @DisplayName("UPDATE 不 set code（编码被探测记录引用，只能新增时可写）")
    void updateNeverSetsCode() throws Exception
    {
        String updateBlock = rawBlockOfUpdate();
        assertFalse(updateBlock.contains("code = #{"),
                "UPDATE 出现了 code：档案编码不可修改（历史探测记录按 code 关联）\nBLOCK=" + updateBlock);
        assertTrue(updateBlock.contains("api_key_env = #{apiKeyEnv}"), "应更新环境变量名\nBLOCK=" + updateBlock);
        assertTrue(updateBlock.contains("where id = #{id} and del_flag = '0'"), "更新必须带主键与软删条件");
    }

    @Test
    @DisplayName("is_default 互斥：clearDefaultFlag 是单条有条件 UPDATE，且支持排除自身")
    void clearDefaultFlagSupportsExclusion()
    {
        String clear = textOf(profileDocument, "update", "clearDefaultFlag");

        assertTrue(clear.contains("set is_default = 0"), "应把默认标记清 0\nSQL=" + clear);
        assertTrue(clear.contains("where del_flag = '0' and is_default = 1"),
                "只清当前默认行且过滤已删除\nSQL=" + clear);
        assertTrue(clear.contains("id != #{excludeId}"),
                "编辑时必须排除自身，否则会把刚设的默认清掉\nSQL=" + clear);
    }

    @Test
    @DisplayName("两条 Mapper XML 都没有任何密钥列名，也没有 ${} 拼接")
    void noSecretColumnsAndNoInterpolation() throws Exception
    {
        for (String resource : new String[] { PROFILE_RESOURCE, PROBE_RESOURCE })
        {
            String raw = rawXml(resource).toLowerCase();
            assertFalse(raw.contains("${"), resource + " 出现 ${} 拼接（AGENTS §7.2 红线）");
            assertFalse(raw.contains("api_key_value"), resource + " 出现 api_key_value 列（密钥不得入库，AGENTS §7.1）");
            assertFalse(raw.contains("secret_value"), resource + " 出现 secret_value 列（密钥不得入库）");
            assertTrue(raw.contains("api_key_env") || raw.contains("agent_llm_probe") || resource.contains("Probe"),
                    resource + " 应只承载环境变量名/探测结论");
        }
    }

    @Test
    @DisplayName("探测记录：INSERT 全字段、SELECT 倒序；只有 insert/select（只增不改）")
    void probeMapperIsAppendOnlyAndOrdered() throws Exception
    {
        String insert = textOf(probeDocument, "insert", "insertAgentLlmProbe");
        assertTrue(insert.contains("profile_code"), "应记录档案编码\nSQL=" + insert);
        assertTrue(insert.contains("model_served"), "应记录服务端实际模型\nSQL=" + insert);
        assertTrue(insert.contains("error_type"), "应记录失败异常类型\nSQL=" + insert);
        assertFalse(insert.contains("response_body"), "不得落响应正文\nSQL=" + insert);

        String list = textOf(probeDocument, "select", "selectAgentLlmProbeList");
        assertTrue(list.contains("profile_code = #{profileCode}"), "应按档案编码过滤且参数绑定\nSQL=" + list);
        assertTrue(list.contains("order by create_time desc, id desc"), "探测记录应倒序（先看最近一次）\nSQL=" + list);

        for (Method method : AgentLlmProbeMapper.class.getDeclaredMethods())
        {
            String name = method.getName();
            assertTrue(name.startsWith("select") || name.startsWith("insert"),
                    "探测记录 Mapper 出现了不允许的方法 " + name + "（只增不改）");
        }
    }

    @Test
    @DisplayName("档案 Mapper 方法白名单：select/insert/update/clearDefaultFlag（无 delete）")
    void profileMapperExposesNoDelete()
    {
        for (Method method : AgentLlmProfileMapper.class.getDeclaredMethods())
        {
            String name = method.getName();
            boolean allowed = name.startsWith("select") || name.startsWith("insert") || name.startsWith("update")
                    || name.startsWith("count") || "clearDefaultFlag".equals(name);
            assertTrue(allowed, "模型档案 Mapper 出现了不允许的方法 " + name);
        }
    }
}
