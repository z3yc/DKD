package com.dkd.manage.service.impl;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.DateUtils;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentConfigConflictException;
import com.dkd.manage.agent.AgentConfigDefaults;
import com.dkd.manage.agent.AgentConfigReloadNotifier;
import com.dkd.manage.agent.AgentConfigValidationException;
import com.dkd.manage.domain.AgentConfig;
import com.dkd.manage.domain.AgentConfigHistory;
import com.dkd.manage.domain.dto.AgentConfigDto;
import com.dkd.manage.domain.dto.AgentConfigEnabledDto;
import com.dkd.manage.domain.dto.AgentConfigRollbackDto;
import com.dkd.manage.domain.vo.AgentConfigVo;
import com.dkd.manage.mapper.AgentConfigMapper;
import com.dkd.manage.service.IAgentConfigService;

/**
 * 智能体配置 Service 实现（排期 2-2，契约 §4）。
 *
 * <p>三条核心设计（改动前先读契约，别按通用 CRUD 直觉改）：
 * <ol>
 *   <li><b>版本是写操作的骨架</b>：新增写 v1；编辑/启停/回滚都是「CAS 更新 → version+1 →
 *       同事务写 agent_config_history → 提交后异步通知 Python」。CAS 影响 0 行一律转 409，
 *       不允许「先查后改」的乐观锁（那只是把竞态窗口缩小，不是消除）；</li>
 *   <li><b>回滚 = 复制历史为新版本</b>，不是原地回退：版本序列必须单调递增，
 *       否则无法回答「当前配置是哪一次保存」（契约 §3.2）；</li>
 *   <li><b>校验失败必须有字段名</b>：前端要按字段标红；全部规则集中在
 *       {@link #validateAndBuild(AgentConfigDto, AgentConfig, boolean, String)} 的拆分方法里，
 *       阈值常量来自 {@link AgentConfigDefaults}（与 Python agent_defaults.py 对齐）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-03
 */
@Service
public class AgentConfigServiceImpl implements IAgentConfigService
{
    private static final Logger log = LoggerFactory.getLogger(AgentConfigServiceImpl.class);

    /** JSON 读写（线程安全；本类只做简单对象/文本互转，不需要 Spring 的 ObjectMapper 定制） */
    private static final ObjectMapper JSON = new ObjectMapper();

    /** 提示词占位符：{name} 形式；`{{`/`}}` 是提示词里的转义花括号，不会命中本正则 */
    private static final Pattern PLACEHOLDER_PATTERN = Pattern.compile("\\{([A-Za-z0-9_]+)\\}");

    /** 智能体编码规则（与 Python CODE_PATTERN 一致） */
    private static final Pattern CODE_PATTERN = Pattern.compile(AgentConfigDefaults.CODE_PATTERN);

    private static final int NAME_MIN_LENGTH = 2;

    private static final int NAME_MAX_LENGTH = 30;

    private static final int DESCRIPTION_MAX_LENGTH = 200;

    private static final int DISABLED_REASON_MIN_LENGTH = 5;

    private static final int DISABLED_REASON_MAX_LENGTH = 100;

    private static final int CHANGE_NOTE_MAX_LENGTH = 200;

    private static final int MAX_TOKENS_MIN = 256;

    private static final int MAX_TOKENS_MAX = 8192;

    private static final int TIMEOUT_MIN_S = 5;

    private static final int TIMEOUT_MAX_S = 300;

    private static final int RETRIES_MIN = 0;

    private static final int RETRIES_MAX = 5;

    private static final int TEMPERATURE_MIN = 0;

    private static final int TEMPERATURE_MAX = 2;

    private static final int ENABLED_YES = 1;

    private static final int ENABLED_NO = 0;

    private static final String CHANGE_NOTE_ON_CREATE = "初始版本";

    private static final String CHANGE_NOTE_ON_EDIT = "编辑保存";

    @Autowired
    private AgentConfigMapper agentConfigMapper;

    @Autowired
    private AgentConfigReloadNotifier reloadNotifier;

    @Override
    public List<AgentConfigVo> selectAgentConfigList(AgentConfig query)
    {
        AgentConfig condition = query == null ? new AgentConfig() : query;
        List<AgentConfig> rows = agentConfigMapper.selectAgentConfigList(condition);
        List<AgentConfigVo> result = new ArrayList<AgentConfigVo>(rows == null ? 0 : rows.size());
        if (rows == null)
        {
            return result;
        }
        for (AgentConfig row : rows)
        {
            result.add(toVo(row));
        }
        return result;
    }

    @Override
    public AgentConfigVo selectAgentConfigById(Long id)
    {
        return toVo(loadOrThrow(id));
    }

    @Override
    public List<AgentConfigHistory> selectHistoryByConfigId(Long id)
    {
        AgentConfig current = loadOrThrow(id);
        List<AgentConfigHistory> history = agentConfigMapper.selectAgentConfigHistoryByCode(current.getCode());
        return history == null ? new ArrayList<AgentConfigHistory>() : history;
    }

