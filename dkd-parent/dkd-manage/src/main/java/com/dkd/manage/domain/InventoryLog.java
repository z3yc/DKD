package com.dkd.manage.domain;

import java.util.Date;
import com.fasterxml.jackson.annotation.JsonFormat;
import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;
import com.dkd.common.annotation.Excel;
import com.dkd.common.core.domain.BaseEntity;

/**
 * 库存变动日志对象 tb_inventory_log
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public class InventoryLog extends BaseEntity
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

    /** 变动类型：1-入库 2-出库 3-补货 4-调整 */
    @Excel(name = "变动类型", readConverterExp = "1=入库,2=出库,3=补货,4=调整")
    private Integer changeType;

    /** 变动数量 */
    @Excel(name = "变动数量")
    private Integer changeQuantity;

    /** 变动前库存 */
    @Excel(name = "变动前库存")
    private Integer beforeQuantity;

    /** 变动后库存 */
    @Excel(name = "变动后库存")
    private Integer afterQuantity;

    /** 变动时间 */
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    @Excel(name = "变动时间", width = 30, dateFormat = "yyyy-MM-dd HH:mm:ss")
    private Date changeTime;

    /** 操作人 */
    @Excel(name = "操作人")
    private String operator;

    /** 售货机编号（非数据库字段） */
    @Excel(name = "售货机编号")
    private String innerCode;

    /** 商品名称（非数据库字段） */
    @Excel(name = "商品名称")
    private String skuName;

    /** 货道编号（非数据库字段） */
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

    public void setChangeType(Integer changeType)
    {
        this.changeType = changeType;
    }

    public Integer getChangeType()
    {
        return changeType;
    }

    public void setChangeQuantity(Integer changeQuantity)
    {
        this.changeQuantity = changeQuantity;
    }

    public Integer getChangeQuantity()
    {
        return changeQuantity;
    }

    public void setBeforeQuantity(Integer beforeQuantity)
    {
        this.beforeQuantity = beforeQuantity;
    }

    public Integer getBeforeQuantity()
    {
        return beforeQuantity;
    }

    public void setAfterQuantity(Integer afterQuantity)
    {
        this.afterQuantity = afterQuantity;
    }

    public Integer getAfterQuantity()
    {
        return afterQuantity;
    }

    public void setChangeTime(Date changeTime)
    {
        this.changeTime = changeTime;
    }

    public Date getChangeTime()
    {
        return changeTime;
    }

    public void setOperator(String operator)
    {
        this.operator = operator;
    }

    public String getOperator()
    {
        return operator;
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
            .append("changeType", getChangeType())
            .append("changeQuantity", getChangeQuantity())
            .append("beforeQuantity", getBeforeQuantity())
            .append("afterQuantity", getAfterQuantity())
            .append("changeTime", getChangeTime())
            .append("operator", getOperator())
            .append("createTime", getCreateTime())
            .toString();
    }
}
