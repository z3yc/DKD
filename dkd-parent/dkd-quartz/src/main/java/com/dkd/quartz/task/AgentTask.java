package com.dkd.quartz.task;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;
import com.dkd.common.agent.domain.AgentUpstreamResult;
import com.dkd.common.agent.support.AgentRequestId;
import com.dkd.common.agent.support.AgentUpstreamClient;
import com.dkd.common.agent.support.AgentUpstreamException;
import com.dkd.common.utils.DateUtils;
import com.dkd.common.utils.StringUtils;

/**
 * 智能体定时任务（Phase 1 / 任务 1-9）：每日 06:00 触发补货分析。
 *
 * <p>调用目标字符串（sys_job.invoke_target）：{@code agentTask.restockAnalyze()}，
 * 注册 SQL 见 {@code docs/ddl/agent_restock_job.sql}。
 *
 * <p>四个必须写清楚的行为约定（每一条都对应一个真会发生的故障）：
 * <ol>
 *   <li><b>开关关闭不是失败。</b>{@code agent.enabled=false} 时直接返回并记 WARN——
 *       抛出异常会让「按计划停用智能体」变成每天一条失败告警，团队就会开始忽略告警
 *       （真正的故障反而没人看）。</li>
 *   <li><b>被暂停（toggle）也不是失败。</b>Python 侧读 {@code agent_restock_pause} 后返回
 *       {@code data.status=skipped}，HTTP 仍是 200；本任务照常记 INFO 不告警。
 *       「暂停自动分析」是运营主动决定的，不是故障。</li>
 *   <li><b>重试只对“可能自己好”的失败。</b>传输层不可达/超时与上游 5xx 才重试；
 *       4xx（密钥错误、路径不存在、参数非法）重试只会把日志刷满，直接失败并让人去看配置。</li>
 *   <li><b>最终失败必须抛异常。</b>RuoYi 的 {@code AbstractQuartzJob} 会把异常写入
 *       sys_job_log 并置 status=失败，这是「失败可告警」的落地方式（AGENTS §6.4 禁止静默失败）；
 *       若只打日志，06:00 悄悄没跑就没人知道，早上运营只会看到「今天怎么没有建议」。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-09-23
 */
@Component("agentTask")
public class AgentTask
{
    private static final Logger log = LoggerFactory.getLogger(AgentTask.class);

    /**
     * 触发路径：属 {@code /agents/**}（服务间密钥）而不是 {@code /agent/restock/**}（用户 JWT 经网关）。
     * 两套鉴权体系不混用（AGENTS §7.5）
     */
    private static final String ANALYZE_PATH = "/agents/restock/analyze";

    /**
     * 失败原因截断长度：sys_job_log.exception_info 会被 AbstractQuartzJob 截到 2000 字符，
     * 这里再紧一档，保证「状态码 + 中文原因」都能留下
     */
    private static final int ERROR_PREVIEW_MAX = 300;

    private final AgentProperties properties;

    private final AgentUpstreamClient upstreamClient;

    public AgentTask(AgentProperties properties, AgentUpstreamClient upstreamClient)
    {
        this.properties = properties;
        this.upstreamClient = upstreamClient;
    }

