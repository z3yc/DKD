package com.dkd.common.agent.controller;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StreamUtils;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import com.alibaba.fastjson2.JSON;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.domain.AgentUserContext;
import com.dkd.common.agent.filter.AgentCallbackAuthFilter;
import com.dkd.common.agent.support.AgentClientAbortException;
import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.agent.support.AgentUpstreamException;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.domain.model.LoginUser;
import com.dkd.common.utils.SecurityUtils;

/**
 * 智能体网关（任务 0-6，方案 §3.3）：前端 /agent/** 一律经此转发到 Python 智能体服务。
 *
 * <p>职责边界：只做「鉴权上下文重建 + 转发 + 降级」，不含任何业务逻辑（AGENTS §2.1）。
 *
 * <p>为什么 SSE 单独一个入口而不全部走泛化代理：SSE 必须逐块写响应流（前端逐 token 渲染），
 * 而其余接口需要“先拿状态码再决定透传/降级”，两者响应模型不同，混在一个方法里必然写一堆分支判断。
 *
 * <p>为什么 SSE 用同步写 Servlet 输出流、而不是 StreamingResponseBody：本模块 dkd-common 的依赖
 * 只到 spring-web（不含 spring-webmvc），拿不到 StreamingResponseBody；且同步写反而省掉异步 servlet 的
 * asyncTimeout（Tomcat 默认 30s，会把长回复的流掐断）这类隐性配置。语义上两者一致，
 * 关键约束都是“读一块写一块并立即 flush，禁止先把响应体读完再返回”。
 *
 * <p>降级语义（方案 §3.4）：
 * <ul>
 *   <li>{@code agent.enabled=false}：非 SSE 接口返回 HTTP 503 + JSON 信封；SSE 接口返回 error+done 帧
 *       （前端对该路径只按 SSE 解析，返回 JSON 会让前端解析器直接报错，故不能混用）；</li>
 *   <li>Python 不可达/自身 5xx：同上，但提示语不同（区分“没开”和“挂了”，前者要运维开开关，后者要等等）；</li>
 *   <li>上游 4xx（参数错误/限额 429）：**原样透传**，因为它携带明确的业务语义，替换成 503 会让前端丢失原因。</li>
 * </ul>
 *
 * <p>越权防护（P0 安全修复，2026-10-21）：
 * <ul>
 *   <li>**为什么取消 {@code /**} 泛化代理**：泛化代理会把 Python 未来新增的任何路由自动暴露给“只要登录”的
 *       后台账号；越权问题不会在网关层报错，而是等业务在 Python 侧被调用后才发生，属于最隐蔽的一类。
 *       因此改为**显式路径白名单**：只有下面列出的路由会被转发，白名单外一律 404（fail-closed），
 *       新增接口必须回来改这里（多一步评审，换来“默认拒绝”）。</li>
 *   <li>**为什么权限点判断放在 Controller 内而不是 SecurityConfig**：SecurityConfig 的
 *       {@code /agent/** authenticated()} 是“登录即可”的通用规则，若在此收窄会连带影响
 *       {@code /agent/status}、{@code /agent/chat}（前端依赖“登录即可”）；而补货的读/写权限点是
 *       本网关特有的路由语义，就近判断可把“路径 → 权限点”的映射与白名单放在同一张表里，
 *       避免两处配置漂移。</li>
 *   <li>权限点口径：读（补货清单 / 暂停状态，GET）→ {@code manage:task:list}；
 *       写（confirm/adjust/skip/restore/pause/resume，POST）→ {@code manage:task:add}。
 *       超管（userId=1）直接放行，与 RuoYi {@code @ss.hasPermi} 的语义保持一致。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@RestController
@RequestMapping("/agent")
public class AgentGatewayController
{
    private static final Logger log = LoggerFactory.getLogger(AgentGatewayController.class);

    /**
     * 上游对话路径（Python app/api/chat.py 的 router 前缀 + 路由）
     */
    private static final String CHAT_PATH = "/agent/chat";

    /**
     * 补货读接口（GET）：需要 {@code manage:task:list}
     */
    private static final String PATH_RESTOCK_PLANS = "/agent/restock/plans";

    private static final String PATH_RESTOCK_PAUSE = "/agent/restock/pause";

    /**
     * 补货写接口：需要 {@code manage:task:add}
     */
    private static final String PATH_RESTOCK_RESUME = "/agent/restock/resume";

    /**
     * 计划级写动作路径正则：confirm / adjust / skip / restore。
     *
     * <p>为什么用“数字 planId + 固定动作名”的白名单正则而不是 startsWith：startsWith 会把
     * {@code /agent/restock/plans/1/delete} 之类的未来路径也当成写操作放过去；显式枚举动作名 +
     * {@code \d+} 才能保证“路径形状”本身也是白名单的一部分（fail-closed）。
     */
    private static final Pattern RESTOCK_PLAN_ACTION = Pattern.compile(
            "^/agent/restock/plans/\\d+/(confirm|adjust|skip|restore)$");

    /**
     * 读权限点（补货清单、暂停状态）
     */
    private static final String PERMISSION_RESTOCK_READ = "manage:task:list";

    /**
     * 写权限点（确认建单、调整、跳过、恢复、暂停/恢复自动分析）
     */
    private static final String PERMISSION_RESTOCK_WRITE = "manage:task:add";

    /**
     * 故障诊断起跑（POST /agent/fault/runs）：只读业务数据，给“能看设备”的账号即可。
     */
    private static final String PATH_FAULT_RUNS = "/agent/fault/runs";

    /**
     * 故障派单确认路径正则：`/agent/fault/runs/{runId}/confirm`（runId 形如 `fault-8ecd8058c359`）。
     *
     * <p>为什么用正则而不是 startsWith：runId 是路径变量，若只判前缀，未来出现
     * `/agent/fault/runs/x/confirm/evil` 之类路径也会被放行；显式约束字符集（字母数字下划线短横，
     * 1~64 位）+ 固定后缀，路径形状本身才是白名单的一部分（fail-closed）。
     */
    private static final Pattern FAULT_CONFIRM_ACTION = Pattern.compile(
            "^/agent/fault/runs/[A-Za-z0-9_-]{1,64}/confirm$");

    /**
     * 故障诊断权限点（登录 + 能看设备）
     */
    private static final String PERMISSION_FAULT_DIAGNOSE = "manage:vm:query";

    /**
     * 故障派单确认权限点（会经 Java 回调建维修工单，与补货确认同级）
     */
    private static final String PERMISSION_FAULT_CONFIRM = "manage:task:add";

    /**
     * 回调路径前缀：回调是 Python → Java 的单向通道，绝不经网关暴露给前端（防御性兜底）
     */
    private static final String CALLBACK_PATH_PREFIX = AgentCallbackAuthFilter.CALLBACK_PATH_PREFIX;

    private static final String DEGRADE_MSG_DISABLED = "智能体服务未启用";

    private static final String DEGRADE_MSG_UNAVAILABLE = "智能体服务暂不可用，请稍后重试";

    private final AgentProperties properties;

    private final AgentUpstreamClient upstreamClient;

    public AgentGatewayController(AgentProperties properties, AgentUpstreamClient upstreamClient)
    {
        this.properties = properties;
        this.upstreamClient = upstreamClient;
    }

    /**
     * 入口可用性探测：前端据此决定是否隐藏/禁用对话入口（降级时**不伪造**会话能力，方案 §3.3）。
     *
     * @return {@code data.enabled} 开关状态；{@code data.upstream} 为 up/down
     */
    @GetMapping("/status")
    public AjaxResult status()
    {
        boolean enabled = properties.getEnabled();
        Map<String, Object> data = new HashMap<String, Object>();
        data.put("enabled", enabled);
        // 开关关掉时不必再探活：既省一次跨进程调用，也避免“开关关了还显示 up”的误导
        data.put("upstream", enabled && upstreamClient.pingHealth() ? "up" : "down");
        return AjaxResult.success(data);
    }

    /**
     * 流式对话（SSE 透传）。
     *
     * <p>降级也通过 SSE 错误帧表达（该路径前端只按 SSE 解析，回 JSON 会让前端解析器直接报错），
     * 因此 HTTP 状态恒为 200；前端是否隐藏入口依据 {@code GET /agent/status}。
     *
     * @param request 当前请求
     * @param response 当前响应（HTTP 状态与头部必须在写第一块前确定，写后就不可改）
     * @throws IOException 写出失败（客户端断开等）
     */
    @PostMapping("/chat")
    public void chat(HttpServletRequest request, HttpServletResponse response) throws IOException
    {
        // 请求体必须在转发前读完：上游一旦开始回帧，本请求的输入流语义就不再可控
        byte[] payload = readBody(request);
        // requestId 必须先解析一次再向下传：若在响应头与转发头处各解析一次，前端拿到的 trace 与上游收到的
        // 会是两个不同的值，全链路追踪直接断掉（单测已覆盖该回归）
        String requestId = resolveRequestId(request);
        Map<String, String> upstreamHeaders = buildUpstreamHeaders(request, requestId);
        String userId = upstreamHeaders.get(AgentHeaders.USER);

        applySseHeaders(response, requestId);
        OutputStream outputStream = response.getOutputStream();
        if (!properties.getEnabled())
        {
            writeDegradeFrames(outputStream, DEGRADE_MSG_DISABLED, requestId);
            return;
        }
        try
        {
            upstreamClient.relayStream(HttpMethod.POST.name(), CHAT_PATH, request.getQueryString(), upstreamHeaders,
                    payload, outputStream);
        }
        catch (AgentClientAbortException e)
        {
            // 用户主动取消（AbortController）属正常操作，不写错误帧、不打 WARN（否则监控噪声掩盖真故障）
            log.info("客户端已断开，停止 SSE 中继 requestId={} userId={}", requestId, userId);
        }
        catch (AgentUpstreamException e)
        {
            log.warn("SSE 中继失败，返回降级帧 requestId={} userId={} upstreamStatus={} err={}", requestId, userId,
                    e.getStatus(), e.getMessage());
            writeDegradeFrames(outputStream, DEGRADE_MSG_UNAVAILABLE, requestId);
        }
    }

    /**
     * 白名单代理：只有这里显式列出的补货工作台路由才会被转发到 Python。
     *
     * <p>为什么把路径写在注解里而不是保留 {@code /**} 再在方法内判断：注解是**路由层的白名单**，
     * 未列出的路径根本不会进入本方法（Spring 直接 404），不给“判断写漏一个分支”留空间；
     * 本方法内还会再按 method + path 校验一次（双保险，也便于单测直接调用本方法验证拒绝路径）。
     *
     * @param request 当前请求
     * @return 上游响应（或降级 JSON）
     */
    @RequestMapping(value = {
            "/restock/plans",
            "/restock/plans/{planId:\\d+}/confirm",
            "/restock/plans/{planId:\\d+}/adjust",
            "/restock/plans/{planId:\\d+}/skip",
            "/restock/plans/{planId:\\d+}/restore",
            "/restock/pause",
            "/restock/resume",
            "/fault/runs",
            "/fault/runs/{runId}/confirm" },
            method = { RequestMethod.GET, RequestMethod.POST })
    public ResponseEntity<byte[]> proxy(HttpServletRequest request)
    {
        String path = resolvePath(request);
        if (isCallbackPath(path))
        {
            log.warn("拒绝经网关访问回调路径 path={}", path);
            return jsonResponse(HttpStatus.NOT_FOUND,
                    AjaxResult.error(HttpStatus.NOT_FOUND.value(), "回调路径不可经网关访问"));
        }
        // 失败关闭（fail-closed）：白名单外的路径不转发、不探开关、不回显上游任何信息。
        // 这里只记 method + path，**不记请求体**：请求体可能含业务数据/个人信息（AGENTS §6.1）。
        String permission = requiredPermission(request.getMethod(), path);
        if (permission == null)
        {
            log.warn("拒绝未在白名单内的智能体接口 method={} path={}", request.getMethod(), path);
            return jsonResponse(HttpStatus.NOT_FOUND,
                    AjaxResult.error(HttpStatus.NOT_FOUND.value(), "智能体接口不存在或方法不支持"));
        }
        // 权限点判断必须在转发之前：越权请求不应触达 Python、更不应产生建单等副作用。
        if (!hasPermission(permission))
        {
            log.warn("拒绝越权访问智能体接口 method={} path={} permission={}", request.getMethod(), path, permission);
            return jsonResponse(HttpStatus.FORBIDDEN,
                    AjaxResult.error(HttpStatus.FORBIDDEN.value(), "无权限访问该智能体接口"));
        }
        if (!properties.getEnabled())
        {
            return jsonResponse(HttpStatus.SERVICE_UNAVAILABLE,
                    AjaxResult.error(HttpStatus.SERVICE_UNAVAILABLE.value(), "智能体服务未启用（agent.enabled=false）"));
        }

        String requestId = resolveRequestId(request);
        Map<String, String> upstreamHeaders = buildUpstreamHeaders(request, requestId);
        try
        {
            AgentUpstreamResult result = upstreamClient.forward(request.getMethod(), path, request.getQueryString(),
                    upstreamHeaders, readBody(request));
            if (result.isServerError())
            {
                // 上游自身 5xx 不回传原始报文：可能含堆栈/内部信息，且前端也处理不了
                log.warn("智能体上游自身故障 requestId={} path={} upstreamStatus={}", requestId, path,
                        result.getStatus());
                return jsonResponse(HttpStatus.SERVICE_UNAVAILABLE,
                        AjaxResult.error(HttpStatus.SERVICE_UNAVAILABLE.value(), DEGRADE_MSG_UNAVAILABLE));
            }
            return passthrough(result, requestId);
        }
        catch (AgentUpstreamException e)
        {
            log.warn("智能体服务不可达 requestId={} path={} err={}", requestId, path, e.getMessage());
            return jsonResponse(HttpStatus.SERVICE_UNAVAILABLE,
                    AjaxResult.error(HttpStatus.SERVICE_UNAVAILABLE.value(), DEGRADE_MSG_UNAVAILABLE));
        }
    }

    /**
     * 兜底：未注册的 /agent/** 一律 404（fail-closed），并留 WARN 便于发现“前端调用了不存在的路由”。
     *
     * <p>为什么不直接让 Spring 的 NoHandlerFound 处理：这里需要**记录被拒绝的路径**（不记请求体），
     * 否则“某个前端页面在打旧接口”只会表现为一条没有上下文的 404，排障时无从查起。
     *
     * @param request 当前请求
     * @return 404 JSON 信封
     */
    @RequestMapping("/**")
    public ResponseEntity<byte[]> rejectUnknown(HttpServletRequest request)
    {
        String path = resolvePath(request);
        if (isCallbackPath(path))
        {
            // 回调路径有自己的过滤器鉴权，这里只是防御性兜底（防止未来过滤器注册被误改后经网关暴露）
            log.warn("拒绝经网关访问回调路径 path={}", path);
            return jsonResponse(HttpStatus.NOT_FOUND,
                    AjaxResult.error(HttpStatus.NOT_FOUND.value(), "回调路径不可经网关访问"));
        }
        log.warn("拒绝未注册的智能体接口 method={} path={}", request.getMethod(), path);
        return jsonResponse(HttpStatus.NOT_FOUND,
                AjaxResult.error(HttpStatus.NOT_FOUND.value(), "智能体接口不存在"));
    }

    /**
     * 按「HTTP 方法 + 请求路径」算出所需权限点（null = 不在白名单，直接 404）。
     *
     * <p>为什么 GET 与 POST 分开：补货清单/暂停状态是**读**（登录用户看数据），
     * 确认建单/暂停分析是**写**（会经 Java 回调产生真实工单或改变全局自动分析状态）。
     * 两者权限点不同，混用会让“只能看清单”的账号获得建单能力——这正是本次要修的越权缺陷。
     *
     * @param method HTTP 方法
     * @param path 去掉 contextPath 的路径
     * @return 权限点；null 表示该 method/path 组合不在白名单内
     */
    private String requiredPermission(String method, String path)
    {
        if (HttpMethod.GET.matches(method))
        {
            if (PATH_RESTOCK_PLANS.equals(path) || PATH_RESTOCK_PAUSE.equals(path))
            {
                return PERMISSION_RESTOCK_READ;
            }
            return null;
        }
        if (HttpMethod.POST.matches(method))
        {
            // 故障场景（M3）：起跑=读业务数据；确认=建维修工单（会经 Java 回调产生真实业务事实）
            if (PATH_FAULT_RUNS.equals(path))
            {
                return PERMISSION_FAULT_DIAGNOSE;
            }
            if (FAULT_CONFIRM_ACTION.matcher(path).matches())
            {
                return PERMISSION_FAULT_CONFIRM;
            }
            if (PATH_RESTOCK_PAUSE.equals(path) || PATH_RESTOCK_RESUME.equals(path)
                    || RESTOCK_PLAN_ACTION.matcher(path).matches())
            {
                return PERMISSION_RESTOCK_WRITE;
            }
            return null;
        }
        // PUT/DELETE/PATCH 等一律不在白名单：即使 Spring 路由注解未来被放宽，也不会被转发
        return null;
    }

    /**
     * 判断当前登录用户是否具备权限点。
     *
     * <p>为什么用 {@link SecurityUtils#hasPermi(java.util.Collection, String)} 而不是自己遍历：
     * 它已包含 RuoYi 的 {@code *:*:*} 通配语义，自己实现会在“权限串通配”上产生第二套口径。
     * 超管直接放行的原因：RuoYi 的 userId=1 是内置超管，其 permissions 可能因角色配置缺失而不全，
     * 但 {@code @PreAuthorize("@ss.hasPermi(...)")} 的判权注解对超管同样放行，这里必须与之一致。
     *
     * @param permission 所需权限点
     * @return true 表示放行
     */
    private boolean hasPermission(String permission)
    {
        LoginUser loginUser;
        try
        {
            loginUser = SecurityUtils.getLoginUser();
        }
        catch (RuntimeException e)
        {
            // /agent/** 已由 Spring Security 要求 authenticated()，正常请求不会走到这里；
            // 走到这里说明身份上下文缺失（过滤器顺序被改/内部转发），按无权限拒绝，绝不放行。
            log.warn("无法解析登录用户，按无权限拒绝智能体接口 permission={}", permission);
            return false;
        }
        if (SecurityUtils.isAdmin(loginUser.getUserId()))
        {
            return true;
        }
        Set<String> permissions = loginUser.getPermissions();
        if (permissions == null)
        {
            permissions = Collections.emptySet();
        }
        return SecurityUtils.hasPermi(permissions, permission);
    }

    /**
     * 设置 SSE 响应头。
     *
     * <p>为什么在写第一块**之前**设置：状态与头部一旦有字节写出就 commit，之后再改无效；
     * 为什么显式声明 no-transform / X-Accel-Buffering：中间层（Nginx 等）默认会缓冲/压缩响应，
     * 把逐帧输出攒成整包，流式体验失效（方案 §3.3 的流式约束）。
     *
     * @param response 当前响应
     * @param requestId 全链路追踪 ID，回带前端便于对日志
     */
    private void applySseHeaders(HttpServletResponse response, String requestId)
    {
        response.setStatus(HttpStatus.OK.value());
        response.setContentType(MediaType.TEXT_EVENT_STREAM_VALUE + ";charset=UTF-8");
        response.setHeader(HttpHeaders.CACHE_CONTROL, "no-cache, no-transform");
        response.setHeader("X-Accel-Buffering", "no");
        response.setHeader(AgentHeaders.REQUEST_ID, requestId);
    }

    /**
     * 重建发往 Python 的白名单请求头。
     *
     * <p>**安全关键点**：这里只写“网关解析出来的值”，绝不遍历客户端头，因此客户端伪造的
     * X-Agent-User / X-Agent-Roles / X-Agent-Secret 无法传到 Python（AGENTS §7.5 鉴权不裸奔）。
     *
     * @param request 当前请求
     * @param requestId 已解析的全链路追踪 ID（不要在本方法里再生成一份）
     * @return 上游请求头
     */
    private Map<String, String> buildUpstreamHeaders(HttpServletRequest request, String requestId)
    {
        Map<String, String> headers = new LinkedHashMap<String, String>();
        AgentUserContext context = (AgentUserContext) request.getAttribute(AgentUserContext.ATTRIBUTE);
        if (context != null)
        {
            if (context.getUserId() != null)
            {
                headers.put(AgentHeaders.USER, String.valueOf(context.getUserId()));
            }
            if (StringUtils.hasText(context.getUserName()))
            {
                headers.put(AgentHeaders.USERNAME, context.getUserName());
            }
            if (StringUtils.hasText(context.getRolesHeader()))
            {
                headers.put(AgentHeaders.ROLES, context.getRolesHeader());
            }
            if (context.getRegionId() != null)
            {
                headers.put(AgentHeaders.REGION, String.valueOf(context.getRegionId()));
            }
        }
        headers.put(AgentHeaders.REQUEST_ID, requestId);
        if (StringUtils.hasText(properties.getSecret()))
        {
            // 纵深防御：回环网络信任之外再加一层服务间密钥（Python 侧当前仅在回调/运维接口强制校验，
            // 携带并不产生副作用，但未来收紧 /agent/** 校验时网关无需再改）
            headers.put(AgentHeaders.SECRET, properties.getSecret());
        }
        String accept = request.getHeader(HttpHeaders.ACCEPT);
        if (StringUtils.hasText(accept))
        {
            headers.put(HttpHeaders.ACCEPT, accept);
        }
        String contentType = request.getHeader(HttpHeaders.CONTENT_TYPE);
        if (StringUtils.hasText(contentType))
        {
            headers.put(HttpHeaders.CONTENT_TYPE, contentType);
        }
        // SSE 断点续传游标必须原样透传，否则重连会重跑整轮（Python 侧 0-10 已支持）
        String lastEventId = request.getHeader(AgentHeaders.LAST_EVENT_ID);
        if (lastEventId != null)
        {
            headers.put(AgentHeaders.LAST_EVENT_ID, lastEventId);
        }
        return headers;
    }

    /**
     * 判断是否回调路径。
     *
     * <p>为什么同时匹配“前缀本身”与“前缀 + /”：过滤器是按 {@code /agent/callback/*} 前缀映射注册的，
     * 不覆盖 {@code /agent/callback}（无尾斜杠）这一种写法；若此处只判 startsWith(前缀 + "/")，
     * 这个畸形路径会绕过拦截器直接被转发给 Python（虽然 Python 也没这个路由，但“回调不经网关”
     * 这条约束不该留一个字符的口子）。
     *
     * @param path 去掉 contextPath 的请求路径
     * @return true 表示是回调路径
     */
    private boolean isCallbackPath(String path)
    {
        return path.equals(CALLBACK_PATH_PREFIX) || path.startsWith(CALLBACK_PATH_PREFIX + "/");
    }

    /**
     * 透传上游响应（4xx 也透传：它带的是明确业务语义，如 429 token 限额）。
     *
     * @param result 上游结果
     * @param requestId 网关 requestId（上游未回带时兜底）
     * @return 响应
     */
    private ResponseEntity<byte[]> passthrough(AgentUpstreamResult result, String requestId)
    {
        HttpStatus status = HttpStatus.resolve(result.getStatus());
        if (status == null)
        {
            return jsonResponse(HttpStatus.BAD_GATEWAY,
                    AjaxResult.error(HttpStatus.BAD_GATEWAY.value(), "上游返回了无法识别的状态码"));
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set(HttpHeaders.CONTENT_TYPE, StringUtils.hasText(result.getContentType()) ? result.getContentType()
                : MediaType.APPLICATION_JSON_VALUE);
        headers.set(AgentHeaders.REQUEST_ID,
                StringUtils.hasText(result.getRequestId()) ? result.getRequestId() : requestId);
        return new ResponseEntity<byte[]>(result.getBody(), headers, status);
    }

    /**
     * 统一 JSON 响应（RuoYi 信封），显式 UTF-8 避免中文乱码。
     *
     * @param status HTTP 状态码
     * @param body 信封
     * @return 响应
     */
    private ResponseEntity<byte[]> jsonResponse(HttpStatus status, AjaxResult body)
    {
        HttpHeaders headers = new HttpHeaders();
        headers.set(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE + ";charset=UTF-8");
        return new ResponseEntity<byte[]>(JSON.toJSONBytes(body), headers, status);
    }

    /**
     * 写降级帧：先 error 再 done，让前端既知道失败原因，也能正常收尾（不悬挂连接）。
     *
     * @param outputStream SSE 输出流
     * @param message 面向用户的中文提示
     * @param requestId 全链路追踪 ID（仅日志用）
     */
    private void writeDegradeFrames(OutputStream outputStream, String message, String requestId)
    {
        Map<String, Object> error = new LinkedHashMap<String, Object>();
        error.put("msg", message);
        error.put("code", HttpStatus.SERVICE_UNAVAILABLE.value());
        error.put("degrade", true);
        StringBuilder frames = new StringBuilder();
        frames.append("event: error\ndata: ").append(JSON.toJSONString(error)).append("\n\n");
        frames.append("event: done\ndata: {\"finish_reason\":\"degrade\"}\n\n");
        try
        {
            outputStream.write(frames.toString().getBytes(StandardCharsets.UTF_8));
            outputStream.flush();
        }
        catch (IOException e)
        {
            // 客户端已经走了，写不出去是正常现象，不能因此抛异常（异步任务抛异常会污染容器日志）
            log.info("降级帧写出失败（客户端可能已断开）requestId={} err={}", requestId, e.getClass().getSimpleName());
        }
    }

    /**
     * 读取请求体。
     *
     * <p>不用 @RequestBody：byte[] 需要与 Jackson 反序列化竞争消息转换器，而这里要的是**原样字节**
     * （透传不能改变上游收到的内容），直接读流最不容易出意外。
     *
     * @param request 当前请求
     * @return 请求体字节，读取失败时返回空数组
     */
    private byte[] readBody(HttpServletRequest request)
    {
        try
        {
            return StreamUtils.copyToByteArray(request.getInputStream());
        }
        catch (IOException e)
        {
            log.warn("读取请求体失败 path={} err={}", request.getRequestURI(), e.getClass().getSimpleName());
            return new byte[0];
        }
    }

    /**
     * requestId：attribute（过滤器已解析并净化）> 请求头（同样净化，防止过滤器未生效时直通）> 新生成。
     *
     * @param request 当前请求
     * @return requestId
     */
    private String resolveRequestId(HttpServletRequest request)
    {
        Object fromAttribute = request.getAttribute(AgentUserContext.REQUEST_ID_ATTRIBUTE);
        if (fromAttribute instanceof String && StringUtils.hasText((String) fromAttribute))
        {
            return (String) fromAttribute;
        }
        return AgentRequestId.resolve(request);
    }

    /**
     * 去掉 contextPath 后的请求路径。
     *
     * @param request 当前请求
     * @return 路径
     */
    private String resolvePath(HttpServletRequest request)
    {
        String uri = request.getRequestURI();
        String contextPath = request.getContextPath();
        if (StringUtils.hasText(contextPath) && uri.startsWith(contextPath))
        {
            return uri.substring(contextPath.length());
        }
        return uri;
    }
}
