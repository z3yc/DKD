package com.dkd.manage.controller;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.dkd.common.annotation.Log;
import com.dkd.common.constant.HttpStatus;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.agent.AgentConfigConflictException;
import com.dkd.manage.agent.AgentConfigValidationException;
import com.dkd.manage.agent.AgentEvalGateException;
import com.dkd.manage.domain.AgentConfig;
import com.dkd.manage.domain.dto.AgentConfigDto;
import com.dkd.manage.domain.dto.AgentConfigEnabledDto;
import com.dkd.manage.domain.dto.AgentConfigRollbackDto;
import com.dkd.manage.service.IAgentConfigService;

/**
 * 智能体配置管理 Controller（排期 2-2，契约 §4.1）。
 *
 * <p>路径前缀 {@code /manage/agent/config}，**不经 {@code /agent/**} 网关**（网关负责用户对话转发，
 * 配置台是后台管理功能，走 RuoYi 的 JWT 过滤链与权限点）。
 *
 * <p>本类只做三件事：取参 → 调 Service → 包 AjaxResult。唯一「像逻辑」的部分是两个异常处理器，
 * 它们负责把异常翻译成契约 §4.2 的两种响应形态：
 * <ul>
 *   <li>字段校验失败：HTTP 200 + {@code {code:400, msg, data:{field:msg}}}（前端表单标红）；</li>
 *   <li>乐观锁冲突：**HTTP 409** + {@code {code:409, msg, data:{version:"当前版本 x，提交版本 y"}}}
 *       ——必须是 409，前端据此提示刷新并重取，而不是当成普通业务错误弹一句就完；</li>
 * </ul>
 * 其余业务错误（不存在/非法动作）交给 RuoYi 全局异常处理器（code 500，契约 §4.2）。
 *
 * <p>本期不提供 delete 接口：配置停用即可，物理/软删都会让 {@code agent_config_history}
 * 与 {@code agent_run.config_version} 的引用断裂（契约 §4.4 最后一行）。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
@RestController
@RequestMapping("/manage/agent/config")
public class AgentConfigController extends BaseController
{
    @Autowired
    private IAgentConfigService agentConfigService;

    /**
     * 配置列表（分页）。
     *
     * <p>查询参数：code/name（模糊）、runtime/enabled/builtin（精确）+ RuoYi 分页参数。
     *
     * @param query 过滤条件
     * @return 分页列表（paramsJson 为对象）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:list')")
    @GetMapping("/list")
    public TableDataInfo list(AgentConfig query)
    {
        startPage();
        return getDataTable(agentConfigService.selectAgentConfigList(query));
    }

    /**
     * 元数据（契约 §4.6）：新增/编辑表单的模型、工具、占位符、参数区间与默认提示词来源。
     *
     * <p>为什么放在 {@code /{id}} 之前不影响路由：Spring MVC 优先匹配字面量路径，
     * {@code /meta} 不会被当成 id 解析（id 是 Long，解析失败会直接 400）。
     *
     * @param runtime 目标载体（缺省/未知按 chat 返回）
     * @return 元数据
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:query')")
    @GetMapping("/meta")
    public AjaxResult meta(@RequestParam(value = "runtime", required = false) String runtime)
    {
        return success(agentConfigService.selectMeta(runtime));
    }

    /**
     * 配置详情。
     *
     * @param id 主键
     * @return 配置详情
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return success(agentConfigService.selectAgentConfigById(id));
    }

    /**
     * 版本历史（含每条快照 JSON 全文，供「快照查看 / 回滚」）。
     *
     * @param id 主键
     * @return 历史列表（按版本倒序）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:query')")
    @GetMapping("/{id}/history")
    public AjaxResult history(@PathVariable("id") Long id)
    {
        return success(agentConfigService.selectHistoryByConfigId(id));
    }

    /**
     * 新增配置（本期只允许 chat 载体；builtin 强制 0）。
     *
     * @param dto 入参
     * @return 落库后的配置（含 version=1，前端用于提示「版本 v1 已生效」）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:add')")
    @Log(title = "智能体配置", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody AgentConfigDto dto)
    {
        return success(agentConfigService.insertAgentConfig(dto, getUsername()));
    }

    /**
     * 编辑配置（乐观锁：body 必带 version）。
     *
     * @param id  主键（以路径为准，防止 body 的 id 与路径不一致时改错行）
     * @param dto 入参
     * @return 保存后的配置（version 已 +1）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:edit')")
    @Log(title = "智能体配置", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}")
    public AjaxResult edit(@PathVariable("id") Long id, @RequestBody AgentConfigDto dto)
    {
        dto.setId(id);
        return success(agentConfigService.updateAgentConfig(dto, getUsername()));
    }

    /**
     * 启用/停用（版本 +1；停用必填原因 5~100 字）。
     *
     * @param id  主键
     * @param dto 入参（enabled/disabledReason/version）
     * @return 保存后的配置（version 已 +1）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:edit')")
    @Log(title = "智能体配置", businessType = BusinessType.UPDATE)
    @PutMapping("/{id}/enabled")
    public AjaxResult updateEnabled(@PathVariable("id") Long id, @RequestBody AgentConfigEnabledDto dto)
    {
        return success(agentConfigService.updateEnabled(id, dto, getUsername()));
    }

    /**
     * 版本回滚：把历史快照复制成新版本（不是原地回退）。
     *
     * @param id  主键
     * @param dto 入参（targetVersion/version/changeNote）
     * @return 回滚后的配置（version 已 +1）
     */
    @PreAuthorize("@ss.hasPermi('manage:agentConfig:rollback')")
    @Log(title = "智能体配置", businessType = BusinessType.UPDATE)
    @PostMapping("/{id}/rollback")
    public AjaxResult rollback(@PathVariable("id") Long id, @RequestBody AgentConfigRollbackDto dto)
    {
        return success(agentConfigService.rollback(id, dto, getUsername()));
    }

    /**
     * 字段校验失败 → HTTP 200 + code 400 + data:{field:msg}（契约 §4.2）。
     *
     * <p>为什么 HTTP 仍是 200：RuoYi 前端 axios 拦截器把非 200 当传输/权限错误，
     * 字段错误属于「表单没填对」，必须让页面能拿到 msg 并给具体字段标红。
     *
     * @param e 字段校验异常
     * @return 统一响应
     */
    @ExceptionHandler(AgentConfigValidationException.class)
    public AjaxResult handleValidationException(AgentConfigValidationException e)
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put(e.getField(), e.getMessage());
        return new AjaxResult(HttpStatus.BAD_REQUEST, e.getMessage(), data);
    }

    /**
     * 乐观锁冲突 → **HTTP 409**（契约 §4.2）。
     *
     * <p>为什么必须是真的 409 而不是 200+code 409：前端据 HTTP 状态码决定「刷新详情并提示重试」
     * 还是走普通错误提示；两者混在一起时，并发覆盖会被当成普通失败悄悄吞掉。
     *
     * @param e 版本冲突异常
     * @return 409 响应（含当前/提交版本，便于排障）
     */
    @ExceptionHandler(AgentConfigConflictException.class)
    public ResponseEntity<AjaxResult> handleConflictException(AgentConfigConflictException e)
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put("version", "当前版本 " + e.getCurrentVersion() + "，提交版本 " + e.getSubmittedVersion());
        AjaxResult body = new AjaxResult(HttpStatus.CONFLICT, "配置已被他人更新，请刷新后重试", data);
        return ResponseEntity.status(HttpStatus.CONFLICT).body(body);
    }

    /**
     * 评测护栏拒绝 → **HTTP 200 + code 409 + data{field:"evalGate", reason, latest}**（排期 5-1）。
     *
     * <p>为什么是 HTTP 200 而不是 409：这是业务规则拒绝（"没通过评测"），前端要拿到
     * reason 与 latest 显示"去跑评测 / 强制跳过"的专用提示；用 HTTP 409 会与乐观锁冲突
     * （那个要刷新重试）混在一起，页面只能二选一处理。契约 §4.2 的"字段错误走 200"同一思路。
     *
     * @param e 评测护栏异常
     * @return 200 响应（body code=409）
     */
    @ExceptionHandler(AgentEvalGateException.class)
    public AjaxResult handleEvalGateException(AgentEvalGateException e)
    {
        Map<String, Object> data = new LinkedHashMap<String, Object>();
        data.put("field", "evalGate");
        data.put("reason", e.getReason());
        data.put("latest", e.getLatest());
        return new AjaxResult(HttpStatus.CONFLICT, "未通过评测护栏：" + e.getReason(), data);
    }
}
