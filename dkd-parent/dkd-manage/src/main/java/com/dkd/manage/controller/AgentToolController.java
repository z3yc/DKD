package com.dkd.manage.controller;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.annotation.Log;
import com.dkd.common.constant.HttpStatus;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentTool;
import com.dkd.manage.domain.dto.AgentToolEditDto;
import com.dkd.manage.service.IAgentToolService;

/**
 * 智能体工具中心 Controller（排期 3-4，原型 S12 的工具部分）。
 *
 * <p>路径前缀 {@code /manage/agent/tool}；与配置台同一条纪律：写操作走 RuoYi 权限点 + {@code @Log}，
 * **不经 {@code /agent/**} 网关**。
 *
 * <p>只有两个接口：
 * <ul>
 *   <li>{@code GET /list}：全量清单（工具表由 seed/MCP 写入，量小且需要整体看，不做分页）；</li>
 *   <li>{@code PUT /{id}}：只改 enabled/timeoutMs/rowLimit。没有新增/删除接口——
 *       工具行必须"code 能映射到代码"，后台随意增删会让启动校验失败或造出不可调用的幻影工具。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/tool")
public class AgentToolController extends BaseController
{
    @Autowired
    private IAgentToolService agentToolService;

    /**
     * 工具列表（全量；可按 scope/source/enabled 过滤）。
     *
     * @param query 过滤条件
     * @return {@code data} 为工具数组（前端读 res.data；不包 rows/total——全量清单不需要分页）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentTool:list')")
    @GetMapping("/list")
    public AjaxResult list(AgentTool query)
    {
        return success(agentToolService.selectAgentToolList(query));
    }

    /**
     * 编辑工具护栏参数（enabled/timeoutMs/rowLimit）。
     *
     * <p>不可变字段（code/source/readOnly/builtin）传了不同值 → 字段错误（HTTP 200 + code 400 + data{field:msg}），
     * 便于前端把错误定位到具体字段；不存在 → 业务错误（code 500）。
     *
     * @param id  主键
     * @param dto 入参
     * @return 更新后的工具
     */
    @PreAuthorize("@ss.hasPermi('manage:agentTool:edit')")
    @Log(title = "智能体工具", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}")
    public AjaxResult edit(@PathVariable("id") Long id, @RequestBody AgentToolEditDto dto)
    {
        return success(agentToolService.updateAgentTool(id, dto, getUsername()));
    }

    /**
     * 字段校验失败 → HTTP 200 + code 400 + data:{field:msg}（与配置台响应形态一致）。
     *
     * @param e 字段错误
     * @return 统一响应
     */
    @ExceptionHandler(AgentFieldErrorException.class)
    public AjaxResult handleFieldError(AgentFieldErrorException e)
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put(e.getField(), e.getMessage());
        return new AjaxResult(HttpStatus.BAD_REQUEST, e.getMessage(), data);
    }
}
