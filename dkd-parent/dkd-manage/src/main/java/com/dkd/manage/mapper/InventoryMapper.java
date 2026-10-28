package com.dkd.manage.mapper;

import java.util.Date;
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
     * 根据售货机ID、货道ID查询库存（库存台账的库存粒度以货道为准）
     *
     * <p>为什么以货道为第一优先：清晰版口径把 tb_channel 定为"卖什么/容量多少"的权威，
     * 台账按货道一行；同一个 SKU 在一台设备上可能占多个货道（docs/DKD业务规范-清晰版.md §8-1）。
     * 旧数据只保证 (vm_id, sku_id) 唯一，因此调用方还需回退 {@link #selectByVmIdAndSkuId}。
     *
     * @param vmId 售货机ID
     * @param channelId 货道ID
     * @return 库存信息（多行时取 id 最小的一行，避免 TooManyResultsException）
     */
    public Inventory selectByVmIdAndChannelId(@Param("vmId") Long vmId, @Param("channelId") Long channelId);

    /**
     * 根据售货机ID、商品ID查询库存（兼容旧唯一键 uk_vm_sku 的回退路径）
     *
     * @param vmId 售货机ID
     * @param skuId 商品ID
     * @return 库存信息（多行时取 id 最小的一行）
     */
    public Inventory selectByVmIdAndSkuId(@Param("vmId") Long vmId, @Param("skuId") Long skuId);

    /**
     * 更新库存台账的数量事实列（current_stock / is_alert / last_supply_time）
     *
     * <p>为什么单独开一个方法而不是复用 {@link #updateInventory(Inventory)}：通用更新会连带写
     * sku_id/channel_id/max_stock 等档案列，库存变更服务只应动"数量 + 预警标记 + 补货时间"，
     * 授权范围越小越不容易把台账写歪（清晰版 §4.3）。
     *
     * @param id 库存主键
     * @param quantity 变更后库存
     * @param isAlert 预警标记：1-预警 0-正常
     * @param lastSupplyTime 上次补货时间（仅补货类型传值，其它传 null 表示不改）
     * @param updateTime 更新时间
     * @return 结果
     */
    public int updateInventoryStockState(@Param("id") Long id, @Param("quantity") Integer quantity,
                                         @Param("isAlert") Integer isAlert, @Param("lastSupplyTime") Date lastSupplyTime,
                                         @Param("updateTime") Date updateTime);

    /**
     * 查询低库存预警列表（current_stock <= min_stock，与 Service 语义一致）
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
