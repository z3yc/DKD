package com.dkd.app.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.dkd.app.domain.Inventory;

/**
 * 库存变更服务（运维端库存回写的**唯一入口**）。
 *
 * <p>清晰版口径（docs/DKD业务规范-清晰版.md §2.2 / §4.3）要求库存变更必须
 * "校验范围 → 更新台账 → 写流水"在**同一事务**内完成，禁止任何地方直接 update 台账；
 * 运维端补货完成时只允许经本服务回写。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
public interface InventoryStockService extends IService<Inventory> {

    /**
     * 补货回写：按货道回写库存台账（current_stock / is_alert / last_supply_time）并写库存流水。
     *
     * <p>实现约定：
     * <ol>
     *   <li>先按 (vm_id, channel_id) 查台账；未命中回退 (vm_id, sku_id)（兼容 Phase B 之前的
     *       旧唯一键 uk_vm_sku）；仍未命中则插入一条新台账；</li>
     *   <li>补货量按剩余容量夹取：after = min(before + quantity, 容量)，容量取台账 max_stock，
     *       为空再取货道 max_capacity；流水记**实际增量**；</li>
     *   <li>重算 is_alert = (after &lt;= min_stock)，更新 last_supply_time；</li>
     *   <li>写 tb_inventory_log：change_type=2、business_type=RESTOCK、before/after、operator_name。</li>
     * </ol>
     *
     * @param vmId 售货机Id
     * @param channelId 货道Id（库存粒度权威，优先按它定位台账）
     * @param skuId 商品Id（旧唯一键回退用）
     * @param quantity 计划补货量（实际增量可能因容量夹取而变小）
     * @param operator 操作人姓名（写 operator_name；为空时记"系统"）
     * @return 是否成功（业务失败抛 LogicException，不会返回 false 后静默提交）
     */
    boolean applyRestock(Long vmId, Long channelId, Long skuId, Integer quantity, String operator);
}
