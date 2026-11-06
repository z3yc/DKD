package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.annotation.Log;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentMcpUpstreamException;
import com.dkd.manage.domain.vo.AgentMcpServerVo;
import com.dkd.manage.service.IAgentMcpServerService;

/**
 * MCP server Controller 形状测试（排期 3-7）。
 *
 * <p>断言四件事：
 * <ol>
 *   <li>路径前缀 {@code /manage/agent/mcp}，八个接口的权限点与 @Log 就位；</li>
 *   <li>probe/sync 使用独立权限点 {@code manage:agentMcp:probe}（会真实出网访问外部 server）；</li>
 *   <li>字段错误走 HTTP200+code400+data{field:msg}；上游失败走 HTTP 503/502 且不透传上游 body；</li>
 *   <li>requestId 从全链路头透传到 Service（进而透传给 Python）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentMcpServerControllerTest
{
    private final IAgentMcpServerService service = mock(IAgentMcpServerService.class);

    private final AgentMcpServerController controller = new AgentMcpServerController();

    @BeforeEach
    void injectService()
    {
        ReflectionTestUtils.setField(controller, "agentMcpServerService", service);
    }

    @Test
    @DisplayName("路径前缀 /manage/agent/mcp（不经 /agent 网关）")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentMcpServerController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/mcp");
    }

    @Test
    @DisplayName("八个接口权限点就位（probe/sync 单独授权）")
    void endpointPermissionsMatchContract()
    {
        Map<String, String> expected = new LinkedHashMap<String, String>();
        expected.put("list", "manage:agentMcp:list");
        expected.put("getInfo", "manage:agentMcp:query");
        expected.put("add", "manage:agentMcp:add");
        expected.put("edit", "manage:agentMcp:edit");
        expected.put("updateEnabled", "manage:agentMcp:edit");
        expected.put("probe", "manage:agentMcp:probe");
        expected.put("sync", "manage:agentMcp:probe");
        expected.put("tools", "manage:agentMcp:list");

        for (Map.Entry<String, String> entry : expected.entrySet())
        {
            PreAuthorize preAuthorize = findMethod(entry.getKey())
                    .getAnnotation(PreAuthorize.class);
            assertThat(preAuthorize).as("接口 " + entry.getKey() + " 缺少权限注解").isNotNull();
            assertThat(preAuthorize.value()).as("接口 " + entry.getKey())
                    .isEqualTo("@ss.hasPermi('" + entry.getValue() + "')");
        }
    }

    @Test
    @DisplayName("写/探测接口挂 @Log 审计；只读接口不写操作日志")
    void writeEndpointsAreAudited()
    {
        for (String write : new String[] { "add", "edit", "updateEnabled", "probe", "sync" })
        {
            Log log = findMethod(write).getAnnotation(Log.class);
            assertThat(log).as("写接口 " + write + " 必须进 sys_oper_log").isNotNull();
            assertThat(log.title()).isEqualTo("MCP server");
        }
        assertThat(findMethod("list").getAnnotation(Log.class)).isNull();
        assertThat(findMethod("getInfo").getAnnotation(Log.class)).isNull();
        assertThat(findMethod("tools").getAnnotation(Log.class)).isNull();
    }

    @Test
    @DisplayName("列表成功：Service 结果进 AjaxResult.data")
    void listPassesData()
    {
        java.util.List<AgentMcpServerVo> rows = new ArrayList<AgentMcpServerVo>();
        AgentMcpServerVo vo = new AgentMcpServerVo();
        vo.setCode("demo");
        rows.add(vo);
        when(service.selectServerList()).thenReturn(rows);

        AjaxResult result = controller.list();

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat(result.get(AjaxResult.DATA_TAG)).isSameAs(rows);
    }

    @Test
    @DisplayName("探测：requestId 从请求头透传到 Service")
    void probeForwardsRequestId()
    {
        when(service.probe(eq(Long.valueOf(7L)), anyString())).thenReturn(new LinkedHashMap<String, Object>());
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/manage/agent/mcp/7/probe");
        request.addHeader("X-Request-Id", "rid-mcp-probe");

        controller.probe(Long.valueOf(7L), request);

        verify(service).probe(eq(Long.valueOf(7L)), eq("rid-mcp-probe"));
    }

    @Test
    @DisplayName("字段错误处理器：HTTP 200 + code 400 + data{field:msg}")
    void fieldErrorHandlerMapsEnvelope()
    {
        AjaxResult result = controller.handleFieldError(new AgentFieldErrorException("transport", "仅支持 http"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("transport", "仅支持 http");
    }

    @Test
    @DisplayName("Python 不可达：HTTP 503 + 明确 msg")
    void mapsTransportFailureTo503()
    {
        ResponseEntity<AjaxResult> response = controller
                .handleUpstream(new AgentMcpUpstreamException(null, true, "MCP 服务不可用（连接失败或超时），请稍后重试"));

        assertThat(response.getStatusCodeValue()).isEqualTo(503);
        assertThat(response.getBody().get(AjaxResult.CODE_TAG)).isEqualTo(503);
        assertThat((String) response.getBody().get(AjaxResult.MSG_TAG)).contains("不可用");
    }

    @Test
    @DisplayName("上游非成功：HTTP 502，提示不含上游 body")
    void mapsUpstreamFailureTo502()
    {
        ResponseEntity<AjaxResult> response = controller.handleUpstream(
                new AgentMcpUpstreamException(Integer.valueOf(500), false, "MCP 接口返回错误（HTTP 500），请稍后重试"));

        assertThat(response.getStatusCodeValue()).isEqualTo(502);
        assertThat(response.getBody().get(AjaxResult.CODE_TAG)).isEqualTo(502);
        assertThat((String) response.getBody().get(AjaxResult.MSG_TAG)).contains("HTTP 500").doesNotContain("SQL");
    }

    @Test
    @DisplayName("探活/同步权限独立：edit 权限不等于 probe 权限（说明性断言）")
    void probePermissionDiffersFromEdit()
    {
        String probePermission = findMethod("probe").getAnnotation(PreAuthorize.class).value();
        String editPermission = findMethod("edit").getAnnotation(PreAuthorize.class).value();

        assertThat(probePermission).isNotEqualTo(editPermission);
        assertThat(probePermission).contains("manage:agentMcp:probe");
    }

    private Method findMethod(String name)
    {
        for (Method method : AgentMcpServerController.class.getDeclaredMethods())
        {
            if (method.getName().equals(name))
            {
                return method;
            }
        }
        throw new AssertionError("找不到接口方法 " + name);
    }
}
