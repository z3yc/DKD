package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;
import java.util.ArrayList;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.domain.vo.AgentConversationDetailVo;
import com.dkd.manage.domain.vo.AgentConversationVo;
import com.dkd.manage.service.IAgentConversationService;

/**
 * 会话 Controller 的路径/权限/**不存在时的错误语义**测试（Batch A · 排期 2-5）。
 *
 * <p>为什么"不存在"要单独测：详情接口对不存在的会话返回**空对象**时，页面会显示一个
 * "空会话"而不是错误——用户以为会话被清空了，实际是 ID 写错/已清理。
 * 这里断言 Service 返回"头为空"时 Controller 必须转成 {@code AjaxResult.error}。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentConversationControllerTest
{
    private final IAgentConversationService service = org.mockito.Mockito.mock(IAgentConversationService.class);

    private AgentConversationController controller()
    {
        AgentConversationController controller = new AgentConversationController();
        ReflectionTestUtils.setField(controller, "agentConversationService", service);
        return controller;
    }

    @Test
    @DisplayName("路径前缀 /manage/agent/conversation")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentConversationController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/conversation");
    }

    @Test
    @DisplayName("列表/详情权限点：manage:agentConversation:list / :query")
    void endpointPermissionsMatchContract() throws Exception
    {
        Method list = AgentConversationController.class.getDeclaredMethod("list",
                com.dkd.manage.domain.AgentConversation.class);
        assertThat(list.getAnnotation(GetMapping.class).value()).containsExactly("/list");
        PreAuthorize listPerm = list.getAnnotation(PreAuthorize.class);
        assertThat(listPerm).isNotNull();
        assertThat(listPerm.value()).isEqualTo("@ss.hasPermi('manage:agentConversation:list')");

        Method getInfo = AgentConversationController.class.getDeclaredMethod("getInfo", String.class);
        assertThat(getInfo.getAnnotation(GetMapping.class).value()).containsExactly("/{conversationId}");
        PreAuthorize queryPerm = getInfo.getAnnotation(PreAuthorize.class);
        assertThat(queryPerm).isNotNull();
        assertThat(queryPerm.value()).isEqualTo("@ss.hasPermi('manage:agentConversation:query')");
    }

    @Test
    @DisplayName("详情不存在：返回业务错误而不是空对象（空对象会被误读成“会话被清空”）")
    void detailNotFoundReturnsBusinessError()
    {
        AgentConversationDetailVo detail = new AgentConversationDetailVo();
        detail.setMessages(new ArrayList<com.dkd.manage.domain.vo.AgentMessageVo>());
        when(service.selectConversationDetail(anyString())).thenReturn(detail);

        AjaxResult result = controller().getInfo("conv-missing");

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(500);
        assertThat((String) result.get(AjaxResult.MSG_TAG)).contains("会话不存在").contains("conv-missing");
    }

    @Test
    @DisplayName("详情：conversationId 空/超长（>64）直接拒绝，不拿它去查库")
    void invalidConversationIdRejectedWithoutQuery()
    {
        StringBuilder tooLong = new StringBuilder();
        for (int i = 0; i < 65; i++)
        {
            tooLong.append('a');
        }

        AjaxResult empty = controller().getInfo("   ");
        AjaxResult overlong = controller().getInfo(tooLong.toString());

        assertThat(empty.get(AjaxResult.CODE_TAG)).isEqualTo(500);
        assertThat((String) empty.get(AjaxResult.MSG_TAG)).contains("conversationId 不合法");
        assertThat(overlong.get(AjaxResult.CODE_TAG)).isEqualTo(500);
        assertThat((String) overlong.get(AjaxResult.MSG_TAG)).contains("conversationId 不合法");
    }

    @Test
    @DisplayName("详情：正常返回 AjaxResult.success(data)，data 含 conversation + messages")
    void detailSuccessReturnsPayload()
    {
        AgentConversationDetailVo detail = new AgentConversationDetailVo();
        AgentConversationVo head = new AgentConversationVo();
        head.setConversationId("conv-ok");
        detail.setConversation(head);
        when(service.selectConversationDetail("conv-ok")).thenReturn(detail);

        AjaxResult result = controller().getInfo("conv-ok");

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(200);
        assertThat(result.get(AjaxResult.DATA_TAG)).isInstanceOf(AgentConversationDetailVo.class);
        assertThat(((AgentConversationDetailVo) result.get(AjaxResult.DATA_TAG)).getConversation())
                .isNotNull();
    }
}
