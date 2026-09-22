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
}
