package com.dkd.manage.controller;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import javax.servlet.http.HttpServletRequest;

import org.springframework.beans.factory.annotation.Autowired;
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

import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.annotation.Log;
import com.dkd.common.constant.HttpStatus;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentKbClient;
import com.dkd.manage.agent.AgentKbUpstreamException;
import com.dkd.manage.domain.dto.AgentKbDocumentDto;
import com.dkd.manage.domain.dto.AgentKbEnabledDto;
import com.dkd.manage.domain.dto.AgentKbSearchDto;

/**
 * 智能体知识库管理 Controller（排期 3-6，原型 S11）。
 *
 * <p>路径前缀 {@code /manage/agent/kb}；**五个接口全部是 Python {@code /agents/kb/**} 的代理**，
 * Java 不直接读写 {@code agent_kb_*} 表——知识库（文档/切片/向量/检索留痕）是智能体自有数据
 * （AGENTS §7.3），切片与向量化的唯一事实源在 Python；Java 只负责：鉴权、参数预校验、
 * 把 Python 的 RuoYi 信封 {code,msg,data} 拆出 data 返回、把失败重新组织成安全提示。
 *
 * <p><b>错误语义（对前端稳定）：</b>Python 不可达 → HTTP 503；上游返回非 2xx / 信封 code != 200
 * → HTTP 502。两者都只给明确中文提示，**不透传上游响应体**（AGENTS §6.1）。
 * 401/407 会明确提示"服务间密钥"，而不是"服务不可用"（按状态码归因，见
 * {@link AgentKbClient} 的已知陷阱说明）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@RestController
@RequestMapping("/manage/agent/kb")
public class AgentKbController extends BaseController
{
    /** 文档编码规则（与 Python kb_store.ingest_document 一致） */
    private static final Pattern DOC_CODE_PATTERN = Pattern.compile("^[a-z0-9][a-z0-9_\\-]{2,63}$");

    /**
     * 允许的文档分类（与 Python 契约一致）。Java 也校验一遍：非法分类在 Python 侧只是 400 信封，
     * 提前拦住能省一次跨进程调用，并让前端把错误标到 category 字段上。
     */
    private static final List<String> CATEGORIES = Arrays.asList("manual", "fault_code", "sop", "faq", "other");

    /** 检索词上限（与 Python 契约一致） */
    private static final int QUERY_MAX_LENGTH = 500;

    /** 检索条数范围 */
    private static final int TOP_K_MIN = 1;
    private static final int TOP_K_MAX = 20;
    private static final int TOP_K_DEFAULT = 5;

    /** 标题上限（与 Python 契约一致） */
    private static final int TITLE_MAX_LENGTH = 200;

    /** 关键词上限（Python GET /documents 的 keyword max_length=100，Java 提前拦，避免 502） */
    private static final int KEYWORD_MAX_LENGTH = 100;

    /** 分类缺省值（与 Python DocumentCreateRequest.category 默认 manual 一致） */
    private static final String DEFAULT_CATEGORY = "manual";

    /** 来源类型白名单（与 Python kb_store.SOURCE_TYPES 一致） */
    private static final List<String> SOURCE_TYPES = Arrays.asList("text", "file", "url");

    /** 来源引用上限（与 Python MAX_SOURCE_REF_CHARS 一致） */
    private static final int SOURCE_REF_MAX_LENGTH = 500;

    /**
     * 正文上限 **1MB 字节**（与 Python MAX_CONTENT_BYTES 一致）。
     *
     * <p>为什么按字节而不是按字符：中文 1 个字符约 3 字节，按字符校验会在 50 万汉字时放行
     * （≈1.5MB），Python 侧再拒——用户拿到的是笼统的 502（因为上游 400 的细节不透传）。
     * 按字节校验才能与 Python 完全同口径，错误在前端就落到 content 字段上。
     */
    private static final int CONTENT_MAX_BYTES = 1024 * 1024;

    /**
     * 列表单页上限。**与 Python 侧 pageSize le=100 对齐**：若 Java 放行 200，Python 会返回 422，
     * 我们只能给出笼统的 502；提前拦住才能告诉运营"每页最多 100 条"。
     */
    private static final int PAGE_SIZE_MAX = 100;

    @Autowired
    private AgentKbClient agentKbClient;

    /**
     * 文档列表。
     *
     * @param category 分类过滤（可空）
     * @param enabled  启用过滤 0/1（可空）
     * @param keyword  关键词（可空）
     * @param pageNum  页码（≥1；可空）
     * @param pageSize 每页条数（1~200；可空）
     * @param request  当前请求（取 requestId）
     * @return {@code data} 为 Python 的 {total,rows}
     */
    @PreAuthorize("@ss.hasPermi('manage:agentKb:list')")
    @GetMapping("/documents")
    public AjaxResult documents(@RequestParam(value = "category", required = false) String category,
            @RequestParam(value = "enabled", required = false) Integer enabled,
            @RequestParam(value = "keyword", required = false) String keyword,
            @RequestParam(value = "pageNum", required = false) Integer pageNum,
            @RequestParam(value = "pageSize", required = false) Integer pageSize, HttpServletRequest request)
    {
        Map<String, String> query = new LinkedHashMap<String, String>();
        putIfHasText(query, "category", category);
        if (enabled != null)
        {
            validateFlag("enabled", enabled.intValue());
            query.put("enabled", String.valueOf(enabled));
        }
        putIfHasText(query, "keyword", keyword);
        if (StringUtils.isNotEmpty(StringUtils.trim(keyword)) && StringUtils.trim(keyword).length() > KEYWORD_MAX_LENGTH)
        {
            throw field("keyword", "关键词最长 " + KEYWORD_MAX_LENGTH + " 字");
        }
        if (pageNum != null)
        {
            if (pageNum.intValue() < 1)
            {
                throw field("pageNum", "页码最小为 1");
            }
            query.put("pageNum", String.valueOf(pageNum));
        }
        if (pageSize != null)
        {
            if (pageSize.intValue() < 1 || pageSize.intValue() > PAGE_SIZE_MAX)
            {
                throw field("pageSize", "每页条数需在 1~" + PAGE_SIZE_MAX + " 之间");
            }
            query.put("pageSize", String.valueOf(pageSize));
        }
        return success(agentKbClient.listDocuments(query, AgentRequestId.resolve(request)));
    }

    /**
     * 文档入库（Python 侧切片 + 本地向量化 + 增量重建）。
     *
     * <p>requestId 通过 {@code X-Request-Id} 透传给 Python，便于把"这次入库"与两侧日志对上；
     * operator 强制取当前登录人（不信任请求体）。
     *
     * @param dto     入参
     * @param request 当前请求（取 requestId）
     * @return {@code data} 为 Python 的 {ingest}
     */
    @PreAuthorize("@ss.hasPermi('manage:agentKb:ingest')")
    @Log(title = "智能体知识库", businessType = BusinessType.INSERT)
    @PostMapping("/documents")
    public AjaxResult ingest(@RequestBody AgentKbDocumentDto dto, HttpServletRequest request)
    {
        validateDocument(dto);
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("docCode", dto.getDocCode().trim());
        payload.put("title", dto.getTitle().trim());
        payload.put("category", dto.getCategory().trim());
        // 可空字段**不传 null**：Python 侧 sourceType 是 `str`（默认 "text"），
        // 显式 null 会被 Pydantic 判 422——表现为"表单没填却报接口错误"。
        putTextIfPresent(payload, "sourceType", dto.getSourceType());
        putTextIfPresent(payload, "sourceRef", dto.getSourceRef());
        payload.put("content", dto.getContent());
        // 审计的操作者永远是当前登录人：请求体里的 operator 一律忽略（防伪造）
        payload.put("operator", getUsername());
        return success(agentKbClient.ingestDocument(payload, AgentRequestId.resolve(request)));
    }

    /**
     * 启用/停用文档。
     *
     * @param docCode 文档编码（路径参数，按契约预校验）
     * @param dto     入参（enabled 0/1）
     * @param request 当前请求（取 requestId）
     * @return {@code data} 为 Python 的 {docCode,enabled}
     */
    @PreAuthorize("@ss.hasPermi('manage:agentKb:edit')")
    @Log(title = "智能体知识库", businessType = BusinessType.UPDATE)
    @PutMapping("/documents/{docCode}/enabled")
    public AjaxResult updateEnabled(@PathVariable("docCode") String docCode,
            @RequestBody AgentKbEnabledDto dto, HttpServletRequest request)
    {
        String code = validateDocCode(docCode);
        if (dto == null || dto.getEnabled() == null)
        {
            throw field("enabled", "启用状态必填（0 或 1）");
        }
        int enabled = dto.getEnabled().intValue();
        validateFlag("enabled", enabled);
        return success(agentKbClient.setEnabled(code, enabled == 1, AgentRequestId.resolve(request)));
    }

    /**
     * 检索测试（会写 Python 侧 agent_kb_retrieval 留痕）。
     *
     * @param dto     入参（query/topK/scene/conversationId）
     * @param request 当前请求（取 requestId）
     * @return {@code data} 为 Python 的 hits/retrievalId/fallbackReason
     */
    @PreAuthorize("@ss.hasPermi('manage:agentKb:search')")
    @Log(title = "智能体知识库", businessType = BusinessType.OTHER)
    @PostMapping("/search")
    public AjaxResult search(@RequestBody AgentKbSearchDto dto, HttpServletRequest request)
    {
        if (dto == null)
        {
            throw field("query", "检索词必填");
        }
        String query = trimToNull(dto.getQuery());
        if (query == null)
        {
            throw field("query", "检索词必填");
        }
        if (query.length() > QUERY_MAX_LENGTH)
        {
            throw field("query", "检索词最长 " + QUERY_MAX_LENGTH + " 字");
        }
        int topK = dto.getTopK() == null ? TOP_K_DEFAULT : dto.getTopK().intValue();
        if (topK < TOP_K_MIN || topK > TOP_K_MAX)
        {
            throw field("topK", "返回条数需在 " + TOP_K_MIN + "~" + TOP_K_MAX + " 之间");
        }
        if (dto.getScene() != null && dto.getScene().intValue() < 1)
        {
            throw field("scene", "场景码必须 ≥1");
        }
        String requestId = AgentRequestId.resolve(request);
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("query", query);
        payload.put("topK", Integer.valueOf(topK));
        if (dto.getScene() != null)
        {
            payload.put("scene", dto.getScene());
        }
        // requestId 同时进 body 与头：Python 用它写检索留痕，Java 用它串日志（同一个值）
        payload.put("requestId", requestId);
        String conversationId = trimToNull(dto.getConversationId());
        if (conversationId != null)
        {
            payload.put("conversationId", conversationId);
        }
        return success(agentKbClient.search(payload, requestId));
    }

    /**
     * 知识库统计（文档数/切片数/检索次数）。
     *
     * @param request 当前请求（取 requestId）
     * @return {@code data} 为 Python 的统计对象
     */
    @PreAuthorize("@ss.hasPermi('manage:agentKb:list')")
    @GetMapping("/stats")
    public AjaxResult stats(HttpServletRequest request)
    {
        return success(agentKbClient.stats(AgentRequestId.resolve(request)));
    }

    // ------------------------------------------------------------------------------------
    // 参数校验（Controller 只做参数校验 + 调 Service/Client，不写业务）
    // ------------------------------------------------------------------------------------

    /**
     * 入库入参校验（与 Python 契约同口径；提前失败省一次跨进程调用）。
     *
     * @param dto 入参
     */
    private void validateDocument(AgentKbDocumentDto dto)
    {
        if (dto == null)
        {
            throw field("docCode", "请求体不能为空");
        }
        String docCode = validateDocCode(dto.getDocCode());
        dto.setDocCode(docCode);
        String title = trimToNull(dto.getTitle());
        if (title == null)
        {
            throw field("title", "标题必填");
        }
        if (title.length() > TITLE_MAX_LENGTH)
        {
            throw field("title", "标题最长 " + TITLE_MAX_LENGTH + " 字");
        }
        String category = trimToNull(dto.getCategory());
        if (category == null)
        {
            // 与 Python DocumentCreateRequest.category 默认值一致：不传按 manual 处理，
            // 而不是给运营一个"分类必填"的额外约束（两边口径不一致时以 Python 契约为准）
            category = DEFAULT_CATEGORY;
        }
        if (!CATEGORIES.contains(category))
        {
            throw field("category", "分类只能是 " + CATEGORIES);
        }
        dto.setCategory(category);
        String content = dto.getContent();
        if (StringUtils.isEmpty(content))
        {
            throw field("content", "正文不能为空");
        }
        if (content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > CONTENT_MAX_BYTES)
        {
            throw field("content", "正文过大（上限 1MB），请拆分文档后再入库");
        }
        String sourceType = trimToNull(dto.getSourceType());
        if (sourceType != null && !SOURCE_TYPES.contains(sourceType))
        {
            throw field("sourceType", "来源类型只能是 " + SOURCE_TYPES);
        }
        String sourceRef = trimToNull(dto.getSourceRef());
        if (sourceRef != null && sourceRef.length() > SOURCE_REF_MAX_LENGTH)
        {
            throw field("sourceRef", "来源引用最长 " + SOURCE_REF_MAX_LENGTH + " 字");
        }
    }

    /**
     * 文档编码校验（PUT 路径参数与入库共用）。
     *
     * @param docCode 原始编码
     * @return 去空白后的编码
     */
    private String validateDocCode(String docCode)
    {
        String code = trimToNull(docCode);
        if (code == null)
        {
            throw field("docCode", "文档编码必填");
        }
        if (!DOC_CODE_PATTERN.matcher(code).matches())
        {
            throw field("docCode", "文档编码格式不正确：需小写字母/数字开头，3~64 位小写字母/数字/下划线/短横");
        }
        return code;
    }

    private void validateFlag(String fieldName, int value)
    {
        if (value != 0 && value != 1)
        {
            throw field(fieldName, "启用状态只能是 0 或 1");
        }
    }

    private void putIfHasText(Map<String, String> target, String key, String value)
    {
        String trimmed = trimToNull(value);
        if (trimmed != null)
        {
            target.put(key, trimmed);
        }
    }

    /**
     * 对象 Map 版本（用于 JSON 请求体：可空字段**不要放 null 进去**）。
     *
     * <p>名字与 {@link #putIfHasText(Map, String, String)} 不同是必须的：两个重载在泛型擦除后
     * 都是 {@code (Map, String, String)}，同名会直接编译冲突。
     *
     * @param target 目标 payload
     * @param key    字段名
     * @param value  值
     */
    private void putTextIfPresent(Map<String, Object> target, String key, String value)
    {
        String trimmed = trimToNull(value);
        if (trimmed != null)
        {
            target.put(key, trimmed);
        }
    }

    private AgentFieldErrorException field(String fieldName, String message)
    {
        return new AgentFieldErrorException(fieldName, message);
    }

    private String trimToNull(String value)
    {
        return StringUtils.isEmpty(StringUtils.trim(value)) ? null : StringUtils.trim(value);
    }

    // ------------------------------------------------------------------------------------
    // 异常映射
    // ------------------------------------------------------------------------------------

    /**
     * 字段校验失败 → HTTP 200 + code 400 + data{field:msg}（与配置台/工具中心响应形状一致）。
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

    /**
     * 上游失败 → HTTP 503（不可达/超时）/ 502（有响应但不成功），消息由 Java 重新组织，
     * **不透传上游响应体**（AGENTS §6.1）。
     *
     * <p>为什么用真实 HTTP 状态而不是 HTTP 200+body code：这是"代理调用失败"（不是表单校验），
     * 前端三态里的"失败态"需要能走 axios 的 reject 分支并显示 msg；与网关降级语义保持一致。
     *
     * @param e 上游异常
     * @return 统一响应
     */
    @ExceptionHandler(AgentKbUpstreamException.class)
    public org.springframework.http.ResponseEntity<AjaxResult> handleUpstream(AgentKbUpstreamException e)
    {
        // 用 Spring 的 HttpStatus（项目内 com.dkd.common.constant.HttpStatus 只定义常用码）：
        // 不可达/超时=503，上游有响应但不成功=502
        int status = e.isTransportFailure() ? org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE.value()
                : org.springframework.http.HttpStatus.BAD_GATEWAY.value();
        AjaxResult body = AjaxResult.error(status, e.getMessage());
        return org.springframework.http.ResponseEntity.status(status).body(body);
    }
}