    @Override
    public Map<String, Object> selectMeta(String runtime)
    {
        String resolved = resolveMetaRuntime(runtime);
        Map<String, Object> meta = new LinkedHashMap<String, Object>();
        meta.put("runtimes", AgentConfigDefaults.runtimeDescriptors());
        meta.put("models", AgentConfigDefaults.MODEL_WHITELIST);
        meta.put("tools", AgentConfigDefaults.TOOL_WHITELIST_BY_RUNTIME.get(resolved));
        meta.put("placeholders", AgentConfigDefaults.ALLOWED_PLACEHOLDERS.get(resolved));
        // fault 没有提示词 → null；前端据 null 禁用「恢复默认」（不编造提示词，契约 §1 有意偏差）
        meta.put("defaultPrompt", AgentConfigDefaults.defaultPrompt(resolved));
        meta.put("defaultParams", AgentConfigDefaults.defaultParams(resolved));
        meta.put("paramSpecs", AgentConfigDefaults.paramSpecs(resolved));
        meta.put("sceneBase", Integer.valueOf(AgentConfigDefaults.CUSTOM_SCENE_BASE));
        return meta;
    }

    @Override
    @Transactional
    public AgentConfigVo insertAgentConfig(AgentConfigDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentConfig config = validateAndBuild(dto, null, true, operator);
        int rows = agentConfigMapper.insertAgentConfig(config);
        if (rows != 1)
        {
            // 为什么不是 toAjax 的 0 行：走到这里说明 DB 行为异常（连接/触发器问题），
            // 静默返回「操作失败」会把「配置没保存上」伪装成校验问题
            throw new ServiceException("新增配置失败：影响行数 " + rows);
        }
        String changeNote = changeNoteOrDefault(config.getChangeNote(), CHANGE_NOTE_ON_CREATE);
        return finishSave(config, Long.valueOf(0L), changeNote, operator);
    }

    @Override
    @Transactional
    public AgentConfigVo updateAgentConfig(AgentConfigDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        if (dto.getId() == null)
        {
            throw new ServiceException("配置ID不能为空");
        }
        if (dto.getVersion() == null)
        {
            // 契约 §4.3：编辑时必带 version。缺失时不能默认「用库内版本」——
            // 那等于关掉乐观锁（两个人同时打开表单就会后写覆盖先写）
            throw field("version", "版本号必填（用于乐观锁）");
        }
        AgentConfig current = loadOrThrow(dto.getId());
        AgentConfig config = validateAndBuild(dto, current, false, operator);
        int rows = agentConfigMapper.updateAgentConfig(config);
        if (rows == 0)
        {
            throw conflictAfterFailedCas(dto.getId(), dto.getVersion());
        }
        String changeNote = changeNoteOrDefault(config.getChangeNote(), CHANGE_NOTE_ON_EDIT);
        return finishSave(config, dto.getVersion(), changeNote, operator);
    }

    @Override
    @Transactional
    public AgentConfigVo updateEnabled(Long id, AgentConfigEnabledDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentConfig current = loadOrThrow(id);
        int enabled = resolveEnabledValue(dto.getEnabled());
        // 前端带 version 时严格按 CAS；漏传时用库内当前版本（只能防住读取后的竞态，前端应始终传）
        long submitted = dto.getVersion() == null ? current.getVersion().longValue() : dto.getVersion().longValue();
        AgentConfig config = copyForEnabledUpdate(current, enabled, dto.getDisabledReason(), dto.getChangeNote(), operator,
                submitted);
        int rows = agentConfigMapper.updateAgentConfig(config);
        if (rows == 0)
        {
            throw conflictAfterFailedCas(id, Long.valueOf(submitted));
        }
        String defaultNote = enabled == ENABLED_YES ? "启用" : "停用";
        String changeNote = changeNoteOrDefault(config.getChangeNote(), defaultNote);
        return finishSave(config, Long.valueOf(submitted), changeNote, operator);
    }

    @Override
    @Transactional
    public AgentConfigVo rollback(Long id, AgentConfigRollbackDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        if (dto.getVersion() == null)
        {
            throw field("version", "版本号必填（用于乐观锁）");
        }
        if (dto.getTargetVersion() == null)
        {
            throw field("targetVersion", "回滚目标版本必填");
        }
        AgentConfig current = loadOrThrow(id);
        AgentConfigHistory history = agentConfigMapper.selectAgentConfigHistoryByCodeAndVersion(current.getCode(),
                dto.getTargetVersion());
        if (history == null)
        {
            throw new ServiceException("历史版本 v" + dto.getTargetVersion() + " 不存在");
        }
        AgentConfig restored = parseSnapshot(history.getSnapshot());
        // 快照来自同一 code（查询条件保证），但 runtime 若被历史脏数据改过就不能套回当前行
        if (!current.getRuntime().equals(restored.getRuntime()))
        {
            throw new ServiceException("历史快照的运行载体与当前配置不一致，无法回滚");
        }
        if (Integer.valueOf(ENABLED_NO).equals(restored.getEnabled())
                && StringUtils.isBlank(restored.getDisabledReason()))
        {
            throw new ServiceException("历史快照的停用原因为空，无法回滚");
        }
        AgentConfig config = copyForRollback(current, restored, dto.getVersion(), operator);
        String changeNote = changeNoteOrDefault(config.getChangeNote(), "回滚至 v" + dto.getTargetVersion());
        config.setChangeNote(changeNote);
        int rows = agentConfigMapper.updateAgentConfig(config);
        if (rows == 0)
        {
            throw conflictAfterFailedCas(id, dto.getVersion());
        }
        return finishSave(config, dto.getVersion(), changeNote, operator);
    }

