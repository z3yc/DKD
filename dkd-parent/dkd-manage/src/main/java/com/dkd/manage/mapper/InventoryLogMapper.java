package com.dkd.manage.mapper;

import java.util.List;
import com.dkd.manage.domain.InventoryLog;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

/**
 * 库存变动日志Mapper接口
 *
 * @author ruoyi
 * @date 2025-01-08
 */
public interface InventoryLogMapper
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
     * 删除库存变动日志
     *
     * @param id 库存变动日志主键
     * @return 结果
     */
    public int deleteInventoryLogById(Long id);

    /**
     * 批量删除库存变动日志
     *
     * @param ids 需要删除的数据主键集合
     * @return 结果
     */
    public int deleteInventoryLogByIds(Long[] ids);

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

    /**
     * 统计某订单已产生的「销售扣减」流水条数（P0-1 幂等判据）。
     *
     * <p>为什么是 {@code order_id + change_type} 而不是只看 {@code order_id}：
     * 同一订单还可能因对账/损耗产生其它类型流水，只有「销售」类型才代表这次扣减已经做过。
     *
     * <p>注解 SQL 无法引用 Java 常量：{@code change_type = 1} 即
     * {@link InventoryLog#CHANGE_TYPE_SALE}，改契约时两处一起改。
     *
     * @param orderId 订单Id（tb_order.id）
     * @return 已扣减次数
     */
    @Select("SELECT COUNT(1) FROM tb_inventory_log WHERE order_id = #{orderId} AND change_type = 1")
    int countSaleByOrderId(@Param("orderId") Long orderId);
}
