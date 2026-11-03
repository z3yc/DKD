package com.dkd.manage.controller;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.RequestMapping;

import com.dkd.common.annotation.Log;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.manage.agent.AgentConfigConflictException;
import com.dkd.manage.agent.AgentConfigValidationException;

/**
 * 配置 Controller 的**响应契约形状**测试（排期 2-2，契约 §4.1/§4.2）。
 *
 * <p>为什么这层也要测：Service 的异常类型与 HTTP 形态之间隔着一个异常处理器，
 * 这段映射是「前端能不能正确提示」的唯一开关：
 * <ul>
 *   <li>字段错误若被包成 500，前端拿不到 data 里的字段名，只能弹一个笼统错误；</li>
 *   <li>乐观锁冲突若被包成 200，前端会当成普通业务失败，不会刷新后重试——并发覆盖就被悄悄吞掉。</li>
 * </ul>
 * 这里直接调处理器方法断言 code/HTTP 状态/数据结构，不启动 Spring 容器（AGENTS §8 不依赖环境）。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
class AgentConfigControllerTest
{
    private final AgentConfigController controller = new AgentConfigController();

    @Test
    @DisplayName("字段校验失败：HTTP 200 + code 400 + data:{field:msg}")
    void 字段校验失败返回400与字段映射()
    {
        AjaxResult result = controller.handleValidationException(
                new AgentConfigValidationException("name", "名称已存在：我的助手"));

        assertThat(result.get(AjaxResult.CODE_TAG)).isEqualTo(400);
        assertThat(result.get(AjaxResult.MSG_TAG)).isEqualTo("名称已存在：我的助手");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) result.get(AjaxResult.DATA_TAG);
        assertThat(data).containsEntry("name", "名称已存在：我的助手");
    }

    @Test
    @DisplayName("乐观锁冲突：HTTP 409 + code 409 + data.version 含当前/提交版本")
    void 乐观锁冲突返回HTTP409与版本信息()
    {
        ResponseEntity<AjaxResult> response = controller
                .handleConflictException(new AgentConfigConflictException(5L, 3L));

        assertThat(response.getStatusCodeValue()).isEqualTo(409);
        AjaxResult body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.get(AjaxResult.CODE_TAG)).isEqualTo(409);
        assertThat(body.get(AjaxResult.MSG_TAG)).isEqualTo("配置已被他人更新，请刷新后重试");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) body.get(AjaxResult.DATA_TAG);
        assertThat((String) data.get("version")).contains("当前版本 5").contains("提交版本 3");
    }

    @Test
    @DisplayName("路径前缀与权限点符合契约 §4.1（8 个接口各就各位）")
    void 路径与权限点符合契约() throws Exception
    {
        RequestMapping mapping = AgentConfigController.class.getAnnotation(RequestMapping.class);
        assertThat(mapping).isNotNull();
        assertThat(mapping.value()).containsExactly("/manage/agent/config");

        Map<String, String> expectedPermissions = new LinkedHashMap<String, String>();
        expectedPermissions.put("list", "manage:agentConfig:list");
        expectedPermissions.put("getInfo", "manage:agentConfig:query");
        expectedPermissions.put("history", "manage:agentConfig:query");
        expectedPermissions.put("meta", "manage:agentConfig:query");
        expectedPermissions.put("add", "manage:agentConfig:add");
        expectedPermissions.put("edit", "manage:agentConfig:edit");
        expectedPermissions.put("updateEnabled", "manage:agentConfig:edit");
        expectedPermissions.put("rollback", "manage:agentConfig:rollback");

        for (Map.Entry<String, String> entry : expectedPermissions.entrySet())
        {
            Method method = findMethod(entry.getKey());
            PreAuthorize preAuthorize = method.getAnnotation(PreAuthorize.class);
            assertThat(preAuthorize).as("接口 %s 缺少 @PreAuthorize", entry.getKey()).isNotNull();
            assertThat(preAuthorize.value()).as("接口 %s 权限点不符", entry.getKey())
                    .isEqualTo("@ss.hasPermi('" + entry.getValue() + "')");
        }

        // 写接口必须留痕（@Log）：配置变更属于审计证据
        for (String writeMethod : new String[] { "add", "edit", "updateEnabled", "rollback" })
        {
            Log log = findMethod(writeMethod).getAnnotation(Log.class);
            assertThat(log).as("写接口 %s 缺少 @Log", writeMethod).isNotNull();
            assertThat(log.title()).isEqualTo("智能体配置");
        }
    }

    private Method findMethod(String name)
    {
        for (Method method : AgentConfigController.class.getDeclaredMethods())
        {
            if (method.getName().equals(name))
            {
                return method;
            }
        }
        throw new AssertionError("AgentConfigController 找不到方法 " + name);
    }
}
