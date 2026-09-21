package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;

import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.InventoryLog;
import com.dkd.manage.domain.dto.InventoryAlertDto;
import com.dkd.manage.domain.dto.RestockSuggestionDto;
import com.dkd.manage.mapper.InventoryLogMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.dkd.manage.mapper.InventoryMapper;
import com.dkd.manage.domain.Inventory;
import com.dkd.manage.service.IInventoryService;

/**
 * 库存Service业务层处理
 *
 * @author ruoyi
 * @date 2025-01-08
 */
@Service
public class InventoryServiceImpl implements IInventoryService
{
    @Autowired
    private InventoryMapper inventoryMapper;

    @Autowired
    private InventoryLogMapper inventoryLogMapper;

    /**
     * 查询库存
     *
     * @param id 库存主键
     * @return 库存
     */
    @Override
    public Inventory selectInventoryById(Long id)
    {
        return inventoryMapper.selectInventoryById(id);
    }

    /**
     * 查询库存列表
     *
     * @param inventory 库存
     * @return 库存
     */
    @Override
    public List<Inventory> selectInventoryList(Inventory inventory)
    {
        return inventoryMapper.selectInventoryList(inventory);
    }

    /**
     * 新增库存
     *
     * @param inventory 库存
     * @return 结果
     */
    @Override
    public int insertInventory(Inventory inventory)
    {
        // 检查是否已存在库存记录
        if (inventory.getVmId() != null && inventory.getSkuId() != null && inventory.getChannelId() != null)
        {
            Inventory existInventory = inventoryMapper.selectByVmIdAndSkuIdAndChannelId(
                inventory.getVmId(),
                inventory.getSkuId(),
                inventory.getChannelId()
            );
            if (existInventory != null)
            {
                throw new RuntimeException("该售货机货道已存在该商品的库存记录，请使用修改操作");
            }
        }

        inventory.setCreateTime(DateUtils.getNowDate());
        return inventoryMapper.insertInventory(inventory);
    }

    /**
     * 修改库存
     *
     * @param inventory 库存
     * @return 结果
     */
    @Override
    public int updateInventory(Inventory inventory)
    {
        inventory.setUpdateTime(DateUtils.getNowDate());
        return inventoryMapper.updateInventory(inventory);
    }

    /**
     * 批量删除库存
     *
     * @param ids 需要删除的库存主键
     * @return 结果
     */
    @Override
    public int deleteInventoryByIds(Long[] ids)
    {
        return inventoryMapper.deleteInventoryByIds(ids);
    }

    /**
     * 删除库存信息
     *
     * @param id 库存主键
     * @return 结果
     */
    @Override
    public int deleteInventoryById(Long id)
    {
        return inventoryMapper.deleteInventoryById(id);
    }

    /**
     * 根据售货机ID、商品ID、货道ID查询库存
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @return 库存信息
     */
    @Override
    public Inventory selectByVmIdAndSkuIdAndChannelId(Long vmId, Long skuId, Long channelId)
    {
        return inventoryMapper.selectByVmIdAndSkuIdAndChannelId(vmId, skuId, channelId);
    }

    /**
     * 查询低库存预警列表
     *
     * @return 低库存列表
     */
    @Override
    public List<Inventory> selectLowInventoryList()
    {
        return inventoryMapper.selectLowInventoryList();
    }

    /**
     * 查询缺货库存列表
     *
     * @return 缺货列表
     */
    @Override
    public List<Inventory> selectOutOfStockList()
    {
        return inventoryMapper.selectOutOfStockList();
    }

    /**
     * 批量插入库存
     *
     * @param inventoryList 库存列表
     * @return 结果
     */
    @Override
    public int batchInsertInventory(List<Inventory> inventoryList)
    {
        if (inventoryList == null || inventoryList.isEmpty())
        {
            return 0;
        }
        Date now = DateUtils.getNowDate();
        for (Inventory inventory : inventoryList)
        {
            inventory.setCreateTime(now);
        }
        return inventoryMapper.batchInsertInventory(inventoryList);
    }