    // ------------------------------------------------------------------------------------
    // 校验 + 组装
    // ------------------------------------------------------------------------------------

    /**
     * 统一校验入口：所有写路径（新增/编辑/启停/回滚）都必须经过这里或它的拆分方法。
     *
     * @param dto      入参
     * @param current  库内当前行（新增为 null）
     * @param create   true=新增，false=编辑
     * @param operator 操作者
     * @return 待落库实体（新增已分配 version=1/scene/builtin；编辑已带 CAS 用的 version）
     */
    private AgentConfig validateAndBuild(AgentConfigDto dto, AgentConfig current, boolean create, String operator)
    {
        AgentConfig config = new AgentConfig();
        fillIdentity(config, dto, current, create);
        fillContent(config, dto, current, create);
        fillAudit(config, dto, current, create, operator);
        return config;
    }

    /**
     * 身份列：code/runtime/scene/builtin。新增时校验并分配；编辑时只读（改了直接报字段错误）。
     */
    private void fillIdentity(AgentConfig config, AgentConfigDto dto, AgentConfig current, boolean create)
    {
        if (create)
        {
            String code = trimToNull(dto.getCode());
            if (code == null)
            {
                throw field("code", "编码必填");
            }
            if (!CODE_PATTERN.matcher(code).matches())
            {
                throw field("code", "编码格式不正确：需小写字母开头，3~32 位小写字母/数字/下划线");
            }
            // DB 有 UK，这里提前查是为了返回字段级错误而不是 DuplicateKey 的 500
            if (agentConfigMapper.countAgentConfigByCode(code) > 0)
            {
                throw field("code", "编码已存在：" + code);
            }
            String runtime = trimToNull(dto.getRuntime());
            if (runtime == null)
            {
                throw field("runtime", "运行载体必填");
            }
            if (!AgentConfigDefaults.isRuntime(runtime))
            {
                throw field("runtime", "未知运行载体：" + runtime);
            }
            if (!AgentConfigDefaults.isCreatable(runtime))
            {
                // Q1：首期只放开 chat 载体；restock/fault 只有内置行
                throw field("runtime", "本期只允许新增 chat 载体的智能体");
            }
            if (dto.getBuiltin() != null && dto.getBuiltin().intValue() != 0)
            {
                throw field("builtin", "不允许创建内置智能体");
            }
            config.setCode(code);
            config.setRuntime(runtime);
            config.setBuiltin(Integer.valueOf(0));
            config.setScene(nextScene());
            return;
        }

        // 编辑：三列只读。传 null 视为「前端没带」，与原值不同即拒绝——静默忽略会把前端 bug 藏起来
        if (dto.getCode() != null && !dto.getCode().equals(current.getCode()))
        {
            throw field("code", "编码不允许修改");
        }
        if (dto.getRuntime() != null && !dto.getRuntime().equals(current.getRuntime()))
        {
            throw field("runtime", "运行载体不允许修改");
        }
        if (dto.getBuiltin() != null && !dto.getBuiltin().equals(current.getBuiltin()))
        {
            throw field("builtin", "是否内置不允许修改");
        }
        config.setCode(current.getCode());
        config.setRuntime(current.getRuntime());
        config.setBuiltin(current.getBuiltin());
        config.setScene(current.getScene());
    }

    /**
     * 可编辑内容列：名称/描述/提示词/工具/模型/采样参数/超时/重试/业务参数/启停/变更说明。
     */
    private void fillContent(AgentConfig config, AgentConfigDto dto, AgentConfig current, boolean create)
    {
        String name = trimToNull(dto.getName());
        if (name == null)
        {
            throw field("name", "名称必填");
        }
        if (name.length() < NAME_MIN_LENGTH || name.length() > NAME_MAX_LENGTH)
        {
            throw field("name", "名称长度需为 2~30 字，当前 " + name.length() + " 字");
        }
        requireNameUnique(name, current == null ? null : current.getId());
        config.setName(name);

        String description = trimToNull(dto.getDescription());
        if (description == null)
        {
            throw field("description", "描述必填");
        }
        if (description.length() > DESCRIPTION_MAX_LENGTH)
        {
            throw field("description", "描述不能超过 200 字，当前 " + description.length() + " 字");
        }
        config.setDescription(description);

        String runtime = config.getRuntime();
        config.setSystemPrompt(validateSystemPrompt(runtime, dto.getSystemPrompt()));
        config.setTools(validateTools(runtime, dto.getTools()));
        config.setModel(validateModel(dto.getModel()));
        config.setTemperature(validateTemperature(dto.getTemperature()));
        config.setMaxTokens(validateMaxTokens(dto.getMaxTokens()));
        config.setTimeoutS(resolveTimeoutS(dto.getTimeoutS(), current, create));
        config.setMaxRetries(resolveMaxRetries(dto.getMaxRetries(), current, create));
        config.setParamsJson(validateParams(runtime, dto.getParamsJson()));
        int enabled = resolveEnabled(dto.getEnabled(), current, create);
        config.setEnabled(Integer.valueOf(enabled));
        config.setDisabledReason(validateDisabledReason(enabled, dto.getDisabledReason()));
        config.setChangeNote(validateChangeNote(dto.getChangeNote()));
    }

