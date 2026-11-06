package com.dkd.manage.service.impl;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentEvalGateResult;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentEvalReport;
import com.dkd.manage.domain.dto.AgentEvalReportDto;
import com.dkd.manage.mapper.AgentEvalReportMapper;
import com.dkd.manage.service.IAgentEvalReportService;

/**
 * 评测报告与保存护栏 Service 实现（排期 5-1）。
 *
 * <p>三条关键规则（每条都有对应的拒绝路径单测）：
 * <ol>
 *   <li><b>枚举与口径必须自证</b>：mode ∈ {live, offline}、status ∈ {scored, blocked, failed}；
 *       {@code passed+failed+blocked == total}；{@code scored} 必须有 accuracy、
 *       {@code blocked/failed} 必须没有 accuracy——否则护栏会拿"没测"当"0 分"或拿"跑挂"当"满分"；</li>
 *   <li><b>报告正文只落库</b>：report_json ≤1MB、必填，但**绝不写日志**（可能含用例原文）；</li>
 *   <li><b>基线不伪造</b>：baseline_accuracy 为空表示评测集没设基线，此时只要 scored 且有 accuracy
 *       就放行（不能因为"没写基线"把所有人拦死）。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentEvalReportServiceImpl implements IAgentEvalReportService
{
    private static final String MODE_LIVE = "live";
    private static final String MODE_OFFLINE = "offline";

    private static final String STATUS_SCORED = "scored";
    private static final String STATUS_BLOCKED = "blocked";
    private static final String STATUS_FAILED = "failed";

    private static final List<String> MODES = Arrays.asList(MODE_LIVE, MODE_OFFLINE);
    private static final List<String> STATUSES = Arrays.asList(STATUS_SCORED, STATUS_BLOCKED, STATUS_FAILED);

    /** 报告正文上限 1MB（按 UTF-8 字节；LONGTEXT 能存更大，但应用层限制防误传整包） */
    private static final int REPORT_JSON_MAX_BYTES = 1024 * 1024;

    private static final int CONFIG_CODE_MAX_LENGTH = 64;
    private static final int RUN_BY_MAX_LENGTH = 64;

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    /** accuracy 列 DECIMAL(5,4)：最多 4 位小数 */
    private static final int ACCURACY_SCALE = 4;

    @Autowired
    private AgentEvalReportMapper agentEvalReportMapper;

    @Autowired(required = false)
    private AgentProperties agentProperties;

    @Override
    public AgentEvalReport insertReport(AgentEvalReportDto dto, String operator)
    {
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentEvalReport report = new AgentEvalReport();
        report.setConfigCode(validateConfigCode(dto.getConfigCode()));
        if (dto.getConfigVersion() == null || dto.getConfigVersion().longValue() < 1L)
        {
            throw field("configVersion", "配置版本必填且 ≥1");
        }
        report.setConfigVersion(dto.getConfigVersion());
        if (dto.getScene() != null && dto.getScene().intValue() < 1)
        {
            throw field("scene", "场景码必须 ≥1（空 = 全场景汇总）");
        }
        report.setScene(dto.getScene());

        String mode = trimToNull(dto.getMode());
        if (mode == null)
        {
            throw field("mode", "评测模式必填（live/offline）");
        }
        if (!MODES.contains(mode))
        {
            throw field("mode", "未知评测模式：" + mode + "（只允许 " + MODES + "）");
        }
        report.setMode(mode);

        String status = trimToNull(dto.getStatus());
        if (status == null)
        {
            throw field("status", "评测状态必填（scored/blocked/failed）");
        }
        if (!STATUSES.contains(status))
        {
            throw field("status", "未知评测状态：" + status + "（只允许 " + STATUSES + "）");
        }
        report.setStatus(status);

        int total = nonNegative(dto.getTotal(), "total");
        int passed = nonNegative(dto.getPassed(), "passed");
        int failed = nonNegative(dto.getFailed(), "failed");
        int blocked = nonNegative(dto.getBlocked(), "blocked");
        if (passed + failed + blocked != total)
        {
            // 三个分项与总数不一致时，accuracy 与护栏判断都会建立在错误分母上，必须拒绝
            throw field("total", "通过 + 失败 + 阻塞必须等于用例总数（" + passed + "+" + failed + "+" + blocked
                    + " != " + total + "）");
        }
        report.setTotal(Integer.valueOf(total));
        report.setPassed(Integer.valueOf(passed));
        report.setFailed(Integer.valueOf(failed));
        report.setBlocked(Integer.valueOf(blocked));

        fillAccuracy(report, status, dto.getAccuracy());
        fillBaseline(report, dto.getBaselineAccuracy(), dto.getBaselineN());

        String reportJson = dto.getReportJson();
        if (StringUtils.isEmpty(reportJson))
        {
            throw field("reportJson", "评测报告正文必填");
        }
        if (reportJson.getBytes(StandardCharsets.UTF_8).length > REPORT_JSON_MAX_BYTES)
        {
            throw field("reportJson", "评测报告正文超过 1MB 上限");
        }
        report.setReportJson(reportJson);

        String runBy = trimToNull(dto.getRunBy());
        if (runBy == null)
        {
            runBy = trimToNull(operator);
        }
        if (runBy != null && runBy.length() > RUN_BY_MAX_LENGTH)
        {
            throw field("runBy", "执行人最长 " + RUN_BY_MAX_LENGTH + " 个字符");
        }
        report.setRunBy(runBy);
        report.setCreateTime(new Date());

        int rows = agentEvalReportMapper.insertAgentEvalReport(report);
        if (rows != 1)
        {
            throw new ServiceException("评测报告写入失败：影响行数 " + rows);
        }
        return report;
    }

    @Override
    public List<AgentEvalReport> selectLatestByCode(String configCode, Integer limit)
    {
        String code = validateConfigCode(configCode);
        int resolvedLimit = limit == null ? DEFAULT_LIMIT : limit.intValue();
        if (resolvedLimit < 1 || resolvedLimit > MAX_LIMIT)
        {
            throw field("limit", "条数需在 1~" + MAX_LIMIT + " 之间");
        }
        List<AgentEvalReport> rows = agentEvalReportMapper.selectLatestByCode(code, resolvedLimit);
        return rows == null ? new ArrayList<AgentEvalReport>() : rows;
    }

    @Override
    public Map<String, Object> selectGate(String configCode, Long configVersion)
    {
        String code = validateConfigCode(configCode);
        boolean enabled = isGateEnabled();
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("enabled", Boolean.valueOf(enabled));
        if (configVersion == null)
        {
            // 没有版本号无法判定：保存时会用库内当前版本再判一次，这里只如实说明
            result.put("passed", Boolean.TRUE);
            result.put("latest", null);
            result.put("reason", enabled ? "缺少 configVersion，无法判定；保存时会以当前版本为准" : "评测护栏未启用");
            return result;
        }
        AgentEvalGateResult gate = checkGate(code, configVersion);
        // 关闭护栏时"不拦"即为通过；但仍回传最新报告，页面可继续做信息展示
        result.put("passed", Boolean.valueOf(!enabled || gate.isPassed()));
        result.put("latest", gate.getLatest());
        result.put("reason", enabled ? gate.getReason() : "评测护栏未启用（仅展示最新报告）");
        return result;
    }

    @Override
    public AgentEvalGateResult checkGate(String configCode, Long configVersion)
    {
        String code = validateConfigCode(configCode);
        if (configVersion == null || configVersion.longValue() < 1L)
        {
            return AgentEvalGateResult.reject("缺少有效的配置版本，无法判定评测结论", null);
        }
        // 不按 status 过滤：必须能看到"最新一次是 blocked/failed"，否则会把"跑挂了"误报成"从未评测"
        AgentEvalReport latest = agentEvalReportMapper.selectLatestForVersion(code, configVersion);
        if (latest == null)
        {
            return AgentEvalGateResult.reject(
                    "当前版本（v" + configVersion + "）没有评测报告：请先运行评测再保存", null);
        }
        if (!STATUS_SCORED.equals(latest.getStatus()))
        {
            return AgentEvalGateResult.reject("当前版本最新评测状态为 " + latest.getStatus()
                    + "（非 scored），不能作为保存依据；请先修复评测后重试", latest);
        }
        BigDecimal accuracy = latest.getAccuracy();
        if (accuracy == null)
        {
            // 历史脏数据兜底：scored 却没有 accuracy 时不能当成"通过"
            return AgentEvalGateResult.reject("评测报告缺少准确率，不能作为保存依据", latest);
        }
        BigDecimal baseline = latest.getBaselineAccuracy();
        if (baseline != null && accuracy.compareTo(baseline) < 0)
        {
            return AgentEvalGateResult.reject("评测准确率 " + accuracy.toPlainString() + " 低于基线 "
                    + baseline.toPlainString() + "，禁止保存（如确需保存请填写原因强制跳过）", latest);
        }
        return AgentEvalGateResult.pass(latest);
    }

    // ------------------------------------------------------------------------------------
    // 校验辅助
    // ------------------------------------------------------------------------------------

    /**
     * 配置编码校验（与 agent_config.code 同规则；跨模块只做长度+正则，避免依赖配置 Service）。
     *
     * @param raw 原始编码
     * @return 去空白后的编码
     */
    private String validateConfigCode(String raw)
    {
        String code = trimToNull(raw);
        if (code == null)
        {
            throw field("configCode", "配置编码必填");
        }
        if (code.length() > CONFIG_CODE_MAX_LENGTH)
        {
            throw field("configCode", "配置编码最长 " + CONFIG_CODE_MAX_LENGTH + " 个字符");
        }
        if (!java.util.regex.Pattern.matches(com.dkd.manage.agent.AgentConfigDefaults.CODE_PATTERN, code))
        {
            throw field("configCode", "配置编码格式不正确：需小写字母开头，3~32 位小写字母/数字/下划线");
        }
        return code;
    }

    /**
     * accuracy 口径校验：scored 必须有值（0~1），blocked/failed 必须为 null。
     *
     * @param report 报告
     * @param status 状态
     * @param raw    原始准确率
     */
    private void fillAccuracy(AgentEvalReport report, String status, BigDecimal raw)
    {
        if (STATUS_SCORED.equals(status))
        {
            if (raw == null)
            {
                throw field("accuracy", "status=scored 时准确率必填");
            }
            report.setAccuracy(validateRatio("accuracy", raw));
            return;
        }
        if (raw != null)
        {
            // blocked/failed 写 accuracy 会伪造"测了但有分数"的假象：必须显式拒绝
            throw field("accuracy", "status=" + status + " 时不能填准确率（只有 scored 才有准确率）");
        }
        report.setAccuracy(null);
    }

    /**
     * 基线口径校验（可空；有值时 0~1）。
     *
     * @param report   报告
     * @param accuracy 基线准确率
     * @param baselineN 基线样本量
     */
    private void fillBaseline(AgentEvalReport report, BigDecimal accuracy, Integer baselineN)
    {
        report.setBaselineAccuracy(accuracy == null ? null : validateRatio("baselineAccuracy", accuracy));
        if (baselineN != null && baselineN.intValue() < 0)
        {
            throw field("baselineN", "基线样本量不能为负数");
        }
        report.setBaselineN(baselineN);
    }

    /**
     * 比率校验：0~1，并按列精度（4 位小数）四舍五入，避免数据库截断造成"存进去和判定的不一致"。
     *
     * @param fieldName 字段名
     * @param value     原值
     * @return 规范化后的值
     */
    private BigDecimal validateRatio(String fieldName, BigDecimal value)
    {
        if (value.compareTo(BigDecimal.ZERO) < 0 || value.compareTo(BigDecimal.ONE) > 0)
        {
            throw field(fieldName, fieldName + " 必须在 0~1 之间（当前 " + value.toPlainString() + "）");
        }
        return value.setScale(ACCURACY_SCALE, RoundingMode.HALF_UP);
    }

    private int nonNegative(Integer value, String fieldName)
    {
        if (value == null)
        {
            return 0;
        }
        if (value.intValue() < 0)
        {
            throw field(fieldName, fieldName + " 不能为负数");
        }
        return value.intValue();
    }

    private boolean isGateEnabled()
    {
        return agentProperties != null && agentProperties.getEvalGateEnabled();
    }

    private AgentFieldErrorException field(String fieldName, String message)
    {
        return new AgentFieldErrorException(fieldName, message);
    }

    private String trimToNull(String value)
    {
        return StringUtils.isEmpty(StringUtils.trim(value)) ? null : StringUtils.trim(value);
    }
}
