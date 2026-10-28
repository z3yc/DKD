package com.dkd.manage.domain;

import org.apache.commons.lang3.builder.ToStringBuilder;
import org.apache.commons.lang3.builder.ToStringStyle;
import com.dkd.common.annotation.Excel;
import com.dkd.common.core.domain.BaseEntity;

/**
 * 库存变动日志对象 tb_inventory_log
 *
 * <p>字段与真实表列一一对应（2026-10-01 实测导出，见 docs/ddl/legacy_tables_tb_order_tb_inventory_log.md）：
 * id/vm_id/sku_id/channel_id/change_type/change_amount/before_stock/after_stock/order_id/
 * business_type/operator_id/operator_name/create_time/remark。
 * 历史代码里的 change_quantity/before_quantity/after_quantity/change_time/operator 在表中并不存在，
 * 已按清晰版口径（docs/DKD业务规范-清晰版.md §4.3）删除；流水时间统一用 create_time。
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public class InventoryLog extends BaseEntity
{
    private static final long serialVersionUID = 1L;

    /** 变动类型：1-销售 */
    public static final Integer CHANGE_TYPE_SALE = 1;

    /** 变动类型：2-补货 */
    public static final Integer CHANGE_TYPE_RESTOCK = 2;

    /** 变动类型：3-损耗 */
    public static final Integer CHANGE_TYPE_LOSS = 3;

    /** 变动类型：4-调整（含初始化、对账） */
    public static final Integer CHANGE_TYPE_ADJUST = 4;

    /** 变动类型：5-盘点 */
    public static final Integer CHANGE_TYPE_STOCKTAKE = 5;

    /** 业务类型：销售 */
    public static final String BUSINESS_TYPE_SALE = "SALE";

    /** 业务类型：补货 */
    public static final String BUSINESS_TYPE_RESTOCK = "RESTOCK";

    /** 业务类型：损耗 */
    public static final String BUSINESS_TYPE_LOSS = "LOSS";

    /** 业务类型：调整 */
    public static final String BUSINESS_TYPE_ADJUST = "ADJUST";

    /** 业务类型：盘点 */
    public static final String BUSINESS_TYPE_STOCKTAKE = "STOCKTAKE";

    /** 业务类型：初始化（change_type=4 的子场景） */
    public static final String BUSINESS_TYPE_INIT = "INIT";

    /** 业务类型：对账校准（change_type=4 的子场景，Phase B 定时任务使用） */
    public static final String BUSINESS_TYPE_RECONCILE = "RECONCILE";

    /** 操作人：系统（初始化/定时任务等无人操作的变更） */
    public static final String OPERATOR_SYSTEM = "系统";

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

    /** 变动类型：1-销售 2-补货 3-损耗 4-调整 5-盘点 */
    @Excel(name = "变动类型", readConverterExp = "1=销售,2=补货,3=损耗,4=调整,5=盘点")
    private Integer changeType;

    /** 变动数量（正数为增加，负数为减少） */
    @Excel(name = "变动数量")
    private Integer changeAmount;

    /** 变动前库存 */
    @Excel(name = "变动前库存")
    private Integer beforeStock;

    /** 变动后库存 */
    @Excel(name = "变动后库存")
    private Integer afterStock;

    /** 关联订单Id（销售类型时） */
    @Excel(name = "关联订单Id")
    private Long orderId;

    /** 业务类型（SALE/RESTOCK/LOSS/ADJUST/INIT/RECONCILE 等字符串） */
    @Excel(name = "业务类型")
    private String businessType;

    /** 操作人Id（后台/运维人员 id，系统任务可为空） */
    private Long operatorId;

    /** 操作人姓名 */
    @Excel(name = "操作人")
    private String operatorName;

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

    public void setChangeAmount(Integer changeAmount)
    {
        this.changeAmount = changeAmount;
    }

    public Integer getChangeAmount()
    {
        return changeAmount;
    }

    public void setBeforeStock(Integer beforeStock)
    {
        this.beforeStock = beforeStock;
    }

    public Integer getBeforeStock()
    {
        return beforeStock;
    }

    public void setAfterStock(Integer afterStock)
    {
        this.afterStock = afterStock;
    }

    public Integer getAfterStock()
    {
        return afterStock;
    }

    public void setOrderId(Long orderId)
    {
        this.orderId = orderId;
    }

    public Long getOrderId()
    {
        return orderId;
    }

    public void setBusinessType(String businessType)
    {
        this.businessType = businessType;
    }

    public String getBusinessType()
    {
        return businessType;
    }

    public void setOperatorId(Long operatorId)
    {
        this.operatorId = operatorId;
    }

    public Long getOperatorId()
    {
        return operatorId;
    }

    public void setOperatorName(String operatorName)
    {
        this.operatorName = operatorName;
    }

    public String getOperatorName()
    {
        return operatorName;
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
            .append("changeAmount", getChangeAmount())
            .append("beforeStock", getBeforeStock())
            .append("afterStock", getAfterStock())
            .append("orderId", getOrderId())
            .append("businessType", getBusinessType())
            .append("operatorId", getOperatorId())
            .append("operatorName", getOperatorName())
            .append("createTime", getCreateTime())
            .append("remark", getRemark())
            .toString();
    }
}
