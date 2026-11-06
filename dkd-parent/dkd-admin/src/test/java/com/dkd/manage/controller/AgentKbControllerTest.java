package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;

import javax.servlet.http.HttpServletRequest;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;

import com.dkd.common.annotation.Log;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.domain.model.LoginUser;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentKbClient;
import com.dkd.manage.agent.AgentKbUpstreamException;
import com.dkd.manage.domain.dto.AgentKbDocumentDto;
import com.dkd.manage.domain.dto.AgentKbEnabledDto;
import com.dkd.manage.domain.dto.AgentKbSearchDto;

/**
 * 知识库代理 Controller 单测（排期 3-6）。
 *
 * <p>覆盖四类语义：
 * <ol>
 *   <li><b>参数预校验</b>：docCode 正则、query 非空/≤500、topK 1~20、enabled 0/1、分页范围——
 *       一律字段级错误（前端能标红），且**校验失败时不触达 Python**；</li>
 *   <li><b>成功透传</b>：Python 信封 data 原样进 `AjaxResult.data`，requestId 从全链路头透传；</li>
 *   <li><b>失败映射</b>：Python 不可达 → HTTP 503；上游非成功 → HTTP 502，消息不泄露上游 body；</li>
 *   <li><b>权限与审计</b>：五个接口权限点与 @Log 就位（ingest/edit 是写操作）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentKbControllerTest
{
    private final AgentKbClient agentKbClient = mock(AgentKbClient.class);

    private final AgentKbController controller = new AgentKbController();

    @BeforeEach
    void injectClient()
    {
        ReflectionTestUtils.setField(controller, "agentKbClient", agentKbClient);
    }

    @AfterEach
    void clearSecurityContext()
    {
        SecurityContextHolder.clearContext();
    }

    // ------------------------------------------------------------------------------------
    // 参数校验（校验失败不应触达 Python）
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("docCode 非法：字段错误（PUT 路径参数）")
    void rejectsInvalidDocCodeOnEnabled()
    {
        AgentKbEnabledDto dto = new AgentKbEnabledDto();
        dto.setEnabled(Integer.valueOf(1));

        assertThatThrownBy(() -> controller.updateEnabled("Bad/Code", dto, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("文档编码格式不正确");
        verify(agentKbClient, org.mockito.Mockito.never()).setEnabled(anyString(),
                org.mockito.ArgumentMatchers.anyBoolean(), anyString());
    }

    @Test
    @DisplayName("启停值非法：字段错误（只能 0/1）")
    void rejectsInvalidEnabledFlag()
    {
        AgentKbEnabledDto dto = new AgentKbEnabledDto();
        dto.setEnabled(Integer.valueOf(2));

        assertThatThrownBy(() -> controller.updateEnabled("manual_a", dto, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("只能是 0 或 1");
    }

    @Test
    @DisplayName("检索：query 必填/≤500、topK 1~20 校验")
    void rejectsInvalidSearchParams()
    {
        AgentKbSearchDto empty = new AgentKbSearchDto();
        assertThatThrownBy(() -> controller.search(empty, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("检索词必填");

        StringBuilder longQuery = new StringBuilder();
        for (int i = 0; i < 501; i++)
        {
            longQuery.append('a');
        }
        AgentKbSearchDto tooLong = new AgentKbSearchDto();
        tooLong.setQuery(longQuery.toString());
        assertThatThrownBy(() -> controller.search(tooLong, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("检索词最长 500");

        AgentKbSearchDto badTopK = new AgentKbSearchDto();
        badTopK.setQuery("库存");
        badTopK.setTopK(Integer.valueOf(21));
        assertThatThrownBy(() -> controller.search(badTopK, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("返回条数需在 1~20");

        AgentKbSearchDto zeroTopK = new AgentKbSearchDto();
        zeroTopK.setQuery("库存");
        zeroTopK.setTopK(Integer.valueOf(0));
        assertThatThrownBy(() -> controller.search(zeroTopK, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class)
                .hasMessageContaining("返回条数需在 1~20");
        verify(agentKbClient, org.mockito.Mockito.never()).search(anyMap(), anyString());
    }

    @Test
    @DisplayName("入库：docCode/标题/分类/正文校验")
    void rejectsInvalidIngestParams()
    {
        AgentKbDocumentDto badCode = newDocument();
        badCode.setDocCode("AB");
        assertThatThrownBy(() -> controller.ingest(badCode, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("文档编码格式不正确");

        AgentKbDocumentDto emptyTitle = newDocument();
        emptyTitle.setTitle("  ");
        assertThatThrownBy(() -> controller.ingest(emptyTitle, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("标题必填");

        AgentKbDocumentDto badCategory = newDocument();
        badCategory.setCategory("unknown");
        assertThatThrownBy(() -> controller.ingest(badCategory, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("分类只能是");

        AgentKbDocumentDto emptyContent = newDocument();
        emptyContent.setContent("");
        assertThatThrownBy(() -> controller.ingest(emptyContent, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("正文不能为空");

        // 与 Python 同口径：sourceType 白名单、sourceRef ≤500、正文按**字节**≤1MB
        AgentKbDocumentDto badSourceType = newDocument();
        badSourceType.setSourceType("http");
        assertThatThrownBy(() -> controller.ingest(badSourceType, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("来源类型只能是");

        StringBuilder longRef = new StringBuilder();
        for (int i = 0; i < 501; i++)
        {
            longRef.append('r');
        }
        AgentKbDocumentDto badSourceRef = newDocument();
        badSourceRef.setSourceRef(longRef.toString());
        assertThatThrownBy(() -> controller.ingest(badSourceRef, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("来源引用最长 500");

        StringBuilder oversized = new StringBuilder();
        for (int i = 0; i <= 1024 * 1024; i++)
        {
            oversized.append('a');
        }
        AgentKbDocumentDto tooLarge = newDocument();
        tooLarge.setContent(oversized.toString());
        assertThatThrownBy(() -> controller.ingest(tooLarge, new MockHttpServletRequest()))
                .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("正文过大");
        verify(agentKbClient, org.mockito.Mockito.never()).ingestDocument(anyMap(), anyString());
    }

    @Test
    @DisplayName("列表：分页范围校验（pageNum≥1、pageSize 1~200）")
    void rejectsInvalidPaging()
    {
        assertThatThrownBy(() -> controller.documents(null, null, null, Integer.valueOf(0), null,
                new MockHttpServletRequest()))
                        .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("页码最小为 1");

        assertThatThrownBy(() -> controller.documents(null, null, null, null, Integer.valueOf(101),
                new MockHttpServletRequest()))
                        .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("每页条数需在 1~100");

        assertThatThrownBy(() -> controller.documents(null, Integer.valueOf(2), null, null, null,
                new MockHttpServletRequest()))
                        .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("只能是 0 或 1");

        StringBuilder longKeyword = new StringBuilder();
        for (int i = 0; i < 101; i++)
        {
            longKeyword.append('k');
        }
        assertThatThrownBy(() -> controller.documents(null, null, longKeyword.toString(), null, null,
                new MockHttpServletRequest()))
                        .isInstanceOf(AgentFieldErrorException.class).hasMessageContaining("关键词最长 100");
    }

    // ------------------------------------------------------------------------------------
    // 成功透传
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("统计：Python data 原样进 AjaxResult.data，requestId 从请求头透传")
    void statsPassesDataAndRequestId()
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put("documents", Integer.valueOf(1));
        data.put("chunks", Integer.valueOf(3));
        data.put("retrievals", Integer.valueOf(4));
        when(agentKbClient.stats(anyString())).thenReturn(data);
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/manage/agent/kb/stats");
        request.addHeader("X-Request-Id", "rid-from-client");

        AjaxResult result = controller.stats(request);

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat(result.get(AjaxResult.DATA_TAG)).isSameAs(data);
        verify(agentKbClient).stats("rid-from-client");
    }

    @Test
    @DisplayName("入库：operator 强制取当前登录人（忽略请求体伪造），requestId 透传")
    void ingestForcesOperatorFromLogin() 
    {
        loginAs("tester");
        when(agentKbClient.ingestDocument(anyMap(), anyString())).thenReturn(new LinkedHashMap<String, Object>());
        AgentKbDocumentDto dto = newDocument();
        // 不传分类 → 按 Python 默认 manual 处理（不额外造"分类必填"约束）
        dto.setCategory(null);

        controller.ingest(dto, new MockHttpServletRequest());

        @SuppressWarnings("unchecked")
        org.mockito.ArgumentCaptor<Map<String, Object>> payloadCaptor = org.mockito.ArgumentCaptor
                .forClass(Map.class);
        verify(agentKbClient).ingestDocument(payloadCaptor.capture(), anyString());
        assertThat(payloadCaptor.getValue().get("operator")).isEqualTo("tester");
        assertThat(payloadCaptor.getValue().get("docCode")).isEqualTo("manual_a");
        assertThat(payloadCaptor.getValue().get("category")).isEqualTo("manual");
        // 可空字段不能以 null 送给 Python：sourceType 在 Python 是 `str`（默认 text），
        // 显式 null 会被 Pydantic 判 422（表现为"表单没填却报接口错误"）
        assertThat(payloadCaptor.getValue()).doesNotContainKey("sourceType");
        assertThat(payloadCaptor.getValue()).doesNotContainKey("sourceRef");
    }

    @Test
    @DisplayName("检索：默认 topK=5 且 requestId 同时进 body")
    void searchFillsDefaultTopKAndRequestId()
    {
        when(agentKbClient.search(anyMap(), anyString())).thenReturn(new LinkedHashMap<String, Object>());
        AgentKbSearchDto dto = new AgentKbSearchDto();
        dto.setQuery("设备 A1000001 库存");
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Request-Id", "rid-search");

        controller.search(dto, request);

        @SuppressWarnings("unchecked")
        org.mockito.ArgumentCaptor<Map<String, Object>> payloadCaptor = org.mockito.ArgumentCaptor
                .forClass(Map.class);
        verify(agentKbClient).search(payloadCaptor.capture(), eq("rid-search"));
        assertThat(payloadCaptor.getValue().get("topK")).isEqualTo(5);
        assertThat(payloadCaptor.getValue().get("requestId")).isEqualTo("rid-search");
    }

    // ------------------------------------------------------------------------------------
    // 失败映射
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("Python 不可达：HTTP 503 + 明确 msg")
    void mapsTransportFailureTo503()
    {
        ResponseEntity<AjaxResult> response = controller
                .handleUpstream(new AgentKbUpstreamException(null, true, "知识库服务不可用（连接失败或超时），请稍后重试"));

        assertThat(response.getStatusCodeValue()).isEqualTo(503);
        assertThat(response.getBody().get(AjaxResult.CODE_TAG)).isEqualTo(503);
        assertThat((String) response.getBody().get(AjaxResult.MSG_TAG)).contains("不可用");
    }

    @Test
    @DisplayName("上游非成功：HTTP 502 + 明确 msg（不泄露 body）")
    void mapsUpstreamFailureTo502()
    {
        ResponseEntity<AjaxResult> response = controller.handleUpstream(
                new AgentKbUpstreamException(Integer.valueOf(500), false, "知识库接口返回错误（HTTP 500），请稍后重试"));

        assertThat(response.getStatusCodeValue()).isEqualTo(502);
        assertThat(response.getBody().get(AjaxResult.CODE_TAG)).isEqualTo(502);
        assertThat((String) response.getBody().get(AjaxResult.MSG_TAG)).contains("HTTP 500").doesNotContain("SQL");
    }

    @Test
    @DisplayName("字段错误处理器：HTTP 200 + code 400 + data{field:msg}")
    void mapsFieldErrorEnvelope()
    {
        AjaxResult result = controller.handleFieldError(new AgentFieldErrorException("topK", "返回条数需在 1~20"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("topK", "返回条数需在 1~20");
    }

    // ------------------------------------------------------------------------------------
    // 权限 / 审计形状
    // ------------------------------------------------------------------------------------

    @Test
    @DisplayName("路径前缀与五个接口权限点/@Log 符合契约")
    void endpointShapeMatchesContract() throws Exception
    {
        org.springframework.web.bind.annotation.RequestMapping mapping = AgentKbController.class
                .getAnnotation(org.springframework.web.bind.annotation.RequestMapping.class);
        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/kb");

        Map<String, String> permissions = new LinkedHashMap<String, String>();
        permissions.put("documents", "manage:agentKb:list");
        permissions.put("ingest", "manage:agentKb:ingest");
        permissions.put("updateEnabled", "manage:agentKb:edit");
        permissions.put("search", "manage:agentKb:search");
        permissions.put("stats", "manage:agentKb:list");
        for (Map.Entry<String, String> entry : permissions.entrySet())
        {
            String permission = permissionOf(entry.getKey());
            assertThat(permission).as("接口 " + entry.getKey()).isEqualTo(entry.getValue());
        }

        // 写操作必须审计；只读接口不写操作日志
        for (String write : new String[] { "ingest", "updateEnabled", "search" })
        {
            assertThat(logOf(write)).as("写接口 " + write + " 必须进 sys_oper_log").isNotNull();
            assertThat(logOf(write).title()).isEqualTo("智能体知识库");
        }
        assertThat(logOf("documents")).isNull();
        assertThat(logOf("stats")).isNull();
    }

    // ------------------------------------------------------------------------------------
    // 辅助
    // ------------------------------------------------------------------------------------

    private AgentKbDocumentDto newDocument()
    {
        AgentKbDocumentDto dto = new AgentKbDocumentDto();
        dto.setDocCode("manual_a");
        dto.setTitle("补货 SOP");
        dto.setCategory("sop");
        dto.setContent("正文内容");
        return dto;
    }

    private void loginAs(String username)
    {
        // LoginUser.getUsername() 取的是 SysUser.userName；只设 userId 会 NPE
        com.dkd.common.core.domain.entity.SysUser sysUser = new com.dkd.common.core.domain.entity.SysUser();
        sysUser.setUserName(username);
        LoginUser loginUser = new LoginUser();
        loginUser.setUserId(Long.valueOf(7L));
        loginUser.setUser(sysUser);
        loginUser.setPermissions(new LinkedHashSet<String>());
        SecurityContextHolder.getContext()
                .setAuthentication(new UsernamePasswordAuthenticationToken(loginUser, null));
    }

    private String permissionOf(String methodName)
    {
        org.springframework.security.access.prepost.PreAuthorize preAuthorize = findMethod(methodName)
                .getAnnotation(org.springframework.security.access.prepost.PreAuthorize.class);
        assertThat(preAuthorize).as("接口 " + methodName + " 缺少权限注解").isNotNull();
        String value = preAuthorize.value();
        return value.substring(value.indexOf('\'') + 1, value.lastIndexOf('\''));
    }

    private Log logOf(String methodName)
    {
        return findMethod(methodName).getAnnotation(Log.class);
    }

    private java.lang.reflect.Method findMethod(String methodName)
    {
        for (java.lang.reflect.Method method : AgentKbController.class.getDeclaredMethods())
        {
            if (method.getName().equals(methodName))
            {
                return method;
            }
        }
        throw new AssertionError("找不到接口方法 " + methodName);
    }

    /** 说明：HttpServletRequest 是五个接口的公共参数，显式引用避免"未使用 import"告警。 */
    private static final Class<?> REQUEST_TYPE = HttpServletRequest.class;
}
