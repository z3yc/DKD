package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.Map;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.annotation.Log;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.domain.entity.SysUser;
import com.dkd.common.core.domain.model.LoginUser;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentEvalReport;
import com.dkd.manage.domain.dto.AgentEvalReportDto;
import com.dkd.manage.service.IAgentEvalReportService;

/**
 * 评测报告 Controller 形状测试（排期 5-1）。
 *
 * <p>断言路径/权限/@Log/响应形状：
 * <ul>
 *   <li>{@code POST /report} 是写操作（@Log + manage:agentConfig:edit）；</li>
 *   <li>{@code GET /reports}、{@code GET /gate} 只读，复用配置台权限点；</li>
 *   <li>字段错误走 HTTP200+code400+data{field:msg}（与配置台一致）。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentEvalControllerTest
{
    private final IAgentEvalReportService service = mock(IAgentEvalReportService.class);

    private final AgentEvalController controller = new AgentEvalController();

    @BeforeEach
    void injectService()
    {
        ReflectionTestUtils.setField(controller, "agentEvalReportService", service);
    }

    @AfterEach
    void clearSecurityContext()
    {
        SecurityContextHolder.clearContext();
    }

    @Test
    @DisplayName("路径前缀 /manage/agent/eval（不经 /agent 网关）")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentEvalController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/eval");
    }

    @Test
    @DisplayName("权限点：上报=edit，查询= list；上报挂 @Log，查询不挂")
    void endpointPermissionsAndAudit()
    {
        Method report = findMethod("report");
        assertThat(report.getAnnotation(PreAuthorize.class).value())
                .isEqualTo("@ss.hasPermi('manage:agentConfig:edit')");
        assertThat(report.getAnnotation(Log.class)).as("上报是写操作，必须审计").isNotNull();
        assertThat(report.getAnnotation(Log.class).title()).isEqualTo("智能体评测");

        for (String read : new String[] { "reports", "gate" })
        {
            Method method = findMethod(read);
            assertThat(method.getAnnotation(PreAuthorize.class).value())
                    .isEqualTo("@ss.hasPermi('manage:agentConfig:list')");
            assertThat(method.getAnnotation(Log.class)).as("只读接口不写操作日志").isNull();
        }
    }

    @Test
    @DisplayName("上报：runBy 缺省时由 Controller 传当前登录人，结果进 data")
    void reportPassesOperatorAndReturnsData()
    {
        loginAs("ci-runner");
        AgentEvalReport saved = new AgentEvalReport();
        saved.setId(Long.valueOf(1L));
        when(service.insertReport(any(AgentEvalReportDto.class), anyString())).thenReturn(saved);

        AjaxResult result = controller.report(new AgentEvalReportDto());

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat(result.get(AjaxResult.DATA_TAG)).isSameAs(saved);
        verify(service).insertReport(any(AgentEvalReportDto.class), eq("ci-runner"));
    }

    @Test
    @DisplayName("报告列表：data 为数组")
    void reportsPassesList()
    {
        java.util.List<AgentEvalReport> rows = new ArrayList<AgentEvalReport>();
        rows.add(new AgentEvalReport());
        when(service.selectLatestByCode("my_assistant", Integer.valueOf(5))).thenReturn(rows);

        AjaxResult result = controller.reports("my_assistant", Integer.valueOf(5));

        assertThat(result.get(AjaxResult.DATA_TAG)).isSameAs(rows);
        verify(service).selectLatestByCode("my_assistant", Integer.valueOf(5));
    }

    @Test
    @DisplayName("gate：data 含 enabled/passed/latest/reason")
    void gatePassesMap()
    {
        Map<String, Object> gate = new java.util.LinkedHashMap<String, Object>();
        gate.put("enabled", Boolean.TRUE);
        gate.put("passed", Boolean.FALSE);
        gate.put("latest", null);
        gate.put("reason", "没有评测报告");
        when(service.selectGate("my_assistant", Long.valueOf(3L))).thenReturn(gate);

        AjaxResult result = controller.gate("my_assistant", Long.valueOf(3L));

        assertThat(result.get(AjaxResult.DATA_TAG)).isSameAs(gate);
    }

    @Test
    @DisplayName("字段错误处理器：HTTP 200 + code 400 + data{field:msg}")
    void fieldErrorHandlerMapsEnvelope()
    {
        AjaxResult result = controller.handleFieldError(
                new AgentFieldErrorException("accuracy", "status=scored 时准确率必填"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("accuracy", "status=scored 时准确率必填");
    }

    private void loginAs(String username)
    {
        SysUser sysUser = new SysUser();
        sysUser.setUserName(username);
        LoginUser loginUser = new LoginUser();
        loginUser.setUserId(Long.valueOf(7L));
        loginUser.setUser(sysUser);
        loginUser.setPermissions(new LinkedHashSet<String>());
        SecurityContextHolder.getContext()
                .setAuthentication(new UsernamePasswordAuthenticationToken(loginUser, null));
    }

    private Method findMethod(String name)
    {
        for (Method method : AgentEvalController.class.getDeclaredMethods())
        {
            if (method.getName().equals(name))
            {
                return method;
            }
        }
        throw new AssertionError("找不到接口方法 " + name);
    }
}
