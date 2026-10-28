package com.dkd.app.domain;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 库存变动流水对象 tb_inventory_log（库存审计的唯一来源）。
 *
 * <p>列名以 2026-10-01 实测结构为准（docs/ddl/legacy_tables_tb_order_tb_inventory_log.md，共 14 列）：
 * id/vm_id/sku_id/channel_id/change_type/change_amount/before_stock/after_stock/order_id/
 * business_type/operator_id/operator_name/create_time/remark。
 * 表里没有 change_quantity/before_quantity/after_quantity/change_time/operator；
 * 表里也没有 inner_code，按设备编号检索需 join tb_vending_machine。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@Data
@TableName("tb_inventory_log")
public class InventoryLog implements Serializable {

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

    /** 业务类型：补货 */
    public static final String BUSINESS_TYPE_RESTOCK = "RESTOCK";

    /** 主键 */
    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    /** 售货机Id */
    @TableField("vm_id")
    private Long vmId;

    /** 商品Id */
    @TableField("sku_id")
    private Long skuId;

    /** 货道Id */
    @TableField("channel_id")
    private Long channelId;

    /** 变动类型：1-销售 2-补货 3-损耗 4-调整 5-盘点 */
    @TableField("change_type")
    private Integer changeType;

    /** 变动数量（正数为增加，负数为减少） */
    @TableField("change_amount")
    private Integer changeAmount;

    /** 变动前库存 */
    @TableField("before_stock")
    private Integer beforeStock;

    /** 变动后库存 */
    @TableField("after_stock")
    private Integer afterStock;

    /** 关联订单Id（销售类型时） */
    @TableField("order_id")
    private Long orderId;

    /** 业务类型（RESTOCK/ADJUST/INIT/RECONCILE 等字符串） */
    @TableField("business_type")
    private String businessType;

    /** 操作人Id */
    @TableField("operator_id")
    private Long operatorId;

    /** 操作人姓名 */
    @TableField("operator_name")
    private String operatorName;

    /** 创建时间 */
    @TableField("create_time")
    private LocalDateTime createTime;

    /** 备注 */
    private String remark;
}
