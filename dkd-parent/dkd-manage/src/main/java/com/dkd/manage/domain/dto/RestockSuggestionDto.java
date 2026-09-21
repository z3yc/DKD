package com.dkd.manage.domain.dto;

import com.dkd.manage.domain.Inventory;

/**
 * 补货建议DTO
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public class RestockSuggestionDto
{
    /** 售货机ID */
    private Long vmId;

    /** 售货机编号 */
    private String innerCode;

    /** 商品ID */
    private Long skuId;

    /** 商品名称 */
    private String skuName;

    /** 货道ID */
    private Long channelId;

    /** 货道编号 */
    private String channelCode;

    /** 当前库存 */
    private Integer currentQuantity;

    /** 建议补货数量 */
    private Integer suggestedQuantity;

    /** 补货后的库存 */
    private Integer afterRestockQuantity;

    /** 最大容量 */
    private Integer maxCapacity;

    /** 预计补货时间（天） */
    private Integer estimatedDays;

    /** 优先级：1-低 2-中 3-高 4-紧急 */
    private Integer priority;

    /** 建议原因 */
    private String reason;

    public Long getVmId()
    {
        return vmId;
    }

    public void setVmId(Long vmId)
    {
        this.vmId = vmId;
    }

    public String getInnerCode()
    {
        return innerCode;
    }

    public void setInnerCode(String innerCode)
    {
        this.innerCode = innerCode;
    }

    public Long getSkuId()
    {
        return skuId;
    }

    public void setSkuId(Long skuId)
    {
        this.skuId = skuId;
    }

    public String getSkuName()
    {
        return skuName;
    }

    public void setSkuName(String skuName)
    {
        this.skuName = skuName;
    }

    public Long getChannelId()
    {
        return channelId;
    }

    public void setChannelId(Long channelId)
    {
        this.channelId = channelId;
    }

    public String getChannelCode()
    {
        return channelCode;
    }

    public void setChannelCode(String channelCode)
    {
        this.channelCode = channelCode;
    }

    public Integer getCurrentQuantity()
    {
        return currentQuantity;
    }

    public void setCurrentQuantity(Integer currentQuantity)
    {
        this.currentQuantity = currentQuantity;
    }

    public Integer getSuggestedQuantity()
    {
        return suggestedQuantity;
    }

    public void setSuggestedQuantity(Integer suggestedQuantity)
    {
        this.suggestedQuantity = suggestedQuantity;
    }

    public Integer getAfterRestockQuantity()
    {
        return afterRestockQuantity;
    }

    public void setAfterRestockQuantity(Integer afterRestockQuantity)
    {
        this.afterRestockQuantity = afterRestockQuantity;
    }

    public Integer getMaxCapacity()
    {
        return maxCapacity;
    }

    public void setMaxCapacity(Integer maxCapacity)
    {
        this.maxCapacity = maxCapacity;
    }

    public Integer getEstimatedDays()
    {
        return estimatedDays;
    }

    public void setEstimatedDays(Integer estimatedDays)
    {
        this.estimatedDays = estimatedDays;
    }

    public Integer getPriority()
    {
        return priority;
    }

    public void setPriority(Integer priority)
    {
        this.priority = priority;
    }

    public String getReason()
    {
        return reason;
    }

    public void setReason(String reason)
    {
        this.reason = reason;
    }
}