    /**
     * 审计/版本列：编辑用提交的 version 做 CAS（写入前不回填新版本，由 finishSave 统一 +1）。
     */
    private void fillAudit(AgentConfig config, AgentConfigDto dto, AgentConfig current, boolean create, String operator)
    {
        Date now = DateUtils.getNowDate();
        config.setUpdateBy(operator);
        config.setUpdateTime(now);
        if (create)
        {
            config.setVersion(Long.valueOf(1L));
            config.setCreateBy(operator);
            config.setCreateTime(now);
            config.setDelFlag("0");
            return;
        }
        config.setVersion(dto.getVersion());
        config.setCreateBy(current.getCreateBy());
        config.setCreateTime(current.getCreateTime());
        config.setDelFlag(current.getDelFlag());
    }

    // ------------------------------------------------------------------------------------
    // 单字段校验
    // ------------------------------------------------------------------------------------

    private String validateSystemPrompt(String runtime, String raw)
    {
        if (AgentConfigDefaults.RUNTIME_FAULT.equals(runtime))
        {
            // 契约 §1 有意偏差：fault 不调 LLM（结论由 Python 纯规则合成），
            // 强制必填只会造出「配了但不生效」的字段 → 忽略输入并置 null
            return null;
        }
        if (StringUtils.isBlank(raw))
        {
            throw field("systemPrompt", "系统提示词必填");
        }
        if (raw.length() < AgentConfigDefaults.PROMPT_MIN_LENGTH
                || raw.length() > AgentConfigDefaults.PROMPT_MAX_LENGTH)
        {
            throw field("systemPrompt", "系统提示词长度需为 100~8000 字，当前 " + raw.length() + " 字");
        }
        List<String> allowed = AgentConfigDefaults.ALLOWED_PLACEHOLDERS.get(runtime);
        Matcher matcher = PLACEHOLDER_PATTERN.matcher(raw);
        while (matcher.find())
        {
            String placeholder = matcher.group(1);
            if (!allowed.contains(placeholder))
            {
                // 未知占位符在 Python 侧渲染会 KeyError → 只能回落默认模板，
                // 配置看着生效实际不生效；在保存时就拒掉（契约 §4.4）
                throw field("systemPrompt", "不支持的占位符 {" + placeholder + "}");
            }
        }
        return raw;
    }

    private String validateTools(String runtime, String raw)
    {
        List<String> tokens = splitTools(raw);
        if (tokens.isEmpty())
        {
            if (AgentConfigDefaults.RUNTIME_CHAT.equals(runtime))
            {
                throw field("tools", "chat 载体至少需要选择 1 个工具");
            }
            return null;
        }
        if (!AgentConfigDefaults.RUNTIME_CHAT.equals(runtime))
        {
            throw field("tools", "该运行载体不支持配置工具，必须为空");
        }
        List<String> whitelist = AgentConfigDefaults.TOOL_WHITELIST_BY_RUNTIME.get(runtime);
        for (String token : tokens)
        {
            if (!whitelist.contains(token))
            {
                throw field("tools", "不支持的工具：" + token);
            }
        }
        return StringUtils.join(tokens, ",");
    }

    private String validateModel(String raw)
    {
        String model = trimToNull(raw);
        if (model == null)
        {
            return null;
        }
        if (!AgentConfigDefaults.MODEL_WHITELIST.contains(model))
        {
            throw field("model", "模型不在白名单：" + model);
        }
        return model;
    }

    private BigDecimal validateTemperature(BigDecimal temperature)
    {
        if (temperature == null)
        {
            return null;
        }
        if (temperature.compareTo(BigDecimal.valueOf(TEMPERATURE_MIN)) < 0
                || temperature.compareTo(BigDecimal.valueOf(TEMPERATURE_MAX)) > 0)
        {
            // 契约：>1.2 只做前端黄色提示，后端只拒 0~2 之外（避免把正常调参拦住）
            throw field("temperature", "温度需在 0~2 之间");
        }
        return temperature;
    }

    private Integer validateMaxTokens(Integer maxTokens)
    {
        if (maxTokens == null)
        {
            return null;
        }
        if (maxTokens.intValue() < MAX_TOKENS_MIN || maxTokens.intValue() > MAX_TOKENS_MAX)
        {
            throw field("maxTokens", "最大输出 token 需在 256~8192 之间");
        }
        return maxTokens;
    }

    private Integer resolveTimeoutS(Integer timeoutS, AgentConfig current, boolean create)
    {
        if (timeoutS == null)
        {
            // 编辑时沿用当前值：前端漏传字段不应该把超时重置成 60（那是一次静默的配置变更）
            return create ? Integer.valueOf(AgentConfigDefaults.DEFAULT_TIMEOUT_S) : current.getTimeoutS();
        }
        if (timeoutS.intValue() < TIMEOUT_MIN_S || timeoutS.intValue() > TIMEOUT_MAX_S)
        {
            throw field("timeoutS", "超时时间需在 5~300 秒之间");
        }
        return timeoutS;
    }

    private Integer resolveMaxRetries(Integer maxRetries, AgentConfig current, boolean create)
    {
        if (maxRetries == null)
        {
            return create ? Integer.valueOf(AgentConfigDefaults.DEFAULT_MAX_RETRIES) : current.getMaxRetries();
        }
        if (maxRetries.intValue() < RETRIES_MIN || maxRetries.intValue() > RETRIES_MAX)
        {
            throw field("maxRetries", "重试次数需在 0~5 之间");
        }
        return maxRetries;
    }

