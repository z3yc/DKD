package com.dkd.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.dkd.app.common.exception.LogicException;
import com.dkd.app.domain.Channel;
import com.dkd.app.domain.Inventory;
import com.dkd.app.domain.InventoryLog;
import com.dkd.app.mapper.InventoryLogMapper;
import com.dkd.app.mapper.InventoryMapper;
import com.dkd.app.service.ChannelService;
import com.dkd.app.service.InventoryStockService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

/**
 * 库存变更服务实现（运维端补货回写的唯一入口）。
 *
 * <p>为什么要有这个类：历史实现里运维端补货只写 tb_channel.current_capacity，
 * 后台预警/建议读的却是 tb_inventory.current_stock —— 补货永远消不掉预警
 * （docs/DKD业务全景梳理.md 的 P0 断点）。现在补货完成时同事务双写：
 * 台账（数量事实）+ 货道缓存（现场读数），并落 tb_inventory_log 审计。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@Service
@Slf4j
public class InventoryStockServiceImpl extends ServiceImpl<InventoryMapper, Inventory> implements InventoryStockService {

    @Autowired
    private InventoryLogMapper inventoryLogMapper;

    @Autowired
    private ChannelService channelService;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean applyRestock(Long vmId, Long channelId, Long skuId, Integer quantity, String operator) {
        if (vmId == null || channelId == null) {
            throw new LogicException("库存回写失败：售货机Id与货道Id不能为空");
        }
        if (quantity == null || quantity <= 0) {
            throw new LogicException("库存回写失败：补货数量必须大于 0");
        }

        LocalDateTime now = LocalDateTime.now();
        String operatorName = (operator == null || operator.trim().isEmpty()) ? "系统" : operator.trim();

        // 1) 定位台账：货道优先（库存粒度以货道为准），未命中回退旧唯一键 (vm_id, sku_id)
        Inventory inventory = selectByVmIdAndChannelId(vmId, channelId);
        if (inventory == null && skuId != null) {
            inventory = selectByVmIdAndSkuId(vmId, skuId);
        }

        if (inventory == null) {
            return insertAndLog(vmId, channelId, skuId, quantity, operatorName, now);
        }
        return updateAndLog(inventory, channelId, skuId, quantity, operatorName, now);
    }

    /**
     * 台账缺行：插入一条并写补货流水。
     *
     * <p>为什么允许插入而不是报错：Phase B（改 uk_vm_channel、补数据）未执行前，
     * 大量货道在 tb_inventory 里没有行（实测 272 个货道只有 3 行库存），
     * 若直接报错则运维端补货永远无法完成；插入时 max_stock 同步为货道容量。
     */
    private boolean insertAndLog(Long vmId, Long channelId, Long skuId, Integer quantity,
                                 String operatorName, LocalDateTime now) {
        if (skuId == null) {
            throw new LogicException("库存回写失败：货道未绑定商品，无法建立库存台账");
        }

        Integer channelCapacity = resolveChannelCapacity(channelId);
        if (channelCapacity != null && channelCapacity <= 0) {
            throw new LogicException("库存回写失败：货道容量为 " + channelCapacity + "，无法补货");
        }

        // 实际入库量按容量夹取（清晰版 §2.2：不允许超过容量）
        int initialStock = quantity;
        if (channelCapacity != null && initialStock > channelCapacity) {
            initialStock = channelCapacity;
        }

        Inventory inventory = new Inventory();
        inventory.setVmId(vmId);
        inventory.setSkuId(skuId);
        inventory.setChannelId(channelId);
        inventory.setQuantity(initialStock);
        // 新台账没有预警阈值，按 0 起步（现场可通过库存初始化/编辑调整）
        inventory.setMinQuantity(0);
        // max_stock 是容量的同步冗余（清晰版 §1：容量权威在 tb_channel.max_capacity）
        inventory.setMaxQuantity(channelCapacity);
        inventory.setIsAlert(buildAlertFlag(initialStock, 0));
        inventory.setLastSupplyTime(now);
        inventory.setCreateTime(now);
        inventory.setUpdateTime(now);

        if (!this.save(inventory)) {
            throw new LogicException("库存回写失败：库存台账写入失败");
        }

        writeLog(vmId, channelId, skuId, initialStock, 0, initialStock, operatorName, now);
        log.info("库存台账缺失已补建并记补货流水 vmId={} channelId={} skuId={} afterStock={}",
                vmId, channelId, skuId, initialStock);
        return true;
    }

    /**
     * 台账已存在：夹取补货量 → 更新数量事实列 → 写补货流水。
     *
     * <p>channelId/skuId 入参只作为"台账行缺列"时的兜底：经旧唯一键 (vm_id, sku_id) 回退命中的
     * 历史行可能 channel_id 为空，此时流水仍要能写明这次补货是哪个货道。
     */
    private boolean updateAndLog(Inventory inventory, Long channelId, Long skuId, Integer quantity,
                                 String operatorName, LocalDateTime now) {
        int beforeStock = inventory.getQuantity() == null ? 0 : inventory.getQuantity();

        // 容量取台账 max_stock；为空时回退货道 max_capacity（清晰版 §4.4 的容量权威）
        Integer maxStock = inventory.getMaxQuantity();
        if (maxStock == null) {
            // 历史行可能 channel_id 为空：优先用台账行的货道，其次用本次请求的货道
            Long capacityChannelId = inventory.getChannelId() != null ? inventory.getChannelId() : channelId;
            maxStock = resolveChannelCapacity(capacityChannelId);
        }

        long targetStock = (long) beforeStock + quantity;
        if (maxStock != null && targetStock > maxStock) {
            // 实补量 = min(计划量, 容量 - 当前数)，流水记实际数量（清晰版 §2.2）
            targetStock = maxStock;
        }
        if (targetStock > Integer.MAX_VALUE) {
            throw new LogicException("库存回写失败：补货后库存超出可表示范围");
        }

        int afterStock = (int) targetStock;
        int actualAmount = afterStock - beforeStock;
        if (actualAmount <= 0) {
            // 已满容量时补货：库存没有实际变化，不写 change_amount=0 的流水（避免审计噪声），
            // 但仍返回成功——设备侧货道缓存/补货时间由调用方处理，这里只负责台账口径。
            log.warn("补货未产生库存增量，跳过台账更新与流水 vmId={} channelId={} beforeStock={} 计划补货量={}",
                    inventory.getVmId(), inventory.getChannelId(), beforeStock, quantity);
            return true;
        }

        Integer isAlert = buildAlertFlag(afterStock, inventory.getMinQuantity());

        // 只更新数量事实列，不用 updateById 整行回写：避免把 sku_id/channel_id/max_stock
        // 这些"档案列"按读到的旧值再写一遍（清晰版 §1 规定档案权威在 tb_channel）
        LambdaUpdateWrapper<Inventory> updateWrapper = new LambdaUpdateWrapper<>();
        updateWrapper.eq(Inventory::getId, inventory.getId())
                .set(Inventory::getQuantity, afterStock)
                .set(Inventory::getIsAlert, isAlert)
                .set(Inventory::getLastSupplyTime, now)
                .set(Inventory::getUpdateTime, now);
        if (!this.update(updateWrapper)) {
            throw new LogicException("库存回写失败：库存台账更新失败，库存ID=" + inventory.getId());
        }

        // 流水要落在"台账行"的身份上（before/after 就是该行的读数）；
        // 历史行缺 channel_id/sku_id 时用本次补货请求的货道/商品兜底。
        Long effectiveVmId = inventory.getVmId();
        Long effectiveChannelId = inventory.getChannelId() != null ? inventory.getChannelId() : channelId;
        Long effectiveSkuId = inventory.getSkuId() != null ? inventory.getSkuId() : skuId;
        writeLog(effectiveVmId, effectiveChannelId, effectiveSkuId,
                actualAmount, beforeStock, afterStock, operatorName, now);
        log.info("补货回写库存台账成功 vmId={} channelId={} beforeStock={} afterStock={} actualAmount={}",
                effectiveVmId, effectiveChannelId, beforeStock, afterStock, actualAmount);
        return true;
    }

    /**
     * 写库存流水（真实列：change_amount/before_stock/after_stock/business_type/operator_name/create_time）。
     */
    private void writeLog(Long vmId, Long channelId, Long skuId, int changeAmount,
                          int beforeStock, int afterStock, String operatorName, LocalDateTime now) {
        InventoryLog inventoryLog = new InventoryLog();
        inventoryLog.setVmId(vmId);
        inventoryLog.setSkuId(skuId);
        inventoryLog.setChannelId(channelId);
        inventoryLog.setChangeType(InventoryLog.CHANGE_TYPE_RESTOCK);
        inventoryLog.setChangeAmount(changeAmount);
        inventoryLog.setBeforeStock(beforeStock);
        inventoryLog.setAfterStock(afterStock);
        inventoryLog.setBusinessType(InventoryLog.BUSINESS_TYPE_RESTOCK);
        inventoryLog.setOperatorName(operatorName);
        inventoryLog.setCreateTime(now);
        if (inventoryLogMapper.insert(inventoryLog) <= 0) {
            throw new LogicException("库存回写失败：库存流水写入失败");
        }
    }

    /**
     * 按 (vm_id, channel_id) 查台账；limit 1 防止历史脏数据（同货道多行）抛 TooManyResultsException。
     */
    private Inventory selectByVmIdAndChannelId(Long vmId, Long channelId) {
        LambdaQueryWrapper<Inventory> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(Inventory::getVmId, vmId)
                .eq(Inventory::getChannelId, channelId)
                .orderByAsc(Inventory::getId)
                .last("limit 1");
        return this.getOne(queryWrapper, false);
    }

    /**
     * 按 (vm_id, sku_id) 查台账（兼容 Phase B 之前的旧唯一键 uk_vm_sku）。
     */
    private Inventory selectByVmIdAndSkuId(Long vmId, Long skuId) {
        LambdaQueryWrapper<Inventory> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(Inventory::getVmId, vmId)
                .eq(Inventory::getSkuId, skuId)
                .orderByAsc(Inventory::getId)
                .last("limit 1");
        return this.getOne(queryWrapper, false);
    }

    /**
     * 取货道容量（容量权威在 tb_channel.max_capacity）。
     *
     * @return 货道容量；货道不存在时返回 null（由上游 supply 的货道存在性校验兜底）
     */
    private Integer resolveChannelCapacity(Long channelId) {
        if (channelId == null) {
            return null;
        }
        Channel channel = channelService.getById(channelId);
        if (channel == null) {
            log.warn("货道不存在，无法读取容量 channelId={}", channelId);
            return null;
        }
        return channel.getMaxCapacity();
    }

    /**
     * is_alert = (current_stock &lt;= min_stock)；min_stock 为空按 0 处理（缺货必须预警）。
     */
    private Integer buildAlertFlag(Integer currentStock, Integer minStock) {
        if (currentStock == null) {
            return 0;
        }
        int threshold = minStock == null ? 0 : minStock;
        return currentStock <= threshold ? 1 : 0;
    }
}