    /**
     * 更新库存数量（同时记录日志）
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @param changeQuantity 变动数量（正数为增加，负数为减少）
     * @param changeType 变动类型：1-入库 2-出库 3-补货 4-调整
     * @param operator 操作人
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int updateInventoryQuantity(Long vmId, Long skuId, Long channelId, Integer changeQuantity, Integer changeType, String operator)
    {
        if (changeQuantity == null || changeQuantity == 0)
        {
            return 0;
        }

        // 查询当前库存
        Inventory inventory = inventoryMapper.selectByVmIdAndSkuIdAndChannelId(vmId, skuId, channelId);
        if (inventory == null)
        {
            throw new RuntimeException("库存信息不存在");
        }

        Integer beforeQuantity = inventory.getQuantity();
        Integer afterQuantity = beforeQuantity + changeQuantity;

        // 检查库存是否足够
        if (afterQuantity < 0)
        {
            throw new RuntimeException("库存不足，当前库存：" + beforeQuantity + "，变动数量：" + changeQuantity);
        }

        // 检查是否超过最大库存
        if (inventory.getMaxQuantity() != null && afterQuantity > inventory.getMaxQuantity())
        {
            throw new RuntimeException("库存超过最大容量，最大容量：" + inventory.getMaxQuantity() + "，变动后库存：" + afterQuantity);
        }

        // 更新库存
        inventory.setQuantity(afterQuantity);
        inventory.setUpdateTime(DateUtils.getNowDate());

        // 如果是补货操作，更新上次补货时间
        if (changeType == 3)
        {
            inventory.setLastSupplyTime(DateUtils.getNowDate());
        }

        int result = inventoryMapper.updateInventory(inventory);

        // 记录库存变动日志
        if (result > 0)
        {
            InventoryLog log = new InventoryLog();
            log.setVmId(vmId);
            log.setSkuId(skuId);
            log.setChannelId(channelId);
            log.setChangeType(changeType);
            log.setChangeQuantity(changeQuantity);
            log.setBeforeQuantity(beforeQuantity);
            log.setAfterQuantity(afterQuantity);
            log.setChangeTime(DateUtils.getNowDate());
            log.setOperator(operator);
            log.setCreateTime(DateUtils.getNowDate());

            inventoryLogMapper.insertInventoryLog(log);
        }

        return result;
    }

    /**
     * 初始化库存（用于新设备或新商品）
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @param quantity 初始库存数量
     * @param minQuantity 最小库存预警值
     * @param maxQuantity 最大库存容量
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int initInventory(Long vmId, Long skuId, Long channelId, Integer quantity, Integer minQuantity, Integer maxQuantity)
    {
        // 检查是否已存在库存记录
        Inventory existInventory = inventoryMapper.selectByVmIdAndSkuIdAndChannelId(vmId, skuId, channelId);
        if (existInventory != null)
        {
            throw new RuntimeException("库存记录已存在，请使用更新操作");
        }

        // 创建库存记录
        Inventory inventory = new Inventory();
        inventory.setVmId(vmId);
        inventory.setSkuId(skuId);
        inventory.setChannelId(channelId);
        inventory.setQuantity(quantity);
        inventory.setMinQuantity(minQuantity);
        inventory.setMaxQuantity(maxQuantity);
        inventory.setLastSupplyTime(DateUtils.getNowDate());
        inventory.setCreateTime(DateUtils.getNowDate());

        int result = inventoryMapper.insertInventory(inventory);

        // 记录初始化日志
        if (result > 0)
        {
            InventoryLog log = new InventoryLog();
            log.setVmId(vmId);
            log.setSkuId(skuId);
            log.setChannelId(channelId);
            log.setChangeType(1); // 初始化视为入库
            log.setChangeQuantity(quantity);
            log.setBeforeQuantity(0);
            log.setAfterQuantity(quantity);
            log.setChangeTime(DateUtils.getNowDate());
            log.setOperator("系统");
            log.setCreateTime(DateUtils.getNowDate());

            inventoryLogMapper.insertInventoryLog(log);
        }

        return result;
    }

    /**
     * 生成库存预警列表
     *
     * @return 预警列表
     */
    @Override
    public List<InventoryAlertDto> generateInventoryAlerts()
    {
        List<InventoryAlertDto> alerts = new ArrayList<>();

        // 获取所有低库存
        List<Inventory> lowInventoryList = inventoryMapper.selectLowInventoryList();
        for (Inventory inventory : lowInventoryList)
        {
            InventoryAlertDto alert = checkInventoryAlert(inventory);
            if (alert != null)
            {
                alerts.add(alert);
            }
        }

        // 获取所有缺货
        List<Inventory> outOfStockList = inventoryMapper.selectOutOfStockList();
        for (Inventory inventory : outOfStockList)
        {
            InventoryAlertDto alert = checkInventoryAlert(inventory);
            if (alert != null)
            {
                alerts.add(alert);
            }
        }

        return alerts;
    }

