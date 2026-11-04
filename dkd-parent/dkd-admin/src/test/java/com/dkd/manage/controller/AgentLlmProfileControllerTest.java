package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.annotation.Log;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.dto.AgentLlmProfileDto;

/**
 * 模型档案 Controller 的路径/权限/审计/错误形状测试（排期 3-5）。
 *
 * <p>重点断言**权限点拆分**：{@code /probe} 会真实出网调模型（消耗公司额度），
 * 必须单独 {@code manage:agentLlmProfile:probe}，不能复用 list/edit——否则"能看档案"
 * 就等于"能用额度探测"。另外断言所有写接口都挂了 {@code @Log}（审计要求）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentLlmProfileControllerTest
{
    @Test
    @DisplayName("路径前缀 /manage/agent/llm/profile（不经 /agent 网关）")
    void pathPrefixMatchesContract()
    {
        RequestMapping mapping = AgentLlmProfileController.class.getAnnotation(RequestMapping.class);

        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/llm/profile");
    }

    @Test
    @DisplayName("接口与权限点逐一就位（list/add/edit/probe 分开授权）")
    void endpointPermissionsMatchContract() throws Exception
    {
        Map<String, String> expected = new LinkedHashMap<String, String>();
        expected.put("list", "manage:agentLlmProfile:list");
        expected.put("add", "manage:agentLlmProfile:add");
        expected.put("edit", "manage:agentLlmProfile:edit");
        expected.put("updateEnabled", "manage:agentLlmProfile:edit");
        expected.put("probe", "manage:agentLlmProfile:probe");
        expected.put("probes", "manage:agentLlmProfile:list");

        for (Map.Entry<String, String> entry : expected.entrySet())
        {
            Method method = findMethod(entry.getKey());
            PreAuthorize preAuthorize = method.getAnnotation(PreAuthorize.class);
            assertThat(preAuthorize).as("接口 " + entry.getKey() + " 缺少权限注解").isNotNull();
            assertThat(preAuthorize.value()).as("接口 " + entry.getKey())
                    .isEqualTo("@ss.hasPermi('" + entry.getValue() + "')");
        }
    }

    @Test
    @DisplayName("写接口都挂 @Log 审计；探活单独 OTHER 类型")
    void writeEndpointsAreAudited() throws Exception
    {
        for (String name : new String[] { "add", "edit", "updateEnabled", "probe" })
        {
            Log log = findMethod(name).getAnnotation(Log.class);
            assertThat(log).as("写接口 " + name + " 必须进 sys_oper_log").isNotNull();
            assertThat(log.title()).isEqualTo("智能体模型档案");
        }
        assertThat(findMethod("probe").getAnnotation(Log.class).businessType().name()).isEqualTo("OTHER");
        // 只读接口不写操作日志
        assertThat(findMethod("list").getAnnotation(Log.class)).isNull();
        assertThat(findMethod("probes").getAnnotation(Log.class)).isNull();
    }

    @Test
    @DisplayName("字段错误处理器：HTTP 200 + code 400 + data{field:msg}")
    void fieldErrorHandlerMapsToFieldEnvelope()
    {
        AgentLlmProfileController controller = new AgentLlmProfileController();

        AjaxResult result = controller.handleFieldError(new AgentFieldErrorException("apiKeyEnv", "只填变量名"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        assertThat(result.get(AjaxResult.MSG_TAG)).isEqualTo("只填变量名");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("apiKeyEnv", "只填变量名");
    }

    @Test
    @DisplayName("DTO 没有密钥值字段（只有 apiKeyEnv 变量名）")
    void dtoExposesEnvNameOnly()
    {
        for (java.lang.reflect.Field field : AgentLlmProfileDto.class.getDeclaredFields())
        {
            String fieldName = field.getName().toLowerCase();
            if ("apikeyenv".equals(fieldName))
            {
                continue;
            }
            assertThat(fieldName).as("DTO 出现疑似密钥字段：" + field.getName()).doesNotContain("apikey")
                    .doesNotContain("secret").doesNotContain("password");
        }
    }

    /** 按方法名找接口方法（参数用占位，只用于读注解）。 */
    private Method findMethod(String name)
    {
        for (Method method : AgentLlmProfileController.class.getDeclaredMethods())
        {
            if (method.getName().equals(name))
            {
                return method;
            }
        }
        throw new AssertionError("找不到接口方法 " + name);
    }
}
