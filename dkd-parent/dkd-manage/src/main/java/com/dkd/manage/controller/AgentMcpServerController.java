package com.dkd.manage.controller;

import java.util.LinkedHashMap;
import java.util.Map;

import javax.servlet.http.HttpServletRequest;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.annotation.Log;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentMcpUpstreamException;
import com.dkd.manage.domain.dto.AgentMcpServerDto;
import com.dkd.manage.domain.dto.AgentMcpServerEnabledDto;
import com.dkd.manage.service.IAgentMcpServerService;

/**
 * MCP server 接入 Controller（排期 3-7，原型 S12）。
 *
 * <p>路径前缀 {@code /manage/agent/mcp}，**不经 {@code /agent/**} 网关**。
 *
 * <p><b>职责切分：</b>配置（增/改/启停/查）由 Java 直接管 {@code agent_mcp_server} 表
 * （RuoYi 权限 + {@code @Log} 审计）；探测/同步/工具清单是 Python 的活，这里只按 server code
 * 代理调用（服务间密钥），**不代理 tools/call**——管理台开"任意工具调用"入口等于给后台账号
 * 一条可绕过人工确认的外呼通道（AGENTS §7.9），首期不需要。
 *
 * <p><b>安全语义：</b>envKeys 只接收 "HTTP 头名 → 环境变量名" 的映射，不接收/不返回密钥值；
 * readOnlyOnly 固定 1（写工具一律不接入）；新增 enabled 默认 0，须人工安全复核后开启。
 * 上游失败：Python 不可达 → 503；上游非 2xx/信封失败 → 502，提示由 Java 重组、不透传上游报文。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/mcp")
public class AgentMcpServerController extends BaseController
{
    @Autowired
    private IAgentMcpServerService agentMcpServerService;

    /**
     * 配置列表（含 Python 回写的 status/lastProbeTime/lastProbeMsg，envKeys 为变量名映射）。
     *
     * @return {@code data} 为配置数组（前端读 res.data）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:list')")
    @GetMapping("/list")
    public AjaxResult list()
    {
        return success(agentMcpServerService.selectServerList());
    }

    /**
     * 配置详情（目前页面编辑用列表行数据、不依赖本接口；保留给排障/后续页面）。
     *
     * @param id 主键
     * @return 配置
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return success(agentMcpServerService.selectServerById(id));
    }

    /**
     * 新增配置（enabled 缺省 0；transport 仅 http；envKeys 只收环境变量名）。
     *
     * @param dto 入参
     * @return 落库后的配置
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:add')")
    @Log(title = "MCP server", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody AgentMcpServerDto dto)
    {
        return success(agentMcpServerService.insertServer(dto, getUsername()));
    }

    /**
     * 编辑配置（编码只读）。
     *
     * @param id  主键（以路径为准，防止 body 的 id 与路径不一致时改错行）
     * @param dto 入参
     * @return 更新后的配置
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:edit')")
    @Log(title = "MCP server", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}")
    public AjaxResult edit(@PathVariable("id") Long id, @RequestBody AgentMcpServerDto dto)
    {
        dto.setId(id);
        return success(agentMcpServerService.updateServer(dto, getUsername()));
    }

    /**
     * 启用/停用配置（二次确认在前端；启用前应已探测通过，属流程约定）。
     *
     * @param id  主键
     * @param dto 入参（enabled 0/1）
     * @return 更新后的配置
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:edit')")
    @Log(title = "MCP server", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}/enabled")
    public AjaxResult updateEnabled(@PathVariable("id") Long id, @RequestBody AgentMcpServerEnabledDto dto)
    {
        return success(agentMcpServerService.updateEnabled(id, dto, getUsername()));
    }

    /**
     * 探测（代理 Python：会同步只读工具并回写 status/lastProbe*）。
     *
     * <p>单独权限点 {@code manage:agentMcp:probe}：探测会真实出网访问外部 MCP server，
     * 能看配置的人不必自动获得"让服务器访问外部地址"的能力。
     *
     * @param id      主键
     * @param request 当前请求（取 requestId）
     * @return Python 信封 data
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:probe')")
    @Log(title = "MCP server", businessType = BusinessType.OTHER)
    @PostMapping("/{id}/probe")
    public AjaxResult probe(@PathVariable("id") Long id, HttpServletRequest request)
    {
        return success(agentMcpServerService.probe(id, AgentRequestId.resolve(request)));
    }

    /**
     * 同步只读工具（代理 Python，不重新探测）。
     *
     * @param id      主键
     * @param request 当前请求（取 requestId）
     * @return Python 信封 data（{synced,rejected,disabled}）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:probe')")
    @Log(title = "MCP server", businessType = BusinessType.OTHER)
    @PostMapping("/{id}/sync")
    public AjaxResult sync(@PathVariable("id") Long id, HttpServletRequest request)
    {
        return success(agentMcpServerService.sync(id, AgentRequestId.resolve(request)));
    }

    /**
     * 已同步工具清单（代理 Python，保证与运行时实际生效清单一致）。
     *
     * @param id      主键
     * @param request 当前请求（取 requestId）
     * @return Python 信封 data（工具数组）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentMcp:list')")
    @GetMapping("/{id}/tools")
    public AjaxResult tools(@PathVariable("id") Long id, HttpServletRequest request)
    {
        return success(agentMcpServerService.tools(id, AgentRequestId.resolve(request)));
    }

    /**
     * 字段校验失败 → HTTP 200 + code 400 + data{field:msg}（与其他智能体管理页一致）。
     *
     * @param e 字段错误
     * @return 统一响应
     */
    @ExceptionHandler(AgentFieldErrorException.class)
    public AjaxResult handleFieldError(AgentFieldErrorException e)
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put(e.getField(), e.getMessage());
        return new AjaxResult(com.dkd.common.constant.HttpStatus.BAD_REQUEST, e.getMessage(), data);
    }

    /**
     * 上游失败 → HTTP 503（不可达/超时）/ 502（有响应但不成功）；消息由 Java 重组、不透传上游 body。
     *
     * @param e 上游异常
     * @return 统一响应
     */
    @ExceptionHandler(AgentMcpUpstreamException.class)
    public ResponseEntity<AjaxResult> handleUpstream(AgentMcpUpstreamException e)
    {
        int status = e.isTransportFailure() ? HttpStatus.SERVICE_UNAVAILABLE.value()
                : HttpStatus.BAD_GATEWAY.value();
        return ResponseEntity.status(status).body(AjaxResult.error(status, e.getMessage()));
    }
}
