package com.dkd.manage.agent;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestTemplate;

import com.dkd.common.agent.config.AgentProperties;
import com.dkd.common.agent.domain.AgentHeaders;

import java.util.concurrent.CompletableFuture;

/**
 * 配置保存后的「重载通知」发送器（排期 2-2，契约 §4.7）。
 *
 * <p>设计要点（每一条都是契约里点名要求的）：
 * <ul>
 *   <li><b>异步 + 失败只 WARN</b>：通知是旁路优化，Python 侧 TTL 30s 才是兜底；
 *       绝不能因为 Python没起/重启就让运营的「保存」失败或变慢；</li>
 *   <li><b>独立超时</b>：取 {@code agent.configReloadTimeoutMs}（默认 3s），
 *       不复用网关的 readTimeout（120s）——否则 Python 卡死时异步线程会被长期占用；</li>
 *   <li><b>不记录响应体</b>：与 AGENTS §6.1 一致，日志只记状态码/异常类型，不 dump body；</li>
 *   <li><b>不复用 {@code AgentUpstreamClient}</b>：那是网关的转发通道（SSE 语义、错误透传），
 *       本通知只需要一个 fire-and-forget 的 POST，复用会把两条不同生命周期绑在一起。</li>
 * </ul>
 *
 * @author ruoyi
 * @date 2026-11-03
 */
@Component
public class AgentConfigReloadNotifier
{
    private static final Logger log = LoggerFactory.getLogger(AgentConfigReloadNotifier.class);

    /** Python 侧重载端点（契约 §4.7：POST /agents/config/reload，body {}） */
    private static final String RELOAD_PATH = "/agents/config/reload";

    /** 与 Python 侧 DKD_AGENT_SERVICE_SECRET 对应的服务间密钥头 */
    private static final String SECRET_HEADER = AgentHeaders.SECRET;

    private final AgentProperties properties;

    public AgentConfigReloadNotifier(AgentProperties properties)
    {
        this.properties = properties;
    }

    /**
     * 异步发出重载通知；本方法不抛异常、不阻塞调用方。
     */
    public void notifyReload()
    {
        if (!properties.getConfigReloadEnabled())
        {
            log.debug("配置重载通知已关闭（agent.configReloadEnabled=false），跳过");
            return;
        }
        final String baseUrl = normalizeBaseUrl(properties.getBaseUrl());
        final String secret = properties.getSecret();
        final int timeoutMs = resolveTimeoutMs();
        // 为什么用 CompletableFuture.commonPool：保存链路是 Web 线程，通知是低频（人工保存）操作，
        // 不值得为此引入一套专用线程池与生命周期管理；commonPool 的并行度对这种偶发任务绰绰有余。
        CompletableFuture.runAsync(new Runnable()
        {
            @Override
            public void run()
            {
                doNotify(baseUrl, secret, timeoutMs);
            }
        });
    }

    /**
     * 真正发请求（在异步线程里执行）。
     *
     * @param baseUrl   Python 服务基址（已去尾斜杠）
     * @param secret    服务间密钥，可为空（Python 侧会拒绝并返回 401，这里只 WARN）
     * @param timeoutMs 连接/读取超时
     */
    private void doNotify(String baseUrl, String secret, int timeoutMs)
    {
        try
        {
            SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
            factory.setConnectTimeout(timeoutMs);
            factory.setReadTimeout(timeoutMs);
            RestTemplate restTemplate = new RestTemplate(factory);

            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            if (StringUtils.hasText(secret))
            {
                headers.set(SECRET_HEADER, secret);
            }
            else
            {
                // 密钥没配也必须发：Python 侧返回 401 会如实暴露「密钥未注入」，
                // 比静默跳过更容易排查（本地开发常见）
                log.warn("未配置 agent.secret，配置重载通知可能被 Python 侧拒绝");
            }
            HttpEntity<String> request = new HttpEntity<String>("{}", headers);
            ResponseEntity<String> response = restTemplate.exchange(baseUrl + RELOAD_PATH, HttpMethod.POST, request,
                    String.class);
            if (response.getStatusCode().is2xxSuccessful())
            {
                log.info("已通知 Python 重载智能体配置 baseUrl={}", baseUrl);
            }
            else
            {
                log.warn("配置重载通知返回非 2xx：status={}", Integer.valueOf(response.getStatusCodeValue()));
            }
        }
        catch (Exception e)
        {
            // 只记异常类型与基址：响应体可能含内部信息（AGENTS §6.1 不 dump body）
            log.warn("配置重载通知失败 baseUrl={} err={}", baseUrl, e.getClass().getSimpleName());
        }
    }

    private int resolveTimeoutMs()
    {
        int configured = properties.getConfigReloadTimeoutMs();
        return configured > 0 ? configured : 3000;
    }

    private String normalizeBaseUrl(String baseUrl)
    {
        if (!StringUtils.hasText(baseUrl))
        {
            return "http://127.0.0.1:8090";
        }
        return baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
    }
}
