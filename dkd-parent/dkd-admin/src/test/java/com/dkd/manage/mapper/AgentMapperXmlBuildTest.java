package com.dkd.manage.mapper;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.io.InputStream;

import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.session.Configuration;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.dkd.manage.domain.AgentLog;
import com.dkd.manage.domain.AgentMessage;

/**
 * 新增 Mapper XML 的**可加载性**测试（Batch A · 排期 2-4 / 2-5）。
 *
 * <p>为什么要有这一层：本项目的 Java 门禁是**纯单测**（不启 Spring 容器、不连 MySQL），
 * 而 MyBatis 的 Mapper XML 只在应用启动时才被解析。也就是说：XML 写错标签、
 * resultMap 引用了不存在的别名、语句 id 重复……这些错误在单测阶段**全都看不见**，
 * 要等有人把服务拉起来才炸。这里用 MyBatis 自己的 {@link XMLMapperBuilder} 做一次
 * 离线解析（DTD 由 MyBatis 内置实体解析器处理，不依赖网络），把这类错误前移到门禁。
 *
 * <p>注意边界：OGNL 动态 SQL（{@code <if test="query.level != null">}）是在语句执行时
 * 才编译的，解析阶段只校验结构、别名与语句注册；参数绑定是否正确仍靠 Service 单测 +
 * SQL 形状测试（{@code AgentLogMapperSqlTest} / {@code AgentConversationMapperSqlTest}）覆盖。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentMapperXmlBuildTest
{
    @Test
    @DisplayName("新增两个 Mapper XML 能被 MyBatis 解析，且语句/别名全部注册成功")
    void newMapperXmlCanBeParsedByMyBatis() throws Exception
    {
        Configuration configuration = new Configuration();
        // 应用里由 typeAliasesPackage: com.dkd.**.domain 自动注册；测试里手动注册等价的别名
        configuration.getTypeAliasRegistry().registerAlias("AgentLog", AgentLog.class);
        configuration.getTypeAliasRegistry().registerAlias("AgentMessage", AgentMessage.class);

        parse(configuration, "mapper/manage/AgentLogMapper.xml");
        parse(configuration, "mapper/manage/AgentConversationMapper.xml");

        assertThat(configuration.hasStatement("com.dkd.manage.mapper.AgentLogMapper.selectAgentLogList")).isTrue();
        assertThat(configuration
                .hasStatement("com.dkd.manage.mapper.AgentConversationMapper.selectAgentConversationList")).isTrue();
        assertThat(configuration
                .hasStatement("com.dkd.manage.mapper.AgentConversationMapper.selectAgentConversationByConversationId"))
                        .isTrue();
        assertThat(configuration
                .hasStatement("com.dkd.manage.mapper.AgentConversationMapper.selectAgentMessageAggregates")).isTrue();
        assertThat(configuration
                .hasStatement("com.dkd.manage.mapper.AgentConversationMapper.selectAgentMessagesByConversationId"))
                        .isTrue();
    }

    /**
     * 解析一个 Mapper XML；任何语法/别名/重复 id 问题都会在这里抛出
     * （而不是等到应用启动）。
     *
     * @param configuration MyBatis 配置（共享，便于后续断言语句是否注册）
     * @param resource      类路径资源
     */
    private static void parse(Configuration configuration, String resource) throws IOException
    {
        try (InputStream in = AgentMapperXmlBuildTest.class.getClassLoader().getResourceAsStream(resource))
        {
            assertThat(in).as("类路径上找不到 " + resource).isNotNull();
            XMLMapperBuilder builder = new XMLMapperBuilder(in, configuration, resource,
                    configuration.getSqlFragments());
            builder.parse();
        }
    }
}