    private int resolveEnabled(Integer enabled, AgentConfig current, boolean create)
    {
        if (enabled == null)
        {
            return create ? AgentConfigDefaults.DEFAULT_ENABLED : current.getEnabled().intValue();
        }
        return resolveEnabledValue(enabled);
    }

    private int resolveEnabledValue(Integer enabled)
    {
        if (enabled == null)
        {
            throw field("enabled", "启用状态必填（0 或 1）");
        }
        if (enabled.intValue() != ENABLED_YES && enabled.intValue() != ENABLED_NO)
        {
            throw field("enabled", "启用状态只能是 0 或 1");
        }
        return enabled.intValue();
    }

    private String validateDisabledReason(int enabled, String raw)
    {
        String reason = trimToNull(raw);
        if (enabled == ENABLED_YES)
        {
            // 启用态不留旧原因：否则重新启用后页面仍显示「上次停用原因」，会被误读为当前状态
            return null;
        }
        if (reason == null)
        {
            throw field("disabledReason", "停用必须填写原因（5~100 字）");
        }
        if (reason.length() < DISABLED_REASON_MIN_LENGTH || reason.length() > DISABLED_REASON_MAX_LENGTH)
        {
            throw field("disabledReason", "停用原因长度需为 5~100 字，当前 " + reason.length() + " 字");
        }
        return reason;
    }

    private String validateChangeNote(String raw)
    {
        String note = trimToNull(raw);
        if (note != null && note.length() > CHANGE_NOTE_MAX_LENGTH)
        {
            throw field("changeNote", "变更说明不能超过 200 字，当前 " + note.length() + " 字");
        }
        return note;
    }

    /**
     * 校验并序列化业务参数（区间口径来自 {@link AgentConfigDefaults#PARAM_SPECS}）。
     *
     * <p>允许缺省键（Python 侧 normalize_params 会补默认值），但**拒绝未知键**：
     * 未知键说明前端/文档与 schema 不一致，静默丢弃会让用户以为参数保存了。
     *
     * @param runtime 载体
     * @param raw     入参参数对象
     * @return 规范化后的 JSON 文本；无参数返回 null
     */
    private String validateParams(String runtime, Map<String, Object> raw)
    {
        if (raw == null || raw.isEmpty())
        {
            return null;
        }
        List<AgentConfigDefaults.ParamSpec> specs = AgentConfigDefaults.paramSpecs(runtime);
        if (specs.isEmpty())
        {
            throw field("paramsJson", "运行载体 " + runtime + " 不支持业务参数，必须为 {} 或 null");
        }
        Map<String, AgentConfigDefaults.ParamSpec> specByKey = new LinkedHashMap<String, AgentConfigDefaults.ParamSpec>();
        for (AgentConfigDefaults.ParamSpec spec : specs)
        {
            specByKey.put(spec.getKey(), spec);
        }
        for (Map.Entry<String, Object> entry : raw.entrySet())
        {
            AgentConfigDefaults.ParamSpec spec = specByKey.get(entry.getKey());
            if (spec == null)
            {
                throw field("paramsJson", "不支持的参数：" + entry.getKey());
            }
            validateParamValue(spec, entry.getValue());
        }
        if (AgentConfigDefaults.RUNTIME_RESTOCK.equals(runtime)
                && raw.containsKey("factor_min") && raw.containsKey("factor_max"))
        {
            double factorMin = ((Number) raw.get("factor_min")).doubleValue();
            double factorMax = ((Number) raw.get("factor_max")).doubleValue();
            if (factorMin >= factorMax)
            {
                // 语义矛盾：min>=max 会让校准区间为空，Python 侧只能整组回落默认值（等于白配）
                throw field("paramsJson", "factor_min 必须小于 factor_max（当前 " + factorMin + " >= " + factorMax + "）");
            }
        }
        try
        {
            return JSON.writeValueAsString(raw);
        }
        catch (JsonProcessingException e)
        {
            throw new ServiceException("业务参数序列化失败", e);
        }
    }

    private void validateParamValue(AgentConfigDefaults.ParamSpec spec, Object value)
    {
        if (value == null)
        {
            throw field("paramsJson", "参数 " + spec.getKey() + " 不能为 null（不设置请省略该键）");
        }
        if (!(value instanceof Number))
        {
            throw field("paramsJson", "参数 " + spec.getKey() + " 必须为数字");
        }
        double number = ((Number) value).doubleValue();
        if (spec.isIntType() && number != Math.floor(number))
        {
            throw field("paramsJson", "参数 " + spec.getKey() + " 必须为整数");
        }
        if (number < spec.minAsDouble() || number > spec.maxAsDouble())
        {
            throw field("paramsJson", "参数 " + spec.getKey() + " 需在 [" + spec.getMin() + ", " + spec.getMax()
                    + "] 区间内，当前 " + value);
        }
    }

    // ------------------------------------------------------------------------------------
    // 落库副作用：版本 +1、历史快照、重载通知
    // ------------------------------------------------------------------------------------

