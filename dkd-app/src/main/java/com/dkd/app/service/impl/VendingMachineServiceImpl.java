package com.dkd.app.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.dkd.app.common.exception.LogicException;
import com.dkd.app.domain.Channel;
import com.dkd.app.domain.TaskDetails;
import com.dkd.app.domain.VendingMachine;
import com.dkd.app.mapper.VendingMachineMapper;
import com.dkd.app.service.ChannelService;
import com.dkd.app.service.InventoryStockService;
import com.dkd.app.service.VendingMachineService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@Slf4j
public class VendingMachineServiceImpl extends ServiceImpl<VendingMachineMapper, VendingMachine> implements VendingMachineService {

    @Autowired
    private ChannelService channelService;

    @Autowired
    private InventoryStockService inventoryStockService;

    // 更改售货机状态
    public boolean updateStatus(String innerCode, Integer status) {
        UpdateWrapper<VendingMachine> uw = new UpdateWrapper<>();
        uw.lambda()
                .eq(VendingMachine::getInnerCode, innerCode)
                .set(VendingMachine::getVmStatus, status);
        return this.update(uw);
    }

    /**
     * 补货完成回写：清晰版 §2.2 的 b/c/d 三件事（a=工单状态由 TaskServiceImpl 负责）。
     *
     * <ol>
     *   <li>货道缓存：current_capacity = min(current_capacity + 补货量, max_capacity)，
     *       同时更新货道与设备的 last_supply_time；</li>
     *   <li>库存台账：逐条调用 {@link InventoryStockService#applyRestock}（台账 + 流水同事务）；</li>
     *   <li>任一条明细失败（空货道号 / 货道不属于设备 / 未绑定商品 / 数量非法）→ 收集后整体抛
     *       LogicException，由调用方事务回滚，**不再静默跳过**。</li>
     * </ol>
     *
     * <p>历史缺陷（本轮修复）：空 channelCode 会被 `if (item.isPresent())` 静默吞掉，
     * 结果工单"完成"了但库存没动；且只写 tb_channel 不写 tb_inventory / tb_inventory_log。
     *
     * @param innerCode 设备编号
     * @param details 补货工单明细
     * @param operator 操作人姓名（工单执行人）
     * @return 是否成功
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean supply(String innerCode, List<TaskDetails> details, String operator) {
        if (details == null || details.isEmpty()) {
            throw new LogicException("补货工单没有明细，无法回写库存");
        }
        if (innerCode == null || innerCode.trim().isEmpty()) {
            throw new LogicException("补货回写失败：设备编号不能为空");
        }

        // 0) 设备必须存在：台账回写需要 vmId；历史数据里 tb_channel.vm_id 可能为空，不能只依赖货道档案
        LambdaQueryWrapper<VendingMachine> vmQuery = new LambdaQueryWrapper<>();
        vmQuery.eq(VendingMachine::getInnerCode, innerCode).last("limit 1");
        VendingMachine vendingMachine = this.getOne(vmQuery, false);
        if (vendingMachine == null) {
            throw new LogicException("设备[" + innerCode + "]不存在，无法回写库存");
        }
        Long vmId = vendingMachine.getId();
        LocalDateTime now = LocalDateTime.now();

        // 1) 货道档案只查一次，后续在内存里筛选（避免逐条明细查库形成 N+1）
        List<Channel> channelList = channelService.getChannelesByInnerCode(innerCode);
        if (channelList == null) {
            channelList = new ArrayList<>();
        }

        // 按货道 id 去重：同一货道出现多条明细时数量已逐条累加，批量更新只需一次
        Map<Long, Channel> channelToUpdate = new LinkedHashMap<>();
        List<String> failures = new ArrayList<>();

        for (TaskDetails detail : details) {
            if (detail == null) {
                failures.add("存在空明细");
                continue;
            }
            String channelCode = detail.getChannelCode() == null ? null : detail.getChannelCode().trim();
            if (channelCode == null || channelCode.isEmpty()) {
                log.warn("补货明细缺少货道编号，收集为失败 innerCode={} detailsId={}", innerCode, detail.getDetailsId());
                failures.add("明细[" + detail.getDetailsId() + "]缺少货道编号");
                continue;
            }
            Integer expectCapacity = detail.getExpectCapacity();
            if (expectCapacity == null || expectCapacity <= 0) {
                failures.add("货道[" + channelCode + "]补货数量不合法");
                continue;
            }

            Channel channel = findChannelByCode(channelList, channelCode);
            if (channel == null) {
                failures.add("货道[" + channelCode + "]不属于设备[" + innerCode + "]");
                continue;
            }
            Long channelSkuId = channel.getSkuId();
            if (channelSkuId == null || channelSkuId <= 0) {
                // tb_channel.sku_id=0 表示空货道（清晰版 §1），空货道不能补货
                failures.add("货道[" + channelCode + "]未绑定商品，无法回写库存");
                continue;
            }

            // 货道缓存：夹取到 max_capacity（历史缺陷：无条件累加会超容量）
            int currentCapacity = channel.getCurrentCapacity() == null ? 0 : channel.getCurrentCapacity();
            long targetCapacity = (long) currentCapacity + expectCapacity;
            Integer maxCapacity = channel.getMaxCapacity();
            if (maxCapacity != null && targetCapacity > maxCapacity) {
                targetCapacity = maxCapacity;
            }
            if (targetCapacity > Integer.MAX_VALUE) {
                failures.add("货道[" + channelCode + "]补货后容量超出可表示范围");
                continue;
            }
            channel.setCurrentCapacity((int) targetCapacity);
            channel.setLastSupplyTime(now);
            channelToUpdate.put(channel.getId(), channel);

            // 库存台账 + 流水（清晰版 §2.2 的 c/d，同事务）
            inventoryStockService.applyRestock(vmId, channel.getId(), channelSkuId, expectCapacity, operator);
        }

        // 空明细/空货道号等失败必须在提交前暴露：整体回滚，工单不完成，错误文案回给运维
        if (!failures.isEmpty()) {
            throw new LogicException("补货回写失败：" + String.join("；", failures));
        }

        // 2) 设备最后补货时间
        LambdaUpdateWrapper<VendingMachine> vmUpdate = new LambdaUpdateWrapper<>();
        vmUpdate.eq(VendingMachine::getId, vmId).set(VendingMachine::getLastSupplyTime, now);
        if (!this.update(vmUpdate)) {
            throw new LogicException("补货回写失败：设备最后补货时间更新失败");
        }

        // 3) 货道缓存批量落库
        if (!channelToUpdate.isEmpty()) {
            if (!channelService.updateBatchById(new ArrayList<>(channelToUpdate.values()))) {
                throw new LogicException("补货回写失败：货道库存更新失败");
            }
        }

        log.info("补货回写完成 innerCode={} vmId={} 明细数={} 更新货道数={} operator={}",
                innerCode, vmId, details.size(), channelToUpdate.size(), operator);
        return true;
    }

    /**
     * 在已查出的货道列表里按货道编号匹配（内存筛选，避免 N+1）。
     *
     * @param channelList 该设备的货道列表
     * @param channelCode 货道编号（已 trim）
     * @return 命中的货道，未命中返回 null
     */
    private Channel findChannelByCode(List<Channel> channelList, String channelCode) {
        for (Channel channel : channelList) {
            if (channel != null && channelCode.equals(channel.getChannelCode())) {
                return channel;
            }
        }
        return null;
    }
}
