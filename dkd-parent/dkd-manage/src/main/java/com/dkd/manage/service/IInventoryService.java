package com.dkd.manage.service;

import java.util.List;
import com.dkd.manage.domain.Inventory;
import com.dkd.manage.domain.dto.InventoryAlertDto;
import com.dkd.manage.domain.dto.RestockSuggestionDto;

/**
 * 库存Service接口
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public interface IInventoryService
{
    /**
     * 查询库存
     *
     * @param id 库存主键
     * @return 库存
     */
    public Inventory selectInventoryById(Long id);

    /**
     * 查询库存列表
     *
     * @param inventory 库存
     * @return 库存集合
     */
    public List<Inventory> selectInventoryList(Inventory inventory);

    /**
     * 新增库存
     *
     * @param inventory 库存
     * @return 结果
     */
    public int insertInventory(Inventory inventory);

    /**
     * 修改库存
     *
     * @param inventory 库存
     * @return 结果
     */
    public int updateInventory(Inventory inventory);

    /**
     * 批量删除库存
     *
     * @param ids 需要删除的库存主键集合
     * @return 结果
     */
    public int deleteInventoryByIds(Long[] ids);

    /**
     * 删除库存信息
     *
     * @param id 库存主键
     * @return 结果
     */
    public int deleteInventoryById(Long id);

    /**
     * 根据售货机ID、商品ID、货道ID查询库存
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @return 库存信息
     */
    public Inventory selectByVmIdAndSkuIdAndChannelId(Long vmId, Long skuId, Long channelId);

    /**
     * 查询低库存预警列表
     *
     * @return 低库存列表
     */
    public List<Inventory> selectLowInventoryList();

    /**
     * 查询缺货库存列表
     *
     * @return 缺货列表
     */
    public List<Inventory> selectOutOfStockList();

    /**
     * 批量插入库存
     *
     * @param inventoryList 库存列表
     * @return 结果
     */
    public int batchInsertInventory(List<Inventory> inventoryList);

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
    public int updateInventoryQuantity(Long vmId, Long skuId, Long channelId, Integer changeQuantity, Integer changeType, String operator);

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
    public int initInventory(Long vmId, Long skuId, Long channelId, Integer quantity, Integer minQuantity, Integer maxQuantity);

    /**
     * 生成库存预警列表
     *
     * @return 预警列表
     */
    public List<InventoryAlertDto> generateInventoryAlerts();

    /**
     * 检查库存并发送预警
     *
     * @param inventory 库存信息
     * @return 预警DTO，如果没有预警则返回null
     */
    public InventoryAlertDto checkInventoryAlert(Inventory inventory);

    /**
     * 生成补货建议列表
     *
     * @return 补货建议列表
     */
    public List<RestockSuggestionDto> generateRestockSuggestions();

    /**
     * 为指定库存生成补货建议
     *
     * @param inventory 库存信息
     * @return 补货建议
     */
    public RestockSuggestionDto generateRestockSuggestion(Inventory inventory);
}
