package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Method;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.annotation.Log;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.dto.AgentToolEditDto;

/**
 * 工具中心 Controller 的路径/权限/错误响应形状测试（排期 3-4）。
 *
 * <p>为什么这层要测：字段错误经异常处理器才变成"前端能标红的 data{field:msg}"，
 * 若哪天把 handler 删了/改了 HTTP 码，Service 的校验再正确页面也只会弹一句笼统提示。
 * 用反射与直接调用处理器方法断言，不启动 Spring 容器（AGENTS §8）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentToolControllerTest
{
    @Test
    @DisplayName("路径前缀 /manage/agent/tool（不经 /agent 网关）")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentToolController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/tool");
    }

    @Test
    @DisplayName("列表：GET /list + manage:agentTool:list")
    void listEndpointUsesListPermission() throws Exception
    {
        Method list = AgentToolController.class.getDeclaredMethod("list", com.dkd.manage.domain.AgentTool.class);

        assertThat(list.getAnnotation(GetMapping.class).value()).containsExactly("/list");
        PreAuthorize preAuthorize = list.getAnnotation(PreAuthorize.class);
        assertThat(preAuthorize).isNotNull();
        assertThat(preAuthorize.value()).isEqualTo("@ss.hasPermi('manage:agentTool:list')");
        // 只读接口不应写操作日志
        assertThat(list.getAnnotation(Log.class)).isNull();
    }

    @Test
    @DisplayName("编辑：PUT /{id} + manage:agentTool:edit + @Log 审计")
    void editEndpointUsesEditPermissionAndAuditLog() throws Exception
    {
        Method edit = AgentToolController.class.getDeclaredMethod("edit", Long.class, AgentToolEditDto.class);

        assertThat(edit.getAnnotation(PutMapping.class).value()).containsExactly("/{id}");
        PreAuthorize preAuthorize = edit.getAnnotation(PreAuthorize.class);
        assertThat(preAuthorize).isNotNull();
        assertThat(preAuthorize.value()).isEqualTo("@ss.hasPermi('manage:agentTool:edit')");
        Log log = edit.getAnnotation(Log.class);
        assertThat(log).as("写操作必须进 sys_oper_log").isNotNull();
        assertThat(log.title()).isEqualTo("智能体工具");
    }

    @Test
    @DisplayName("字段错误处理器：HTTP 200 + code 400 + data{field:msg}")
    void fieldErrorHandlerMapsToFieldEnvelope()
    {
        AgentToolController controller = new AgentToolController();

        AjaxResult result = controller.handleFieldError(new AgentFieldErrorException("timeoutMs", "超时需在 100~30000"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        assertThat(result.get(AjaxResult.MSG_TAG)).isEqualTo("超时需在 100~30000");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("timeoutMs", "超时需在 100~30000");
    }
}