    /**
     * 检查库存并发送预警
     *
     * @param inventory 库存信息
     * @return 预警DTO，如果没有预警则返回null
     */
    @Override
    public InventoryAlertDto checkInventoryAlert(Inventory inventory)
    {
        if (inventory == null || inventory.getQuantity() == null)
        {
            return null;
        }

        Integer quantity = inventory.getQuantity();
        Integer minQuantity = inventory.getMinQuantity();

        // 缺货预警
        if (quantity == 0)
        {
            InventoryAlertDto alert = new InventoryAlertDto();
            alert.setAlertType(2); // 缺货预警
            alert.setAlertLevel(3); // 严重
            alert.setInventory(inventory);
            alert.setAlertMessage("【严重】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】已缺货");
            alert.setSuggestion("请立即安排补货，避免影响销售");
            return alert;
        }

        // 低库存预警
        if (minQuantity != null && quantity <= minQuantity)
        {
            InventoryAlertDto alert = new InventoryAlertDto();
            alert.setAlertType(1); // 低库存预警

            // 计算预警级别
            double ratio = (double) quantity / minQuantity;
            if (ratio <= 0.5)
            {
                alert.setAlertLevel(3); // 严重：库存低于预警值的50%
                alert.setAlertMessage("【严重】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】库存严重不足，当前库存：" + quantity);
                alert.setSuggestion("请尽快安排补货");
            }
            else if (ratio <= 0.8)
            {
                alert.setAlertLevel(2); // 紧急：库存低于预警值的80%
                alert.setAlertMessage("【紧急】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】库存偏低，当前库存：" + quantity);
                alert.setSuggestion("建议尽快补货");
            }
            else
            {
                alert.setAlertLevel(1); // 普通：库存达到预警值
                alert.setAlertMessage("【提醒】售货机【" + inventory.getInnerCode() + "】的商品【" + inventory.getSkuName() + "】库存达到预警值，当前库存：" + quantity);
                alert.setSuggestion("建议及时补货");
            }

            alert.setInventory(inventory);
            return alert;
        }

        return null;
    }

    /**
     * 生成补货建议列表
     *
     * @return 补货建议列表
     */
    @Override
    public List<RestockSuggestionDto> generateRestockSuggestions()
    {
        List<RestockSuggestionDto> suggestions = new ArrayList<>();

        // 获取所有低库存和缺货
        List<Inventory> lowInventoryList = inventoryMapper.selectLowInventoryList();
        List<Inventory> outOfStockList = inventoryMapper.selectOutOfStockList();

        // 合并列表
        lowInventoryList.addAll(outOfStockList);

        for (Inventory inventory : lowInventoryList)
        {
            RestockSuggestionDto suggestion = generateRestockSuggestion(inventory);
            if (suggestion != null)
            {
                suggestions.add(suggestion);
            }
        }

        return suggestions;
    }

    /**
     * 为指定库存生成补货建议
     *
     * @param inventory 库存信息
     * @return 补货建议
     */
    @Override
    public RestockSuggestionDto generateRestockSuggestion(Inventory inventory)
    {
        if (inventory == null || inventory.getMaxQuantity() == null)
        {
            return null;
        }

        Integer currentQuantity = inventory.getQuantity();
        Integer maxQuantity = inventory.getMaxQuantity();

        // 如果库存已经达到最大容量的90%，不需要补货
        if (currentQuantity >= maxQuantity * 0.9)
        {
            return null;
        }

        RestockSuggestionDto suggestion = new RestockSuggestionDto();
        suggestion.setVmId(inventory.getVmId());
        suggestion.setInnerCode(inventory.getInnerCode());
        suggestion.setSkuId(inventory.getSkuId());
        suggestion.setSkuName(inventory.getSkuName());
        suggestion.setChannelId(inventory.getChannelId());
        suggestion.setChannelCode(inventory.getChannelCode());
        suggestion.setCurrentQuantity(currentQuantity);
        suggestion.setMaxCapacity(maxQuantity);

        // 计算建议补货数量（补到最大容量的80%-90%，留10%-20%的安全空间）
        Integer targetQuantity = (int) (maxQuantity * 0.85);
        Integer suggestedQuantity = targetQuantity - currentQuantity;

        // 至少补货到最小预警值的2倍
        if (inventory.getMinQuantity() != null)
        {
            Integer minRestock = inventory.getMinQuantity() * 2 - currentQuantity;
            if (minRestock > suggestedQuantity)
            {
                suggestedQuantity = minRestock;
            }
        }

        // 确保不超过最大容量
        if (currentQuantity + suggestedQuantity > maxQuantity)
        {
            suggestedQuantity = maxQuantity - currentQuantity;
        }

        suggestion.setSuggestedQuantity(suggestedQuantity);
        suggestion.setAfterRestockQuantity(currentQuantity + suggestedQuantity);

        // 计算优先级
        if (currentQuantity == 0)
        {
            suggestion.setPriority(4); // 紧急：缺货
            suggestion.setReason("商品已缺货，需要立即补货");
            suggestion.setEstimatedDays(0);
        }
        else if (inventory.getMinQuantity() != null && currentQuantity <= inventory.getMinQuantity() * 0.5)
        {
            suggestion.setPriority(3); // 高：库存严重不足
            suggestion.setReason("库存低于预警值的50%，建议尽快补货");
            suggestion.setEstimatedDays(1);
        }
        else if (inventory.getMinQuantity() != null && currentQuantity <= inventory.getMinQuantity())
        {
            suggestion.setPriority(2); // 中：库存偏低
            suggestion.setReason("库存达到预警值，建议及时补货");
            suggestion.setEstimatedDays(2);
        }
        else
        {
            suggestion.setPriority(1); // 低：常规补货
            suggestion.setReason("常规补货，保持合理库存水平");
            suggestion.setEstimatedDays(3);
        }

        return suggestion;
    }
}