    /**
     * 写操作成功后的统一收尾：版本 +1 → 同事务写历史 → 提交后异步通知 Python。
     *
     * @param config           已通过 CAS 的实体（version 仍是提交时的旧版本）
     * @param submittedVersion 提交的旧版本（新增传 0）
     * @param changeNote       历史快照的变更说明
     * @param operator         操作者
     * @return 出参（version 为新版本）
     */
    private AgentConfigVo finishSave(AgentConfig config, Long submittedVersion, String changeNote, String operator)
    {
        long newVersion = submittedVersion.longValue() + 1L;
        // 为什么不直接改 config 的 version：这个对象是刚传给 Mapper 的入参，
        // 原地改版本会让「CAS 用旧版本、写历史用新版本」两个语义混在同一个对象里，
        // 单测用 ArgumentCaptor 也只能看到改后的值（读不到真正发出去的 WHERE 条件）。
        AgentConfig saved = copyWithVersion(config, newVersion);
        saved.setChangeNote(changeNote);
        insertHistory(saved, changeNote, operator);
        notifyReloadAfterCommit();
        return toVo(saved);
    }

    /**
     * 复制实体并指定新版本（写历史快照与出参都用副本，保持 Mapper 入参对象不被修改）。
     *
     * @param config  源实体
     * @param version 新版本号
     * @return 副本
     */
    private AgentConfig copyWithVersion(AgentConfig config, long version)
    {
        AgentConfig copy = new AgentConfig();
        copy.setId(config.getId());
        copy.setCode(config.getCode());
        copy.setName(config.getName());
        copy.setDescription(config.getDescription());
        copy.setRuntime(config.getRuntime());
        copy.setScene(config.getScene());
        copy.setBuiltin(config.getBuiltin());
        copy.setSystemPrompt(config.getSystemPrompt());
        copy.setTools(config.getTools());
        copy.setModel(config.getModel());
        copy.setTemperature(config.getTemperature());
        copy.setMaxTokens(config.getMaxTokens());
        copy.setTimeoutS(config.getTimeoutS());
        copy.setMaxRetries(config.getMaxRetries());
        copy.setParamsJson(config.getParamsJson());
        copy.setEnabled(config.getEnabled());
        copy.setDisabledReason(config.getDisabledReason());
        copy.setChangeNote(config.getChangeNote());
        copy.setVersion(Long.valueOf(version));
        copy.setCreateBy(config.getCreateBy());
        copy.setCreateTime(config.getCreateTime());
        copy.setUpdateBy(config.getUpdateBy());
        copy.setUpdateTime(config.getUpdateTime());
        copy.setDelFlag(config.getDelFlag());
        return copy;
    }

    private void insertHistory(AgentConfig config, String changeNote, String operator)
    {
        AgentConfigHistory history = new AgentConfigHistory();
        history.setCode(config.getCode());
        history.setVersion(config.getVersion());
        history.setSnapshot(buildSnapshotJson(config));
        history.setChangeNote(changeNote);
        history.setCreateBy(operator);
        history.setCreateTime(DateUtils.getNowDate());
        agentConfigMapper.insertAgentConfigHistory(history);
    }

    /**
     * 构建整行快照 JSON（键名 snake_case，与 agent_config 列名一致；params_json 内嵌为对象）。
     *
     * <p>键集合与 seed 快照一致（不含 id/审计列/change_note）：回滚只恢复「配置内容」，
     * 不恢复创建人、创建时间与自增主键。
     */
    private String buildSnapshotJson(AgentConfig config)
    {
        Map<String, Object> snapshot = new LinkedHashMap<String, Object>();
        snapshot.put("builtin", config.getBuiltin());
        snapshot.put("code", config.getCode());
        snapshot.put("description", config.getDescription());
        snapshot.put("disabled_reason", config.getDisabledReason());
        snapshot.put("enabled", config.getEnabled());
        snapshot.put("max_retries", config.getMaxRetries());
        snapshot.put("max_tokens", config.getMaxTokens());
        snapshot.put("model", config.getModel());
        snapshot.put("name", config.getName());
        snapshot.put("params_json", readParamsOrThrow(config.getParamsJson()));
        snapshot.put("runtime", config.getRuntime());
        snapshot.put("scene", config.getScene());
        snapshot.put("system_prompt", config.getSystemPrompt());
        snapshot.put("temperature", config.getTemperature());
        snapshot.put("timeout_s", config.getTimeoutS());
        snapshot.put("tools", config.getTools());
        snapshot.put("version", config.getVersion());
        try
        {
            return JSON.writeValueAsString(snapshot);
        }
        catch (JsonProcessingException e)
        {
            throw new ServiceException("配置快照序列化失败", e);
        }
    }

