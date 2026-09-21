package com.dkd.manage.domain;

import java.util.Date;
import com.fasterxml.jackson.annotation.JsonFormat;
import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;
import com.dkd.common.annotation.Excel;
import com.dkd.common.core.domain.BaseEntity;

/**
 * 库存对象 tb_inventory
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public class Inventory extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;

    /** 售货机Id */
    @Excel(name = "售货机Id")
    private Long vmId;

    /** 商品Id */
    @Excel(name = "商品Id")
    private Long skuId;

    /** 货道Id */
    @Excel(name = "货道Id")
    private Long channelId;

    /** 当前库存数量 */
    @Excel(name = "当前库存数量")
    private Integer quantity;

    /** 最小库存预警值 */
    @Excel(name = "最小库存预警值")
    private Integer minQuantity;

    /** 最大库存容量 */
    @Excel(name = "最大库存容量")
    private Integer maxQuantity;

    /** 上次补货时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    @Excel(name = "上次补货时间", width = 30, dateFormat = "yyyy-MM-dd HH:mm:ss")
    private Date lastSupplyTime;

    /** 售货机编号 */
    @Excel(name = "售货机编号")
    private String innerCode;

    /** 商品名称（非数据库字段，用于展示） */
    @Excel(name = "商品名称")
    private String skuName;

    /** 商品图片（非数据库字段，用于展示） */
    private String skuImage;

    /** 货道编号（非数据库字段，用于展示） */
    @Excel(name = "货道编号")
    private String channelCode;

    public void setId(Long id)
    {
        this.id = id;
    }

    public Long getId()
    {
        return id;
    }

    public void setVmId(Long vmId)
    {
        this.vmId = vmId;
    }

    public Long getVmId()
    {
        return vmId;
    }

    public void setSkuId(Long skuId)
    {
        this.skuId = skuId;
    }

    public Long getSkuId()
    {
        return skuId;
    }

    public void setChannelId(Long channelId)
    {
        this.channelId = channelId;
    }

    public Long getChannelId()
    {
        return channelId;
    }

    public void setQuantity(Integer quantity)
    {
        this.quantity = quantity;
    }

    public Integer getQuantity()
    {
        return quantity;
    }

    public void setMinQuantity(Integer minQuantity)
    {
        this.minQuantity = minQuantity;
    }

    public Integer getMinQuantity()
    {
        return minQuantity;
    }

    public void setMaxQuantity(Integer maxQuantity)
    {
        this.maxQuantity = maxQuantity;
    }

    public Integer getMaxQuantity()
    {
        return maxQuantity;
    }

    public void setLastSupplyTime(Date lastSupplyTime)
    {
        this.lastSupplyTime = lastSupplyTime;
    }

    public Date getLastSupplyTime()
    {
        return lastSupplyTime;
    }

    public void setInnerCode(String innerCode)
    {
        this.innerCode = innerCode;
    }

    public String getInnerCode()
    {
        return innerCode;
    }

    public void setSkuName(String skuName)
    {
        this.skuName = skuName;
    }

    public String getSkuName()
    {
        return skuName;
    }

    public void setSkuImage(String skuImage)
    {
        this.skuImage = skuImage;
    }

    public String getSkuImage()
    {
        return skuImage;
    }

    public void setChannelCode(String channelCode)
    {
        this.channelCode = channelCode;
    }

    public String getChannelCode()
    {
        return channelCode;
    }

    @Override
    public String toString() {
        return new ToStringBuilder(this,ToStringStyle.MULTI_LINE_STYLE)
            .append("id", getId())
            .append("vmId", getVmId())
            .append("skuId", getSkuId())
            .append("channelId", getChannelId())
            .append("quantity", getQuantity())
            .append("minQuantity", getMinQuantity())
            .append("maxQuantity", getMaxQuantity())
            .append("lastSupplyTime", getLastSupplyTime())
            .append("createTime", getCreateTime())
            .append("updateTime", getUpdateTime())
            .toString();
    }
}
