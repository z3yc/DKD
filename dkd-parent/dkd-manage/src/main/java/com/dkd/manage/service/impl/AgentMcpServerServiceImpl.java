package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentMcpClient;
import com.dkd.manage.domain.AgentMcpServer;
import com.dkd.manage.domain.dto.AgentMcpServerDto;
import com.dkd.manage.domain.dto.AgentMcpServerEnabledDto;
import com.dkd.manage.domain.vo.AgentMcpServerVo;
import com.dkd.manage.mapper.AgentMcpServerMapper;
import com.dkd.manage.service.IAgentMcpServerService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * MCP server 配置 Service 实现（排期 3-7，S12）。
 *
 * <p>四条关键纪律（都在代码里落地）：
 * <ol>
 *   <li><b>只接只读</b>：{@code readOnlyOnly} 固定 1，传其它值直接字段错误——写工具由 Python
 *       接入层拒绝（AGENTS §7.9），Java 这边连"改这个开关"的口子都不开；</li>
 *   <li><b>只存环境变量名</b>：{@code envKeys} 是 {HTTP 头名: ENV_NAME}，值必须匹配
 *       {@code ^[A-Z][A-Z0-9_]{2,63}$}——把密钥值粘进来会被正则拒绝（AGENTS §7.1）；</li>
 *   <li><b>本期只支持 http</b>：{@code stdio} 需要本地进程管理与额外安全评审，明确拒绝而不是静默忽略；</li>
 *   <li><b>默认停用</b>：新增时 enabled 缺省 0，需人工安全复核后由启停接口打开。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentMcpServerServiceImpl implements IAgentMcpServerService
{
    private static final Logger log = LoggerFactory.getLogger(AgentMcpServerServiceImpl.class);

    /** JSON 读写（线程安全；与 AgentConfigServiceImpl/AgentKbClient 同一做法） */
    private static final ObjectMapper JSON = new ObjectMapper();

    /** server 编码规则（与 Python mcp_client 的 code 契约一致） */
    private static final Pattern CODE_PATTERN = Pattern.compile("^[a-z][a-z0-9_]{2,31}$");

    /** HTTP 头名（RFC 7230 token）：只允许 token 字符，避免换行/冒号注入请求头 */
    private static final Pattern HEADER_NAME_PATTERN = Pattern.compile("^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$");

    /** 环境变量名规则（只允许变量名，不可能塞进密钥值） */
    private static final Pattern ENV_NAME_PATTERN = Pattern.compile("^[A-Z][A-Z0-9_]{2,63}$");

    private static final Pattern ENDPOINT_PATTERN = Pattern.compile("^https?://\\S+$");

    private static final String TRANSPORT_HTTP = "http";
    private static final String TRANSPORT_STDIO = "stdio";

    private static final int NAME_MIN_LENGTH = 2;
    private static final int NAME_MAX_LENGTH = 50;
    private static final int ENDPOINT_MAX_LENGTH = 500;
    private static final int TIMEOUT_MS_MIN = 1000;
    private static final int TIMEOUT_MS_MAX = 5000;
    private static final int TIMEOUT_MS_DEFAULT = 5000;
    private static final int ROW_LIMIT_MIN = 1;
    private static final int ROW_LIMIT_MAX = 200;
    private static final int ROW_LIMIT_DEFAULT = 200;
    /** envKeys 条目上限：任何正常 server 都不会要十几个密钥头，多出来的更可能是误填 */
    private static final int ENV_KEYS_MAX_ENTRIES = 10;
    private static final int FLAG_YES = 1;
    private static final int FLAG_NO = 0;
    private static final int READ_ONLY_ONLY = 1;
    private static final String STATUS_UNKNOWN = "unknown";

    @Autowired
    private AgentMcpServerMapper agentMcpServerMapper;

    @Autowired
    private AgentMcpClient agentMcpClient;

    @Override
    public List<AgentMcpServerVo> selectServerList()
    {
        List<AgentMcpServer> servers = agentMcpServerMapper.selectAgentMcpServerList(new AgentMcpServer());
        List<AgentMcpServerVo> result = new ArrayList<AgentMcpServerVo>();
        if (servers != null)
        {
            for (AgentMcpServer server : servers)
            {
                result.add(toVo(server));
            }
        }
        return result;
    }

    @Override
    public AgentMcpServerVo selectServerById(Long id)
    {
        return toVo(loadOrThrow(id));
    }

    @Override
    @Transactional
    public AgentMcpServerVo insertServer(AgentMcpServerDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentMcpServer server = buildServer(dto, null, operator);
        int rows = agentMcpServerMapper.insertAgentMcpServer(server);
        if (rows != 1)
        {
            throw new ServiceException("新增 MCP server 失败：影响行数 " + rows);
        }
        return toVo(agentMcpServerMapper.selectAgentMcpServerById(server.getId()));
    }

    @Override
    @Transactional
    public AgentMcpServerVo updateServer(AgentMcpServerDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        if (dto.getId() == null)
        {
            throw new ServiceException("MCP server ID 不能为空");
        }
        AgentMcpServer current = loadOrThrow(dto.getId());
        AgentMcpServer server = buildServer(dto, current, operator);
        int rows = agentMcpServerMapper.updateAgentMcpServer(server);
        if (rows != 1)
        {
            throw new ServiceException("更新 MCP server 失败：记录可能已被删除（id=" + dto.getId() + "）");
        }
        return toVo(agentMcpServerMapper.selectAgentMcpServerById(dto.getId()));
    }

    @Override
    public AgentMcpServerVo updateEnabled(Long id, AgentMcpServerEnabledDto dto, String operator)
    {
        if (id == null)
        {
            throw new ServiceException("MCP server ID 不能为空");
        }
        if (dto == null || dto.getEnabled() == null)
        {
            throw field("enabled", "启用状态必填（0 或 1）");
        }
        AgentMcpServer current = loadOrThrow(id);
        int enabled = dto.getEnabled().intValue();
        validateFlag("enabled", enabled);
        int rows = agentMcpServerMapper.updateAgentMcpServerEnabled(id, Integer.valueOf(enabled), operator,
                new Date());
        if (rows != 1)
        {
            throw new ServiceException("更新启用状态失败：记录可能已被删除（id=" + current.getId() + "）");
        }
        return toVo(agentMcpServerMapper.selectAgentMcpServerById(id));
    }

    @Override
    public Object probe(Long id, String requestId)
    {
        // Java 侧不因 enabled=0 拦截探测（探测本身是无副作用的只读握手，先探测再启用更安全）；
        // 但注意 Python 侧 load_server_by_code 只加载"启用且未删除"的配置，未启用时其 probe 会 404，
        // 客户端已把 404 翻成"请先启用后再探测/同步"的明确提示。
        // 跨端流程（是否允许探测停用配置）待 Lead 拍板，见本次报告「未验/风险」。
        AgentMcpServer server = loadOrThrow(id);
        return agentMcpClient.probe(server.getCode(), requestId);
    }

    @Override
    public Object sync(Long id, String requestId)
    {
        AgentMcpServer server = loadOrThrow(id);
        return agentMcpClient.sync(server.getCode(), requestId);
    }

    @Override
    public Object tools(Long id, String requestId)
    {
        AgentMcpServer server = loadOrThrow(id);
        return agentMcpClient.tools(server.getCode(), requestId);
    }

    // ------------------------------------------------------------------------------------
    // 校验 + 组装
    // ------------------------------------------------------------------------------------

    /**
     * 校验并组装待落库配置（新增/编辑共用）。
     *
     * @param dto      入参
     * @param current  库内当前行（新增为 null）
     * @param operator 操作者
     * @return 待落库实体
     */
    private AgentMcpServer buildServer(AgentMcpServerDto dto, AgentMcpServer current, String operator)
    {
        boolean create = current == null;
        AgentMcpServer server = new AgentMcpServer();
        if (create)
        {
            String code = trimToNull(dto.getCode());
            if (code == null)
            {
                throw field("code", "server 编码必填");
            }
            if (!CODE_PATTERN.matcher(code).matches())
            {
                throw field("code", "server 编码格式不正确：需小写字母开头，3~32 位小写字母/数字/下划线");
            }
            if (agentMcpServerMapper.countAgentMcpServerByCode(code, null) > 0)
            {
                throw field("code", "server 编码已存在：" + code);
            }
            server.setCode(code);
            server.setStatus(STATUS_UNKNOWN);
        }
        else
        {
            server.setId(current.getId());
            server.setCode(current.getCode());
            // 编码是配置身份（Python 按 code 调 probe/sync），编辑只读；SQL 也不 set code
            String submittedCode = trimToNull(dto.getCode());
            if (submittedCode != null && !submittedCode.equals(current.getCode()))
            {
                throw field("code", "server 编码不可修改：库内为 " + current.getCode());
            }
            server.setStatus(current.getStatus());
        }

        String name = requiredText(mergeText(dto.getName(), current == null ? null : current.getName()), "name",
                "名称必填");
        if (name.length() < NAME_MIN_LENGTH || name.length() > NAME_MAX_LENGTH)
        {
            throw field("name", "名称需 " + NAME_MIN_LENGTH + "~" + NAME_MAX_LENGTH + " 字");
        }
        server.setName(name);

        String transport = mergeText(dto.getTransport(), current == null ? null : current.getTransport());
        if (StringUtils.isEmpty(transport))
        {
            transport = TRANSPORT_HTTP;
        }
        if (TRANSPORT_STDIO.equals(transport))
        {
            // 明确拒绝并说明原因，而不是静默改回 http：用户需要知道"stdio 本期不支持"
            throw field("transport", "本期不支持 stdio transport：stdio 需要本地进程管理与额外安全评审，当前仅支持 http");
        }
        if (!TRANSPORT_HTTP.equals(transport))
        {
            throw field("transport", "未知 transport：" + transport + "（本期仅支持 http）");
        }
        server.setTransport(transport);

        String endpoint = requiredText(
                mergeText(dto.getEndpoint(), current == null ? null : current.getEndpoint()), "endpoint",
                "endpoint 必填");
        if (endpoint.length() > ENDPOINT_MAX_LENGTH)
        {
            throw field("endpoint", "endpoint 最长 " + ENDPOINT_MAX_LENGTH + " 个字符");
        }
        if (!ENDPOINT_PATTERN.matcher(endpoint).matches())
        {
            throw field("endpoint", "endpoint 必须是 http:// 或 https:// 开头的地址");
        }
        server.setEndpoint(endpoint);

        int enabled = dto.getEnabled() != null ? dto.getEnabled().intValue()
                : (current == null ? FLAG_NO : intValue(current.getEnabled()));
        validateFlag("enabled", enabled);
        server.setEnabled(Integer.valueOf(enabled));

        int timeoutMs = dto.getTimeoutMs() != null ? dto.getTimeoutMs().intValue()
                : (current == null ? TIMEOUT_MS_DEFAULT : intValue(current.getTimeoutMs()));
        if (timeoutMs < TIMEOUT_MS_MIN || timeoutMs > TIMEOUT_MS_MAX)
        {
            throw field("timeoutMs",
                    "超时需在 " + TIMEOUT_MS_MIN + "~" + TIMEOUT_MS_MAX + " 毫秒之间（当前 " + timeoutMs + "）");
        }
        server.setTimeoutMs(Integer.valueOf(timeoutMs));

        int rowLimit = dto.getRowLimit() != null ? dto.getRowLimit().intValue()
                : (current == null ? ROW_LIMIT_DEFAULT : intValue(current.getRowLimit()));
        if (rowLimit < ROW_LIMIT_MIN || rowLimit > ROW_LIMIT_MAX)
        {
            throw field("rowLimit", "行数上限需在 " + ROW_LIMIT_MIN + "~" + ROW_LIMIT_MAX + " 之间（当前 " + rowLimit
                    + "）");
        }
        server.setRowLimit(Integer.valueOf(rowLimit));

        if (dto.getReadOnlyOnly() != null && dto.getReadOnlyOnly().intValue() != READ_ONLY_ONLY)
        {
            // 红线：允许改这个开关等于允许接入写工具，绕过 AGENTS §7.9 的人工确认
            throw field("readOnlyOnly", "只读限制固定为 1（写工具一律不接入）");
        }
        server.setReadOnlyOnly(Integer.valueOf(READ_ONLY_ONLY));

        if (create)
        {
            server.setEnvKeysJson(serializeEnvKeys(dto.getEnvKeys(), null));
        }
        else if (dto.getEnvKeys() == null)
        {
            // 编辑不传 envKeys = 不修改；传空对象 = 清空
            server.setEnvKeysJson(current.getEnvKeysJson());
        }
        else
        {
            server.setEnvKeysJson(serializeEnvKeys(dto.getEnvKeys(), current.getEnvKeysJson()));
        }

        Date now = new Date();
        server.setUpdateBy(operator);
        server.setUpdateTime(now);
        if (create)
        {
            server.setCreateBy(operator);
            server.setCreateTime(now);
            server.setDelFlag("0");
        }
        return server;
    }

    /**
     * 校验并序列化 envKeys（{HTTP 头名: 环境变量名}）。
     *
     * @param envKeys       前端传入的映射（null = 不修改，由调用方处理）
     * @param currentJson   当前库内 JSON（日志用，仅当解析失败时提示；不参与计算）
     * @return JSON 字符串（空映射返回 null）
     */
    private String serializeEnvKeys(Map<String, String> envKeys, String currentJson)
    {
        if (envKeys == null || envKeys.isEmpty())
        {
            return null;
        }
        if (envKeys.size() > ENV_KEYS_MAX_ENTRIES)
        {
            throw field("envKeys", "环境变量映射最多 " + ENV_KEYS_MAX_ENTRIES + " 项（当前 " + envKeys.size() + "）");
        }
        Map<String, String> normalized = new LinkedHashMap<String, String>();
        Set<String> lowerCaseHeaders = new LinkedHashSet<String>();
        for (Map.Entry<String, String> entry : envKeys.entrySet())
        {
            String header = trimToNull(entry.getKey());
            String envName = trimToNull(entry.getValue());
            if (header == null)
            {
                throw field("envKeys", "envKeys 的 HTTP 头名不能为空");
            }
            if (!HEADER_NAME_PATTERN.matcher(header).matches())
            {
                throw field("envKeys", "envKeys 的 HTTP 头名不合法：" + header + "（只允许字母/数字/!#$%&'*+.^_`|~-）");
            }
            String lower = header.toLowerCase();
            if (lowerCaseHeaders.contains(lower))
            {
                // 头名大小写不敏感：同时出现 X-Api-Key / x-api-key 会让"用哪个"变得不可预期
                throw field("envKeys", "envKeys 的 HTTP 头名重复（大小写不敏感）：" + header);
            }
            lowerCaseHeaders.add(lower);
            if (envName == null)
            {
                throw field("envKeys", "envKeys 中头 " + header + " 对应的环境变量名不能为空");
            }
            if (!ENV_NAME_PATTERN.matcher(envName).matches())
            {
                // 提示里强调"只填变量名"：否则运营会把真实密钥粘进来（那就直接落库了！）
                throw field("envKeys",
                        "环境变量名格式不正确：" + envName + "（需大写字母开头，3~64 位大写字母/数字/下划线，只填变量名不要填密钥值）");
            }
            normalized.put(header, envName);
        }
        try
        {
            return JSON.writeValueAsString(normalized);
        }
        catch (com.fasterxml.jackson.core.JsonProcessingException e)
        {
            // 理论上不会发生（map 只有字符串）；发生了说明环境异常，明确报错而不是写 null
            log.warn("envKeys 序列化失败 err={} currentJsonLength={}", e.getClass().getSimpleName(),
                    currentJson == null ? 0 : currentJson.length());
            throw new ServiceException("环境变量映射保存失败，请重试");
        }
    }

    /**
     * 实体 → 出参：把 env_keys_json 解析成变量名映射（损坏时给空对象 + WARN，不让列表 500）。
     *
     * @param server 实体
     * @return 出参
     */
    private AgentMcpServerVo toVo(AgentMcpServer server)
    {
        AgentMcpServerVo vo = new AgentMcpServerVo();
        vo.setId(server.getId());
        vo.setCode(server.getCode());
        vo.setName(server.getName());
        vo.setTransport(server.getTransport());
        vo.setEndpoint(server.getEndpoint());
        vo.setEnvKeys(parseEnvKeys(server.getEnvKeysJson(), server.getCode()));
        vo.setEnabled(server.getEnabled());
        vo.setTimeoutMs(server.getTimeoutMs());
        vo.setRowLimit(server.getRowLimit());
        vo.setReadOnlyOnly(server.getReadOnlyOnly());
        vo.setStatus(server.getStatus());
        vo.setLastProbeTime(server.getLastProbeTime());
        vo.setLastProbeMsg(server.getLastProbeMsg());
        vo.setCreateBy(server.getCreateBy());
        vo.setCreateTime(server.getCreateTime());
        vo.setUpdateBy(server.getUpdateBy());
        vo.setUpdateTime(server.getUpdateTime());
        return vo;
    }

    /**
     * 解析 env_keys_json。
     *
     * @param json        JSON 文本
     * @param serverCode  编码（日志用，不打印 JSON 内容）
     * @return 变量名映射（空/损坏 → 空对象）
     */
    private Map<String, String> parseEnvKeys(String json, String serverCode)
    {
        Map<String, String> result = new LinkedHashMap<String, String>();
        if (StringUtils.isEmpty(json))
        {
            return result;
        }
        try
        {
            Map<String, String> parsed = JSON.readValue(json, new TypeReference<LinkedHashMap<String, String>>()
            {
            });
            if (parsed != null)
            {
                result.putAll(parsed);
            }
        }
        catch (Exception e)
        {
            // 历史脏数据不能让列表 500：返回空映射 + WARN（页面会显示"未配置"，可编辑修正）
            log.warn("env_keys_json 解析失败，按未配置处理 serverCode={} err={}", serverCode,
                    e.getClass().getSimpleName());
        }
        return result;
    }

    /**
     * 取配置；不存在抛业务错误。
     *
     * @param id 主键
     * @return 实体
     */
    private AgentMcpServer loadOrThrow(Long id)
    {
        if (id == null)
        {
            throw new ServiceException("MCP server ID 不能为空");
        }
        AgentMcpServer server = agentMcpServerMapper.selectAgentMcpServerById(id);
        if (server == null)
        {
            throw new ServiceException("MCP server 不存在：" + id);
        }
        return server;
    }

    private void validateFlag(String fieldName, int value)
    {
        if (value != FLAG_YES && value != FLAG_NO)
        {
            throw field(fieldName, "启用状态只能是 0 或 1");
        }
    }

    private AgentFieldErrorException field(String fieldName, String message)
    {
        return new AgentFieldErrorException(fieldName, message);
    }

    private String requiredText(String value, String fieldName, String message)
    {
        if (StringUtils.isEmpty(value))
        {
            throw field(fieldName, message);
        }
        return value;
    }

    private String mergeText(String dtoValue, String currentValue)
    {
        return trimToNull(dtoValue == null ? currentValue : dtoValue);
    }

    private int intValue(Integer value)
    {
        return value == null ? 0 : value.intValue();
    }

    private String trimToNull(String value)
    {
        return StringUtils.isEmpty(StringUtils.trim(value)) ? null : StringUtils.trim(value);
    }
}
