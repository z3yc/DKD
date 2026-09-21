package com.dkd.common.ai.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * AI配置类
 * 
 * @author ruoyi
 */
@Component
@ConfigurationProperties(prefix = "ai")
public class AiConfig {
    
    /**
     * API密钥
     */
    private String apiKey;
    
    /**
     * API URL
     */
    private String apiUrl;
    
    /**
     * 模型名称
     */
    private String model = "deepseek-chat";
    
    /**
     * 超时时间（毫秒）
     */
    private Integer timeout = 20000;
    
    /**
     * 最大重试次数
     */
    private Integer maxRetries = 3;
    
    public String getApiKey() {
        return apiKey;
    }
    
    public void setApiKey(String apiKey) {
        this.apiKey = apiKey;
    }
    
    public String getApiUrl() {
        return apiUrl;
    }
    
    public void setApiUrl(String apiUrl) {
        this.apiUrl = apiUrl;
    }
    
    public String getModel() {
        return model;
    }
    
    public void setModel(String model) {
        this.model = model;
    }
    
    public Integer getTimeout() {
        return timeout;
    }
    
    public void setTimeout(Integer timeout) {
        this.timeout = timeout;
    }
    
    public Integer getMaxRetries() {
        return maxRetries;
    }
    
    public void setMaxRetries(Integer maxRetries) {
        this.maxRetries = maxRetries;
    }
}