    /**
     * 每日 06:00 触发补货分析（分析在人工确认处中断，计划已在 Python 侧落库）。
     *
     * @throws IllegalStateException 全部尝试均失败，或配置缺失（由 Quartz 记录为任务失败）
     */
    public void restockAnalyze()
    {
        // 约定 1：开关关闭 → 跳过（不是失败）
        if (!properties.getEnabled())
        {
            log.warn("智能体开关已关闭（agent.enabled=false），跳过本次补货分析（配置状态，非任务失败）");
            return;
        }
        // 密钥缺失属配置错误：重试无意义，但必须让它在任务日志里可见（否则表现为“上游 401”更难查）
        if (!StringUtils.hasText(properties.getSecret()))
        {
            throw new IllegalStateException("agent.secret 未配置，无法触发补货分析（检查 DKD_AGENT_SERVICE_SECRET 是否注入）");
        }

        String planDate = DateUtils.getDate();
        String requestId = "restock-job-" + AgentRequestId.generate().substring(0, 8);
        Map<String, String> headers = new LinkedHashMap<String, String>();
        headers.put(AgentHeaders.SECRET, properties.getSecret());
        headers.put(AgentHeaders.REQUEST_ID, requestId);
        headers.put(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE);
        // planDate 由 Java 侧算好传过去：两端各自 today() 在跨时区/跨机部署时会不一致，
        // 现象是「今天没有计划」却查不出原因（Python 侧缺省值仅用于本地脚本/演示）
        byte[] body = ("{\"planDate\":\"" + planDate + "\"}").getBytes(StandardCharsets.UTF_8);

        int maxAttempts = Math.max(1, properties.getAnalyzeRetryMax() + 1);
        int attempts = 0;
        String lastFailure = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++)
        {
            attempts = attempt;
            try
            {
                AgentUpstreamResult result = upstreamClient.forward("POST", ANALYZE_PATH, null, headers, body);
                if (result.getStatus() == HttpStatus.OK.value())
                {
                    logAnalyzeResult(requestId, planDate, result);
                    return;
                }
                lastFailure = "上游返回 " + result.getStatus() + describe(result);
                // 约定 3：4xx 是配置/鉴权问题，重试不会变好；5xx 才继续尝试
                if (result.getStatus() < HttpStatus.INTERNAL_SERVER_ERROR.value())
                {
                    break;
                }
            }
            catch (AgentUpstreamException e)
            {
                lastFailure = "智能体服务不可达或超时（status=" + e.getStatus() + "：" + e.getMessage() + "）";
            }
            log.warn("补货分析第 {}/{} 次尝试未成功：{} requestId={}", attempt, maxAttempts, lastFailure, requestId);
            sleepBeforeRetry(attempt, maxAttempts);
        }
        // 约定 4：最终失败必须抛异常（RuoYi 写入 sys_job_log 的失败记录）。
        // 报告**实际**尝试次数（4xx 会在第一次就跳出循环）：声称“试了 3 次”而实际只试了 1 次，
        // 会让看日志的人误以为上游抖了三次。
        throw new IllegalStateException(
                "补货分析任务最终失败（已尝试 " + attempts + "/" + maxAttempts + " 次，requestId=" + requestId + "）："
                        + lastFailure);
    }

    /**
     * 解析并记录 200 响应：区分「跑完了」与「被暂停跳过」（约定 2）。
     *
     * <p>只记解析后的计数，**不记原始响应体**（AGENTS §6.1：日志不落业务数据/请求体）。
     */
    private void logAnalyzeResult(String requestId, String planDate, AgentUpstreamResult result)
    {
        JSONObject data = parseData(result);
        if (data == null)
        {
            log.info("补货分析已触发（响应体不可解析，未影响任务成功判定）planDate={} requestId={}", planDate, requestId);
            return;
        }
        if ("skipped".equals(data.getString("status")))
        {
            log.info("补货分析被「暂停自动分析」开关跳过 planDate={} reason={} requestId={}",
                    planDate, data.getString("reason"), requestId);
            return;
        }
        log.info("补货分析完成 planDate={} 设备={} 货道={} 建议总数={} 中断={} requestId={}",
                planDate, data.getIntValue("deviceCount"), data.getIntValue("channelCount"),
                data.getIntValue("totalQuantity"), data.getBooleanValue("interrupted"), requestId);
    }

    /**
     * 从 RuoYi 信封里取 {@code data}；取不到返回 null（不抛异常：解析失败不该让任务失败）。
     *
     * @param result 上游结果
     * @return data 对象或 null
     */
    private JSONObject parseData(AgentUpstreamResult result)
    {
        byte[] payload = result.getBody();
        if (payload == null || payload.length == 0)
        {
            return null;
        }
        try
        {
            JSONObject envelope = JSON.parseObject(new String(payload, StandardCharsets.UTF_8));
            return envelope == null ? null : envelope.getJSONObject("data");
        }
        catch (RuntimeException e)
        {
            log.warn("补货分析响应解析失败（不影响任务成功判定）：{}", e.getClass().getSimpleName());
            return null;
        }
    }

    /**
     * 取上游错误信封里的中文 `msg`（我们约定的可读失败原因），截断到 {@link #ERROR_PREVIEW_MAX}。
     *
     * <p>为什么只取 msg 而不是整个响应体：msg 是 Python 侧有意暴露给人看的一句话（不含业务数据），
     * 而响应体可能包含数据行；日志只留能定位问题的最小信息。
     *
     * @param result 上游结果
     * @return 形如「（原因）」，无 msg 时返回空串
     */
    private String describe(AgentUpstreamResult result)
    {
        byte[] payload = result.getBody();
        if (payload == null || payload.length == 0)
        {
            return "";
        }
        try
        {
            JSONObject envelope = JSON.parseObject(new String(payload, StandardCharsets.UTF_8));
            String message = envelope == null ? null : envelope.getString("msg");
            if (!StringUtils.hasText(message))
            {
                return "";
            }
            return "（" + StringUtils.substring(message, 0, ERROR_PREVIEW_MAX) + "）";
        }
        catch (RuntimeException e)
        {
            return "（响应体非 JSON，见 Python 侧同 requestId 日志）";
        }
    }

    /**
     * 线性退避等待（第 n 次重试等 n × analyzeRetryBackoffMs）。
     *
     * @param attempt 当前已完成的尝试序号（1 起）
     * @param maxAttempts 总尝试次数
     * @throws IllegalStateException 等待期间线程被中断（Quartz 停机）
     */
    private void sleepBeforeRetry(int attempt, int maxAttempts)
    {
        if (attempt >= maxAttempts)
        {
            return;
        }
        long waitMs = properties.getAnalyzeRetryBackoffMs() * attempt;
        if (waitMs <= 0)
        {
            return;
        }
        try
        {
            Thread.sleep(waitMs);
        }
        catch (InterruptedException e)
        {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("补货分析任务在重试等待期间被中断", e);
        }
    }
}