    /**
     * 配置落库后通知 Python 重载；有事务时挂到 afterCommit。
     *
     * <p>为什么必须 afterCommit：通知是异步的，若在 commit 前发出，Python 可能在事务可见前
     * 就回查数据库，读到旧值并重建缓存——「保存成功但新配置要等 30s TTL」的假象。
     */
    private void notifyReloadAfterCommit()
    {
        if (TransactionSynchronizationManager.isSynchronizationActive())
        {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization()
            {
                @Override
                public void afterCommit()
                {
                    reloadNotifier.notifyReload();
                }
            });
        }
        else
        {
            // 单测/非事务调用路径：直接通知（通知器内部仍是异步且失败只 WARN）
            reloadNotifier.notifyReload();
        }
    }

    // ------------------------------------------------------------------------------------
    // 回滚快照解析 / 实体转换 / 工具方法
    // ------------------------------------------------------------------------------------

    /**
     * 解析历史快照为可写实体（只取可恢复的配置内容列）。
     *
     * @param snapshotJson 快照 JSON 文本
     * @return 实体（未设置 id/code 之外的审计列）
     */
    private AgentConfig parseSnapshot(String snapshotJson)
    {
        if (StringUtils.isBlank(snapshotJson))
        {
            throw new ServiceException("历史快照为空，无法回滚");
        }
        JsonNode root;
        try
        {
            root = JSON.readTree(snapshotJson);
        }
        catch (IOException e)
        {
            throw new ServiceException("历史快照不是合法 JSON，无法回滚", e);
        }
        if (root == null || !root.isObject())
        {
            throw new ServiceException("历史快照结构非法，无法回滚");
        }
        AgentConfig config = new AgentConfig();
        config.setName(textValue(root, "name"));
        config.setDescription(textValue(root, "description"));
        config.setRuntime(textValue(root, "runtime"));
        config.setSystemPrompt(textValue(root, "system_prompt"));
        config.setTools(textValue(root, "tools"));
        config.setModel(textValue(root, "model"));
        config.setTemperature(decimalValue(root, "temperature"));
        config.setMaxTokens(intValue(root, "max_tokens"));
        config.setTimeoutS(intValue(root, "timeout_s"));
        config.setMaxRetries(intValue(root, "max_retries"));
        JsonNode params = root.get("params_json");
        config.setParamsJson(params == null || params.isNull() ? null : params.toString());
        config.setEnabled(intValue(root, "enabled"));
        config.setDisabledReason(textValue(root, "disabled_reason"));
        if (StringUtils.isBlank(config.getName()) || StringUtils.isBlank(config.getRuntime()))
        {
            throw new ServiceException("历史快照缺少必要字段（name/runtime），无法回滚");
        }
        return config;
    }

    /**
     * 回滚用的待更新实体：身份列取当前行，内容列取历史快照，version 取提交版本（CAS 条件）。
     */
    private AgentConfig copyForRollback(AgentConfig current, AgentConfig restored, Long submittedVersion,
            String operator)
    {
        AgentConfig config = new AgentConfig();
        config.setId(current.getId());
        config.setVersion(submittedVersion);
        config.setCode(current.getCode());
        config.setRuntime(current.getRuntime());
        config.setScene(current.getScene());
        config.setBuiltin(current.getBuiltin());
        config.setCreateBy(current.getCreateBy());
        config.setCreateTime(current.getCreateTime());
        config.setDelFlag(current.getDelFlag());
        config.setUpdateBy(operator);
        config.setUpdateTime(DateUtils.getNowDate());
        config.setName(restored.getName());
        config.setDescription(restored.getDescription());
        config.setSystemPrompt(restored.getSystemPrompt());
        config.setTools(restored.getTools());
        config.setModel(restored.getModel());
        config.setTemperature(restored.getTemperature());
        config.setMaxTokens(restored.getMaxTokens());
        config.setTimeoutS(restored.getTimeoutS());
        config.setMaxRetries(restored.getMaxRetries());
        config.setParamsJson(restored.getParamsJson());
        config.setEnabled(restored.getEnabled());
        config.setDisabledReason(restored.getDisabledReason());
        return config;
    }

    /**
     * 启停用的待更新实体：所有可编辑列原样复制当前行，只改 enabled/disabledReason。
     */
    private AgentConfig copyForEnabledUpdate(AgentConfig current, int enabled, String disabledReason,
            String changeNote, String operator, long submittedVersion)
    {
        AgentConfig config = new AgentConfig();
        config.setId(current.getId());
        config.setVersion(Long.valueOf(submittedVersion));
        config.setCode(current.getCode());
        config.setRuntime(current.getRuntime());
        config.setScene(current.getScene());
        config.setBuiltin(current.getBuiltin());
        config.setCreateBy(current.getCreateBy());
        config.setCreateTime(current.getCreateTime());
        config.setDelFlag(current.getDelFlag());
        config.setName(current.getName());
        config.setDescription(current.getDescription());
        config.setSystemPrompt(current.getSystemPrompt());
        config.setTools(current.getTools());
        config.setModel(current.getModel());
        config.setTemperature(current.getTemperature());
        config.setMaxTokens(current.getMaxTokens());
        config.setTimeoutS(current.getTimeoutS());
        config.setMaxRetries(current.getMaxRetries());
        config.setParamsJson(current.getParamsJson());
        config.setEnabled(Integer.valueOf(enabled));
        config.setDisabledReason(validateDisabledReason(enabled, disabledReason));
        config.setChangeNote(validateChangeNote(changeNote));
        config.setUpdateBy(operator);
        config.setUpdateTime(DateUtils.getNowDate());
        return config;
    }

    private AgentConfigVo toVo(AgentConfig config)
    {
        if (config == null)
        {
            return null;
        }
        AgentConfigVo vo = new AgentConfigVo();
        vo.setId(config.getId());
        vo.setCode(config.getCode());
        vo.setName(config.getName());
        vo.setDescription(config.getDescription());
        vo.setRuntime(config.getRuntime());
        vo.setScene(config.getScene());
        vo.setBuiltin(config.getBuiltin());
        vo.setSystemPrompt(config.getSystemPrompt());
        vo.setTools(config.getTools());
        vo.setModel(config.getModel());
        vo.setTemperature(config.getTemperature());
        vo.setMaxTokens(config.getMaxTokens());
        vo.setTimeoutS(config.getTimeoutS());
        vo.setMaxRetries(config.getMaxRetries());
        vo.setParamsJson(readParamsForVo(config));
        vo.setEnabled(config.getEnabled());
        vo.setDisabledReason(config.getDisabledReason());
        vo.setVersion(config.getVersion());
        vo.setChangeNote(config.getChangeNote());
        vo.setCreateBy(config.getCreateBy());
        vo.setCreateTime(config.getCreateTime());
        vo.setUpdateBy(config.getUpdateBy());
        vo.setUpdateTime(config.getUpdateTime());
        return vo;
    }

    /**
     * 出参的 params_json → 对象。脏数据不阻断列表（一个坏行不该让整页 500），
     * 与 Python 侧「损坏 JSON → 回落默认 + WARN」同一原则。
     */
    private Map<String, Object> readParamsForVo(AgentConfig config)
    {
        if (StringUtils.isBlank(config.getParamsJson()))
        {
            return new LinkedHashMap<String, Object>();
        }
        try
        {
            return readParams(config.getParamsJson());
        }
        catch (IOException e)
        {
            log.warn("配置 id={} 的 params_json 非法，按空对象返回", config.getId());
            return new LinkedHashMap<String, Object>();
        }
    }

    private Map<String, Object> readParamsOrThrow(String paramsJson)
    {
        if (StringUtils.isBlank(paramsJson))
        {
            return new LinkedHashMap<String, Object>();
        }
        try
        {
            return readParams(paramsJson);
        }
        catch (IOException e)
        {
            throw new ServiceException("业务参数 JSON 解析失败", e);
        }
    }

    private Map<String, Object> readParams(String paramsJson) throws IOException
    {
        return JSON.readValue(paramsJson, new TypeReference<LinkedHashMap<String, Object>>()
        {
        });
    }

    // ------------------------------------------------------------------------------------
    // 小工具
    // ------------------------------------------------------------------------------------

    private AgentConfig loadOrThrow(Long id)
    {
        if (id == null)
        {
            throw new ServiceException("配置ID不能为空");
        }
        AgentConfig config = agentConfigMapper.selectAgentConfigById(id);
        if (config == null)
        {
            throw new ServiceException("配置不存在或已删除：" + id);
        }
        return config;
    }

    private AgentConfigConflictException conflictAfterFailedCas(Long id, Long submittedVersion)
    {
        AgentConfig latest = agentConfigMapper.selectAgentConfigById(id);
        if (latest == null)
        {
            throw new ServiceException("配置不存在或已删除：" + id);
        }
        long current = latest.getVersion() == null ? -1L : latest.getVersion().longValue();
        long submitted = submittedVersion == null ? -1L : submittedVersion.longValue();
        log.warn("配置乐观锁冲突 id={} currentVersion={} submittedVersion={}", id, Long.valueOf(current),
                Long.valueOf(submitted));
        return new AgentConfigConflictException(current, submitted);
    }

    private void requireNameUnique(String name, Long excludeId)
    {
        if (agentConfigMapper.countAgentConfigByName(name, excludeId) > 0)
        {
            throw field("name", "名称已存在：" + name);
        }
    }

    private int nextScene()
    {
        Integer maxScene = agentConfigMapper.selectMaxScene();
        int max = maxScene == null ? 0 : maxScene.intValue();
        return Math.max(AgentConfigDefaults.CUSTOM_SCENE_BASE, max + 1);
    }

    private String resolveMetaRuntime(String runtime)
    {
        String resolved = trimToNull(runtime);
        if (resolved == null)
        {
            return AgentConfigDefaults.RUNTIME_CHAT;
        }
        if (!AgentConfigDefaults.isRuntime(resolved))
        {
            log.warn("meta 接口收到未知载体 {}，按 chat 返回", runtime);
            return AgentConfigDefaults.RUNTIME_CHAT;
        }
        return resolved;
    }

    private List<String> splitTools(String raw)
    {
        List<String> tokens = new ArrayList<String>();
        if (StringUtils.isBlank(raw))
        {
            return tokens;
        }
        Set<String> seen = new LinkedHashSet<String>();
        String[] parts = raw.split(",");
        for (String part : parts)
        {
            String token = trimToNull(part);
            if (token != null && seen.add(token))
            {
                tokens.add(token);
            }
        }
        return tokens;
    }

    private String changeNoteOrDefault(String changeNote, String defaultNote)
    {
        String note = trimToNull(changeNote);
        return note == null ? defaultNote : note;
    }

    private String trimToNull(String value)
    {
        if (value == null)
        {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private String textValue(JsonNode root, String field)
    {
        JsonNode node = root.get(field);
        return node == null || node.isNull() ? null : node.asText();
    }

    private Integer intValue(JsonNode root, String field)
    {
        JsonNode node = root.get(field);
        return node == null || node.isNull() ? null : Integer.valueOf(node.asInt());
    }

    private BigDecimal decimalValue(JsonNode root, String field)
    {
        JsonNode node = root.get(field);
        return node == null || node.isNull() ? null : new BigDecimal(node.asText());
    }

    private AgentConfigValidationException field(String field, String message)
    {
        return new AgentConfigValidationException(field, message);
    }
}
