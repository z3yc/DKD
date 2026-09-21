package com.dkd.manage.service;

import java.util.List;
import com.dkd.manage.domain.InventoryLog;

/**
 * 库存变动日志Service接口
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public interface IInventoryLogService
{
    /**
     * 查询库存变动日志
     *
     * @param id 库存变动日志主键
     * @return 库存变动日志
     */
    public InventoryLog selectInventoryLogById(Long id);

    /**
     * 查询库存变动日志列表
     *
     * @param inventoryLog 库存变动日志
     * @return 库存变动日志集合
     */
    public List<InventoryLog> selectInventoryLogList(InventoryLog inventoryLog);

    /**
     * 新增库存变动日志
     *
     * @param inventoryLog 库存变动日志
     * @return 结果
     */
    public int insertInventoryLog(InventoryLog inventoryLog);

    /**
     * 批量删除库存变动日志
     *
     * @param ids 需要删除的库存变动日志主键集合
     * @return 结果
     */
    public int deleteInventoryLogByIds(Long[] ids);

    /**
     * 删除库存变动日志信息
     *
     * @param id 库存变动日志主键
     * @return 结果
     */
    public int deleteInventoryLogById(Long id);

    /**
     * 根据售货机ID查询库存变动日志
     *
     * @param vmId 售货机ID
     * @return 库存变动日志列表
     */
    public List<InventoryLog> selectByVmId(Long vmId);

    /**
     * 根据商品ID查询库存变动日志
     *
     * @param skuId 商品ID
     * @return 库存变动日志列表
     */
    public List<InventoryLog> selectBySkuId(Long skuId);

    /**
     * 根据货道ID查询库存变动日志
     *
     * @param channelId 货道ID
     * @return 库存变动日志列表
     */
    public List<InventoryLog> selectByChannelId(Long channelId);
}
