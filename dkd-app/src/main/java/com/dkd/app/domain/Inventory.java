package com.dkd.app.domain;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 库存台账对象 tb_inventory（库存"数量"的唯一事实源）。
 *
 * <p>口径见 docs/DKD业务规范-清晰版.md §1：tb_channel 是"卖什么/容量多少"的权威
 * （sku_id、max_capacity），tb_inventory 是数量事实（current_stock、min_stock），
 * max_stock 是容量的同步冗余，is_alert = (current_stock &lt;= min_stock)。
 *
 * <p>字段命名沿用运维端既有习惯：quantity/minQuantity/maxQuantity 分别映射真实列
 * current_stock/min_stock/max_stock（tb_inventory 共这些列 + last_sell_time/is_alert 等）。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@Data
@TableName("tb_inventory")
public class Inventory implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 主键 */
    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    /** 售货机Id */
    @TableField("vm_id")
    private Long vmId;

    /** 商品Id（与货道档案保持一致，0 表示空货道） */
    @TableField("sku_id")
    private Long skuId;

    /** 货道Id */
    @TableField("channel_id")
    private Long channelId;

    /** 当前库存（真实列 current_stock） */
    @TableField("current_stock")
    private Integer quantity;

    /** 最小库存预警值（真实列 min_stock） */
    @TableField("min_stock")
    private Integer minQuantity;

    /** 最大库存容量（真实列 max_stock，容量权威是 tb_channel.max_capacity） */
    @TableField("max_stock")
    private Integer maxQuantity;

    /** 上次补货时间 */
    @TableField("last_supply_time")
    private LocalDateTime lastSupplyTime;

    /** 上次销售时间（仓外系统写入，本端只读） */
    @TableField("last_sell_time")
    private LocalDateTime lastSellTime;

    /** 预警标记：1-预警 0-正常（is_alert = current_stock &lt;= min_stock） */
    @TableField("is_alert")
    private Integer isAlert;

    /** 创建时间 */
    @TableField(value = "create_time", fill = FieldFill.INSERT)
    private LocalDateTime createTime;

    /** 修改时间 */
    @TableField(value = "update_time", fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updateTime;
}
