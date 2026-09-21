package com.dkd.manage.service.impl;

import java.util.List;
import com.dkd.common.utils.DateUtils;
import com.dkd.manage.mapper.InventoryLogMapper;
import com.dkd.manage.domain.InventoryLog;
import com.dkd.manage.service.IInventoryLogService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

/**
 * 库存变动日志Service业务层处理
 *
 * @author ruoyi
 * @date 2025-01-08
 */
@Service
public class InventoryLogServiceImpl implements IInventoryLogService
{
    @Autowired
    private InventoryLogMapper inventoryLogMapper;

    /**
     * 查询库存变动日志
     *
     * @param id 库存变动日志主键
     * @return 库存变动日志
     */
    @Override
    public InventoryLog selectInventoryLogById(Long id)
    {
        return inventoryLogMapper.selectInventoryLogById(id);
    }

    /**
     * 查询库存变动日志列表
     *
     * @param inventoryLog 库存变动日志
     * @return 库存变动日志
     */
    @Override
    public List<InventoryLog> selectInventoryLogList(InventoryLog inventoryLog)
    {
        return inventoryLogMapper.selectInventoryLogList(inventoryLog);
    }

    /**
     * 新增库存变动日志
     *
     * @param inventoryLog 库存变动日志
     * @return 结果
     */
    @Override
    public int insertInventoryLog(InventoryLog inventoryLog)
    {
        inventoryLog.setCreateTime(DateUtils.getNowDate());
        return inventoryLogMapper.insertInventoryLog(inventoryLog);
    }

    /**
     * 批量删除库存变动日志
     *
     * @param ids 需要删除的库存变动日志主键
     * @return 结果
     */
    @Override
    public int deleteInventoryLogByIds(Long[] ids)
    {
        return inventoryLogMapper.deleteInventoryLogByIds(ids);
    }

    /**
     * 删除库存变动日志信息
     *
     * @param id 库存变动日志主键
     * @return 结果
     */
    @Override
    public int deleteInventoryLogById(Long id)
    {
        return inventoryLogMapper.deleteInventoryLogById(id);
    }

    /**
     * 根据售货机ID查询库存变动日志
     *
     * @param vmId 售货机ID
     * @return 库存变动日志列表
     */
    @Override
    public List<InventoryLog> selectByVmId(Long vmId)
    {
        return inventoryLogMapper.selectByVmId(vmId);
    }

    /**
     * 根据商品ID查询库存变动日志
     *
     * @param skuId 商品ID
     * @return 库存变动日志列表
     */
    @Override
    public List<InventoryLog> selectBySkuId(Long skuId)
    {
        return inventoryLogMapper.selectBySkuId(skuId);
    }

    /**
     * 根据货道ID查询库存变动日志
     *
     * @param channelId 货道ID
     * @return 库存变动日志列表
     */
    @Override
    public List<InventoryLog> selectByChannelId(Long channelId)
    {
        return inventoryLogMapper.selectByChannelId(channelId);
    }
}
