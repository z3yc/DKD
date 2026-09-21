package com.dkd.manage.mapper;

import java.util.List;
import com.dkd.manage.domain.Inventory;
import org.apache.ibatis.annotations.Param;

/**
 * 库存Mapper接口
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public interface InventoryMapper
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
     * 删除库存
     *
     * @param id 库存主键
     * @return 结果
     */
    public int deleteInventoryById(Long id);

    /**
     * 批量删除库存
     *
     * @param ids 需要删除的数据主键集合
     * @return 结果
     */
    public int deleteInventoryByIds(Long[] ids);

    /**
     * 根据售货机ID、商品ID、货道ID查询库存
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @param channelId 货道ID
     * @return 库存信息
     */
    public Inventory selectByVmIdAndSkuIdAndChannelId(@Param("vmId") Long vmId, @Param("skuId") Long skuId, @Param("channelId") Long channelId);

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
}
