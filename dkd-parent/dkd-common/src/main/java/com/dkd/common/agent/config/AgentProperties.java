package com.dkd.common.agent.config;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 智能体服务（dkd-agent）配置，前缀 {@code agent}（方案 §3.3）。
 *
 * <p>为什么 enabled 的 getter 不写成 isEnabled：Spring Boot 对 boolean 属性的 JavaBean 读取
 * 只认 {@code getXxx}，写成 {@code isXxx} + setter 时部分版本会在绑定/刷新期抛
 * "not a valid property" 或静默不生效，属于典型陷阱，统一用 get 前缀规避。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@Component
@ConfigurationProperties(prefix = "agent")
public class AgentProperties
{
    /**
     * 智能体开关。false 时网关一律降级（前端应隐藏对话入口），主业务零影响
     */
    private boolean enabled = true;

    /**
     * Python 智能体服务基址（只监听回环/内网）
     */
    private String baseUrl = "http://127.0.0.1:8090";

    /**
     * 服务间密钥，必须与 Python 侧 DKD_AGENT_SERVICE_SECRET 一致；由环境变量注入
     */
    private String secret;

    /**
     * 连接上游超时（毫秒）。取值偏小：Python 本机部署，连不上就该快速降级
     */
    private int connectTimeout = 2000;

    /**
     * 读取上游超时（毫秒）。同时作为 SSE 空闲心跳上限与异步请求超时基线
     */
    private int readTimeout = 120000;

    /**
     * 回调路径白名单：未列入的 /agent/callback/** 一律拒绝（方案 §8“回调被滥用”缓解项）
     */
    private List<String> callbackPathWhitelist = new ArrayList<String>(
            Arrays.asList("/agent/callback/task"));

    /**
     * 定时分析（任务 1-9）失败后的额外重试次数：0 表示只试一次。
     *
     * <p>为什么给默认 2（共 3 次）：06:00 那一刻 Python 可能正在重启（部署/滚动），
     * 一次连不上就告警会让团队对告警脱敏；但也不能无限重试——全部失败必须抛异常，
     * 由 RuoYi 的定时任务日志（sys_job_log）留下 status=失败 的记录供人工发现（AGENTS §6.4 不允许静默失败）。
     */
    private int analyzeRetryMax = 2;

    /**
     * 定时分析重试间隔基数（毫秒），第 n 次重试实际等待 n × 该值（线性退避）。
     *
     * <p>取 5s 而不是指数退避到分钟级：本任务的窗口是「早上运营上班前」，
     * 等待过久会让 06:00 的计划错过早班确认。
     */
    private long analyzeRetryBackoffMs = 5000L;

    /**
     * 配置保存后是否向 Python 侧发「配置重载」通知（排期 2-2，契约 §4.7）。
     *
     * <p>为什么默认开：通知只是让新配置更快生效（TTL 30s 是兜底），
     * 关掉不会出错但会让「保存后立即对话仍是旧配置」的窗口从毫秒级扩到 30s。
     * 本地未起 Python 服务时可临时关掉，避免每次保存刷一条 WARN。
     */
    private boolean configReloadEnabled = true;

    /**
     * 配置重载通知的超时（毫秒），同时作为连接与读取超时。
     *
     * <p>为什么取 3s 而不是复用 {@link #readTimeout}：reload 是「尽力而为」的旁路通知，
     * 它跑在异步线程里、失败只 WARN；超时取小可以避免 Python 卡住时长期占用线程。
     */
    private int configReloadTimeoutMs = 3000;

    /**
     * 模型连通性探测（排期 3-5）的 HTTP 超时（毫秒），同时作为连接与读取超时。
     *
     * <p>为什么单独一个配置而不是复用 {@link #readTimeout}：探测是"人工点一下"的同步操作，
     * 需要在页面上尽快给出结论；而 {@code readTimeout} 默认 120s 是给 SSE 长连接用的，
     * 让「测试连通」卡两分钟既没有意义，也会让运营以为页面死了。默认 30s 与 Python 侧
     * llm_probe 的模型调用超时量级一致（探测本身要等一次真实 LLM 往返）。
     */
    private int llmProbeTimeoutMs = 30000;

    /**
     * 配置保存前的**评测护栏**开关（排期 5-1，env {@code AGENT_EVAL_GATE_ENABLED}）。
     *
     * <p>为什么默认 false（关闭）：
     * <ul>
     *   <li>评测流水线是**渐进上线**的：先能把报告落库、能在页面查，再要求"每次保存必须跑评测"。
     *       默认开启会让所有还没接评测的配置保存立刻被拦死（连改个错别字都不行）；</li>
     *   <li>关闭时行为必须与改造前**完全一致**（不新增任何拒绝路径），这是回归安全的前提。</li>
     * </ul>
     * 开启后：编辑保存需有当前版本的最新 scored 报告且准确率不低于基线，否则拒绝
     * （可用 force+forceReason≥10 字强制跳过，变更说明里会留痕）。
     */
    private boolean evalGateEnabled = false;

    public boolean getEnabled()
    {
        return enabled;
    }

    public void setEnabled(boolean enabled)
    {
        this.enabled = enabled;
    }

    public String getBaseUrl()
    {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl)
    {
        this.baseUrl = baseUrl;
    }

    public String getSecret()
    {
        return secret;
    }

    public void setSecret(String secret)
    {
        this.secret = secret;
    }

    public int getConnectTimeout()
    {
        return connectTimeout;
    }

    public void setConnectTimeout(int connectTimeout)
    {
        this.connectTimeout = connectTimeout;
    }

    public int getReadTimeout()
    {
        return readTimeout;
    }

    public void setReadTimeout(int readTimeout)
    {
        this.readTimeout = readTimeout;
    }

    public List<String> getCallbackPathWhitelist()
    {
        return callbackPathWhitelist;
    }

    public void setCallbackPathWhitelist(List<String> callbackPathWhitelist)
    {
        this.callbackPathWhitelist = callbackPathWhitelist;
    }

    public int getAnalyzeRetryMax()
    {
        return analyzeRetryMax;
    }

    public void setAnalyzeRetryMax(int analyzeRetryMax)
    {
        this.analyzeRetryMax = analyzeRetryMax;
    }

    public long getAnalyzeRetryBackoffMs()
    {
        return analyzeRetryBackoffMs;
    }

    public void setAnalyzeRetryBackoffMs(long analyzeRetryBackoffMs)
    {
        this.analyzeRetryBackoffMs = analyzeRetryBackoffMs;
    }

    public boolean getConfigReloadEnabled()
    {
        return configReloadEnabled;
    }

    public void setConfigReloadEnabled(boolean configReloadEnabled)
    {
        this.configReloadEnabled = configReloadEnabled;
    }

    public int getConfigReloadTimeoutMs()
    {
        return configReloadTimeoutMs;
    }

    public void setConfigReloadTimeoutMs(int configReloadTimeoutMs)
    {
        this.configReloadTimeoutMs = configReloadTimeoutMs;
    }

    public int getLlmProbeTimeoutMs()
    {
        return llmProbeTimeoutMs;
    }

    public void setLlmProbeTimeoutMs(int llmProbeTimeoutMs)
    {
        this.llmProbeTimeoutMs = llmProbeTimeoutMs;
    }

    public boolean getEvalGateEnabled()
    {
        return evalGateEnabled;
    }

    public void setEvalGateEnabled(boolean evalGateEnabled)
    {
        this.evalGateEnabled = evalGateEnabled;
    }
}
