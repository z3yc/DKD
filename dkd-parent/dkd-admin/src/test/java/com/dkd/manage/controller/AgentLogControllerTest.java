package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Method;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;

/**
 * 执行日志 Controller 的**路径/权限形状**测试（Batch A · 排期 2-4）。
 *
 * <p>为什么这层也要测：日志页的权限点是"只读"纪律的最后一环。如果哪天有人给这个
 * 只读控制器加了写接口或换了权限点，页面不会报错，但审计证据的可信度就没了。
 * 这里用反射断言，不启动 Spring 容器（AGENTS §8：Java 侧门禁不依赖环境）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentLogControllerTest
{
    @Test
    @DisplayName("路径前缀 /manage/agent/log（不经 /agent 网关）")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentLogController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/log");
    }

    @Test
    @DisplayName("列表接口：GET /list + 权限点 manage:agentLog:list")
    void listEndpointUsesReadOnlyPermission() throws Exception
    {
        Method list = AgentLogController.class.getDeclaredMethod("list", com.dkd.manage.domain.AgentLog.class);

        GetMapping getMapping = list.getAnnotation(GetMapping.class);
        assertThat(getMapping).isNotNull();
        assertThat(getMapping.value()).containsExactly("/list");

        PreAuthorize preAuthorize = list.getAnnotation(PreAuthorize.class);
        assertThat(preAuthorize).isNotNull();
        assertThat(preAuthorize.value()).isEqualTo("@ss.hasPermi('manage:agentLog:list')");
    }

    @Test
    @DisplayName("控制器只有这一个只读接口（没有写/删入口）")
    void controllerExposesSingleReadEndpoint()
    {
        int endpoints = 0;
        for (Method method : AgentLogController.class.getDeclaredMethods())
        {
            if (method.getAnnotation(GetMapping.class) != null)
            {
                endpoints++;
            }
            // 写接口一律不应出现：日志是只增不改的排障证据
            assertThat(method.getAnnotation(org.springframework.web.bind.annotation.PostMapping.class)).isNull();
            assertThat(method.getAnnotation(org.springframework.web.bind.annotation.PutMapping.class)).isNull();
            assertThat(method.getAnnotation(org.springframework.web.bind.annotation.DeleteMapping.class)).isNull();
        }
        assertThat(endpoints).isEqualTo(1);
    }
}
