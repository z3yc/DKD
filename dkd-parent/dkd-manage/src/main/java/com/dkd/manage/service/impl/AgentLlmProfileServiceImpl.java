package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.regex.Pattern;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.agent.AgentLlmProbeClient;
import com.dkd.manage.domain.AgentLlmProbe;
import com.dkd.manage.domain.AgentLlmProfile;
import com.dkd.manage.domain.dto.AgentLlmProfileDto;
import com.dkd.manage.domain.dto.AgentLlmProfileEnabledDto;
import com.dkd.manage.mapper.AgentLlmProfileMapper;
import com.dkd.manage.mapper.AgentLlmProbeMapper;
import com.dkd.manage.service.IAgentLlmProfileService;

/**
 * 模型档案 Service 实现（排期 3-5）。
 *
 * <p>三条关键纪律（都在代码里落地，而不是只写在文档里）：
 * <ol>
 *   <li><b>只碰环境变量名</b>：入参/实体/表都没有密钥值字段；探测时把 {@code apiKeyEnv}
 *       原样交给 Python，由 Python 在本进程内解析环境变量（AGENTS §7.1）；</li>
 *   <li><b>isDefault 互斥</b>：MySQL 无部分唯一索引，靠"同一事务里先 clearDefaultFlag 再写本行"
 *       保证最多一个默认档案；顺序不能反（先写后清，中途失败会留下两个默认）；
 *       {@code @Transactional} 是这一步的必要条件，不是习惯性加注解；</li>
 *   <li><b>探测失败也要留证据</b>：probe 方法**刻意不加事务**——Python 不可达时先插失败记录
 *       再抛业务错误，若加了事务，抛异常会把刚插入的记录一起回滚掉，页面"已记录"就成假话。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentLlmProfileServiceImpl implements IAgentLlmProfileService
{
    /** 档案编码规则（与配置台/文档口径一致） */
    private static final Pattern CODE_PATTERN = Pattern.compile("^[a-z][a-z0-9_]{2,31}$");

    /** 环境变量名规则：大写字母开头，3~64 位大写字母/数字/下划线（只允许变量名，不可能塞进密钥值） */
    private static final Pattern API_KEY_ENV_PATTERN = Pattern.compile("^[A-Z][A-Z0-9_]{2,63}$");

    /** 基址规则：http/https + 非空白字符（不做严格 URL 解析：兼容带路径的兼容网关） */
    private static final Pattern BASE_URL_PATTERN = Pattern.compile("^https?://\\S+$");

    private static final int CODE_MAX_LENGTH = 64;
    private static final int NAME_MIN_LENGTH = 2;
    private static final int NAME_MAX_LENGTH = 50;
    private static final int PROVIDER_MAX_LENGTH = 32;
    private static final int BASE_URL_MAX_LENGTH = 255;
    private static final int MODEL_MAX_LENGTH = 64;
    private static final int DESCRIPTION_MAX_LENGTH = 500;
    private static final int TIMEOUT_S_MIN = 5;
    private static final int TIMEOUT_S_MAX = 300;
    private static final int MAX_RETRIES_MIN = 0;
    private static final int MAX_RETRIES_MAX = 5;
    private static final int TIMEOUT_S_DEFAULT = 60;
    private static final int MAX_RETRIES_DEFAULT = 2;
    private static final String PROVIDER_DEFAULT = "openai_compatible";
    private static final int FLAG_YES = 1;
    private static final int FLAG_NO = 0;

    /** 失败摘要落库上限（DDL error_msg VARCHAR(500)） */
    private static final int ERROR_MSG_MAX_LENGTH = 500;

    @Autowired
    private AgentLlmProfileMapper agentLlmProfileMapper;

    @Autowired
    private AgentLlmProbeMapper agentLlmProbeMapper;

    @Autowired
    private AgentLlmProbeClient agentLlmProbeClient;

    @Override
    public List<AgentLlmProfile> selectAgentLlmProfileList(AgentLlmProfile query)
    {
        AgentLlmProfile condition = query == null ? new AgentLlmProfile() : query;
        condition.setCode(trimToNull(condition.getCode()));
        List<AgentLlmProfile> list = agentLlmProfileMapper.selectAgentLlmProfileList(condition);
        return list == null ? new ArrayList<AgentLlmProfile>() : list;
    }

    @Override
    @Transactional
    public AgentLlmProfile insertAgentLlmProfile(AgentLlmProfileDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentLlmProfile profile = new AgentLlmProfile();
        String code = trimToNull(dto.getCode());
        if (code == null)
        {
            throw field("code", "档案编码必填");
        }
        if (!CODE_PATTERN.matcher(code).matches())
        {
            throw field("code", "档案编码格式不正确：需小写字母开头，3~32 位小写字母/数字/下划线");
        }
        // DB 有 UK，这里提前查是为了返回字段级错误而不是 DuplicateKey 的 500
        if (agentLlmProfileMapper.countAgentLlmProfileByCode(code, null) > 0)
        {
            throw field("code", "档案编码已存在：" + code);
        }
        profile.setCode(code);
        fillEditableFields(profile, dto, null);
        Date now = new Date();
        profile.setCreateBy(operator);
        profile.setCreateTime(now);
        profile.setUpdateBy(operator);
        profile.setUpdateTime(now);
        profile.setDelFlag("0");
        if (isDefaultFlag(profile.getIsDefault()))
        {
            // 新增时本行还没有 id，excludeId=null 表示"把当前所有默认档案清 0"
            agentLlmProfileMapper.clearDefaultFlag(null, operator, now);
        }
        int rows = agentLlmProfileMapper.insertAgentLlmProfile(profile);
        if (rows != 1)
        {
            throw new ServiceException("新增模型档案失败：影响行数 " + rows);
        }
        return agentLlmProfileMapper.selectAgentLlmProfileById(profile.getId());
    }

    @Override
    @Transactional
    public AgentLlmProfile updateAgentLlmProfile(AgentLlmProfileDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        if (dto.getId() == null)
        {
            throw new ServiceException("档案ID不能为空");
        }
        AgentLlmProfile current = loadOrThrow(dto.getId());
        String code = trimToNull(dto.getCode());
        if (code != null && !code.equals(current.getCode()))
        {
            // 编码被 agent_llm_probe.profile_code 引用；改编码会让历史探测记录指向一个不存在的档案
            throw field("code", "档案编码不可修改：库内为 " + current.getCode());
        }
        AgentLlmProfile profile = new AgentLlmProfile();
        profile.setId(current.getId());
        profile.setCode(current.getCode());
        fillEditableFields(profile, dto, current);
        Date now = new Date();
        profile.setUpdateBy(operator);
        profile.setUpdateTime(now);
        if (isDefaultFlag(profile.getIsDefault()))
        {
            // 先清其它行再写本行（顺序见类注释纪律 2）
            agentLlmProfileMapper.clearDefaultFlag(current.getId(), operator, now);
        }
        int rows = agentLlmProfileMapper.updateAgentLlmProfile(profile);
        if (rows != 1)
        {
            throw new ServiceException("更新模型档案失败：记录可能已被删除（id=" + current.getId() + "）");
        }
        return agentLlmProfileMapper.selectAgentLlmProfileById(current.getId());
    }

    @Override
    public AgentLlmProfile updateEnabled(Long id, AgentLlmProfileEnabledDto dto, String operator)
    {
        if (id == null)
        {
            throw new ServiceException("档案ID不能为空");
        }
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentLlmProfile current = loadOrThrow(id);
        int enabled = dto.getEnabled() == null ? FLAG_YES : dto.getEnabled().intValue();
        validateFlag("enabled", enabled);
        int rows = agentLlmProfileMapper.updateAgentLlmProfileEnabled(id, Integer.valueOf(enabled), operator,
                new Date());
        if (rows != 1)
        {
            throw new ServiceException("更新启用状态失败：记录可能已被删除（id=" + current.getId() + "）");
        }
        return agentLlmProfileMapper.selectAgentLlmProfileById(id);
    }

    @Override
    public AgentLlmProbe probe(Long id, String requestId, String operator)
    {
        if (id == null)
        {
            throw new ServiceException("档案ID不能为空");
        }
        AgentLlmProfile profile = loadOrThrow(id);
        AgentLlmProbeClient.ProbeOutcome outcome = agentLlmProbeClient.ping(profile, requestId);

        AgentLlmProbe probe = new AgentLlmProbe();
        probe.setProfileCode(profile.getCode());
        probe.setOk(Integer.valueOf(outcome.isOk() ? FLAG_YES : FLAG_NO));
        probe.setHttpStatus(outcome.getHttpStatus());
        probe.setLatencyMs(Integer.valueOf(outcome.getLatencyMs()));
        probe.setModelServed(trimToNull(outcome.getModelServed()));
        probe.setErrorType(trimToNull(outcome.getErrorType()));
        probe.setErrorMsg(truncate(outcome.getErrorMsg(), ERROR_MSG_MAX_LENGTH));
        probe.setRequestId(requestId);
        probe.setCreateBy(operator);
        probe.setCreateTime(new Date());
        // 本方法刻意不加 @Transactional：下面 Python 不可达时要"先落失败记录再抛业务错误"，
        // 事务会把记录一起回滚（见类注释纪律 3）
        agentLlmProbeMapper.insertAgentLlmProbe(probe);
        if (outcome.isTransportFailure())
        {
            throw new ServiceException("连通测试失败：智能体服务不可达（" + nvl(probe.getErrorType(), "未知原因")
                    + "），探测结果已记录");
        }
        return probe;
    }

    @Override
    public List<AgentLlmProbe> selectProbeList(Long profileId)
    {
        if (profileId == null)
        {
            throw new ServiceException("档案ID不能为空");
        }
        AgentLlmProfile profile = loadOrThrow(profileId);
        return selectProbeListByCode(profile.getCode());
    }

    @Override
    public AgentLlmProfile selectAgentLlmProfileById(Long id)
    {
        return loadOrThrow(id);
    }

    @Override
    public List<AgentLlmProbe> selectProbeListByCode(String profileCode)
    {
        if (profileCode == null || profileCode.trim().isEmpty())
        {
            throw new ServiceException("档案编码不能为空");
        }
        List<AgentLlmProbe> list = agentLlmProbeMapper.selectAgentLlmProbeList(profileCode.trim());
        return list == null ? new ArrayList<AgentLlmProbe>() : list;
    }

    // ------------------------------------------------------------------------------------
    // 校验 + 组装
    // ------------------------------------------------------------------------------------

    /**
     * 填充可编辑字段并校验（新增/编辑共用）。
     *
     * <p>合并语义：DTO 为 null 的字段沿用库内当前值（编辑）或默认值（新增）；
     * 空串是"显式清空"（仅 description 允许清空）。
     *
     * @param target  待落库实体
     * @param dto     入参
     * @param current 库内当前行（新增为 null）
     */
    private void fillEditableFields(AgentLlmProfile target, AgentLlmProfileDto dto, AgentLlmProfile current)
    {
        String name = requiredText(mergeText(dto.getName(), current == null ? null : current.getName()), "name", "名称必填");
        if (name.length() < NAME_MIN_LENGTH || name.length() > NAME_MAX_LENGTH)
        {
            throw field("name", "名称需 " + NAME_MIN_LENGTH + "~" + NAME_MAX_LENGTH + " 字（当前 " + name.length() + "）");
        }
        target.setName(name);

        String provider = mergeText(dto.getProvider(), current == null ? null : current.getProvider());
        if (StringUtils.isEmpty(provider))
        {
            provider = PROVIDER_DEFAULT;
        }
        if (provider.length() > PROVIDER_MAX_LENGTH)
        {
            throw field("provider", "供应商类型最长 " + PROVIDER_MAX_LENGTH + " 个字符");
        }
        target.setProvider(provider);

        String baseUrl = requiredText(mergeText(dto.getBaseUrl(), current == null ? null : current.getBaseUrl()),
                "baseUrl", "基址必填");
        if (baseUrl.length() > BASE_URL_MAX_LENGTH)
        {
            throw field("baseUrl", "基址最长 " + BASE_URL_MAX_LENGTH + " 个字符");
        }
        if (!BASE_URL_PATTERN.matcher(baseUrl).matches())
        {
            throw field("baseUrl", "基址必须是 http:// 或 https:// 开头的地址");
        }
        target.setBaseUrl(baseUrl);

        String model = requiredText(mergeText(dto.getModel(), current == null ? null : current.getModel()), "model",
                "模型名必填");
        if (model.length() > MODEL_MAX_LENGTH)
        {
            throw field("model", "模型名最长 " + MODEL_MAX_LENGTH + " 个字符");
        }
        target.setModel(model);

        String apiKeyEnv = requiredText(mergeText(dto.getApiKeyEnv(), current == null ? null : current.getApiKeyEnv()),
                "apiKeyEnv", "环境变量名必填");
        if (!API_KEY_ENV_PATTERN.matcher(apiKeyEnv).matches())
        {
            // 提示里明确"只填变量名"，避免运营把真实密钥粘进来（那会直接落库！）
            throw field("apiKeyEnv",
                    "环境变量名格式不正确：需大写字母开头，3~64 位大写字母/数字/下划线（只填变量名，不要填密钥值）");
        }
        target.setApiKeyEnv(apiKeyEnv);

        int timeoutS = mergeInt(dto.getTimeoutS(), current == null ? null : current.getTimeoutS(), TIMEOUT_S_DEFAULT);
        if (timeoutS < TIMEOUT_S_MIN || timeoutS > TIMEOUT_S_MAX)
        {
            throw field("timeoutS",
                    "超时需在 " + TIMEOUT_S_MIN + "~" + TIMEOUT_S_MAX + " 秒之间（当前 " + timeoutS + "）");
        }
        target.setTimeoutS(Integer.valueOf(timeoutS));

        int maxRetries = mergeInt(dto.getMaxRetries(), current == null ? null : current.getMaxRetries(),
                MAX_RETRIES_DEFAULT);
        if (maxRetries < MAX_RETRIES_MIN || maxRetries > MAX_RETRIES_MAX)
        {
            throw field("maxRetries", "重试次数需在 " + MAX_RETRIES_MIN + "~" + MAX_RETRIES_MAX + " 之间（当前 "
                    + maxRetries + "）");
        }
        target.setMaxRetries(Integer.valueOf(maxRetries));

        int enabled = mergeInt(dto.getEnabled(), current == null ? null : current.getEnabled(), FLAG_YES);
        validateFlag("enabled", enabled);
        target.setEnabled(Integer.valueOf(enabled));

        int isDefault = mergeInt(dto.getIsDefault(), current == null ? null : current.getIsDefault(), FLAG_NO);
        validateFlag("isDefault", isDefault);
        target.setIsDefault(Integer.valueOf(isDefault));

        // description 允许为空：DTO 传 "" 表示清空；null 表示不修改
        String description;
        if (dto.getDescription() == null)
        {
            description = current == null ? null : current.getDescription();
        }
        else
        {
            description = trimToNull(dto.getDescription());
        }
        if (description != null && description.length() > DESCRIPTION_MAX_LENGTH)
        {
            throw field("description", "说明最长 " + DESCRIPTION_MAX_LENGTH + " 个字符");
        }
        target.setDescription(description);
    }

    /**
     * 取当前档案；不存在抛业务错误。
     *
     * @param id 主键
     * @return 档案
     */
    private AgentLlmProfile loadOrThrow(Long id)
    {
        AgentLlmProfile profile = agentLlmProfileMapper.selectAgentLlmProfileById(id);
        if (profile == null)
        {
            throw new ServiceException("模型档案不存在：" + id);
        }
        return profile;
    }

    private void validateFlag(String fieldName, int value)
    {
        if (value != FLAG_YES && value != FLAG_NO)
        {
            throw field(fieldName, (("enabled".equals(fieldName)) ? "启用状态" : "默认标记") + "只能是 0 或 1");
        }
    }

    private boolean isDefaultFlag(Integer isDefault)
    {
        return isDefault != null && isDefault.intValue() == FLAG_YES;
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

    private int mergeInt(Integer dtoValue, Integer currentValue, int defaultValue)
    {
        if (dtoValue != null)
        {
            return dtoValue.intValue();
        }
        return currentValue == null ? defaultValue : currentValue.intValue();
    }

    private String trimToNull(String value)
    {
        return StringUtils.isEmpty(StringUtils.trim(value)) ? null : StringUtils.trim(value);
    }

    private String truncate(String value, int maxLength)
    {
        if (value == null)
        {
            return null;
        }
        return value.length() <= maxLength ? value : value.substring(0, maxLength);
    }

    private String nvl(String value, String defaultValue)
    {
        return StringUtils.isEmpty(value) ? defaultValue : value;
    }
}
