package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.service.IAgentDashboardService;

/**
 * 驾驶舱 Controller 形状测试（排期 6-1）。
 *
 * <p>断言：路径前缀、权限点、days 透传、字段错误信封（HTTP200+code400+data{field:msg}）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentDashboardControllerTest
{
    private final IAgentDashboardService service = mock(IAgentDashboardService.class);

    private final AgentDashboardController controller = new AgentDashboardController();

    @BeforeEach
    void injectService()
    {
        ReflectionTestUtils.setField(controller, "agentDashboardService", service);
    }

    @Test
    @DisplayName("路径前缀 /manage/agent/dashboard（不经 /agent 网关）")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentDashboardController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/dashboard");
    }

    @Test
    @DisplayName("overview：权限点 manage:agentDashboard:list，只读（无 @Log）")
    void overviewUsesDashboardPermission()
    {
        Method method = findMethod("overview");
        PreAuthorize preAuthorize = method.getAnnotation(PreAuthorize.class);

        assertThat(preAuthorize).isNotNull();
        assertThat(preAuthorize.value()).isEqualTo("@ss.hasPermi('manage:agentDashboard:list')");
        assertThat(method.getAnnotation(com.dkd.common.annotation.Log.class)).isNull();
    }

    @Test
    @DisplayName("overview：days 透传 Service，结果进 data")
    void overviewPassesDays()
    {
        Map<String, Object> overview = new LinkedHashMap<String, Object>();
        overview.put("days", Integer.valueOf(7));
        when(service.selectOverview(Integer.valueOf(7))).thenReturn(overview);

        AjaxResult result = controller.overview(Integer.valueOf(7));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat(result.get(AjaxResult.DATA_TAG)).isSameAs(overview);
        verify(service).selectOverview(Integer.valueOf(7));
    }

    @Test
    @DisplayName("字段错误处理器：HTTP200 + code400 + data{field:msg}")
    void fieldErrorHandlerMapsEnvelope()
    {
        AjaxResult result = controller.handleFieldError(new AgentFieldErrorException("days", "统计窗口需在 1~30 天之间"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("days", "统计窗口需在 1~30 天之间");
    }

    private Method findMethod(String name)
    {
        for (Method method : AgentDashboardController.class.getDeclaredMethods())
        {
            if (method.getName().equals(name))
            {
                return method;
            }
        }
        throw new AssertionError("找不到接口方法 " + name);
    }
}
