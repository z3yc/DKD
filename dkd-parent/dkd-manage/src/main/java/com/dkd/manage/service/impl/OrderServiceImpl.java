package com.dkd.manage.service.impl;

import java.util.List;
import com.dkd.common.constant.DkdContants;
import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.mapper.InventoryLogMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
import com.dkd.manage.service.IInventoryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.dkd.manage.mapper.OrderMapper;
import com.dkd.manage.domain.Order;
import com.dkd.manage.domain.dto.OrderDetailDto;
import com.dkd.manage.service.IOrderService;

/**
 * 订单管理Service业务层处理
 * 
 * @author itheima
 * @date 2024-07-29
 */
@Service
public class OrderServiceImpl implements IOrderService 
{
    private static final Logger log = LoggerFactory.getLogger(OrderServiceImpl.class);

    @Autowired
    private OrderMapper orderMapper;

    /** P0-1：出货成功后回写库存台账（唯一库存变更入口） */
    @Autowired
    private IInventoryService inventoryService;

    @Autowired
    private VendingMachineMapper vendingMachineMapper;

    @Autowired
    private ChannelMapper channelMapper;

    @Autowired
    private InventoryLogMapper inventoryLogMapper;

    /**
     * 查询订单管理
     * 
     * @param id 订单管理主键
     * @return 订单管理
     */
    @Override
    public Order selectOrderById(Long id)
    {
        return orderMapper.selectOrderById(id);
    }

    /**
     * 查询订单管理列表
     * 
     * @param order 订单管理
     * @return 订单管理
     */
    @Override
    public List<Order> selectOrderList(Order order)
    {
        return orderMapper.selectOrderList(order);
    }

    /**
     * 新增订单管理。
     *
     * <p>P0-1（交易链路接线）：订单落库状态为「2 出货成功」时，同事务内回写销售库存。
     * 为什么只认 2：状态机是 {@code 0 待支付 → 1 支付完成 → 2 出货成功 / 3 出货失败}，
     * 只有 2 代表货真的出了；在 1 扣减会和退款/出货失败纠缠，在 0 扣减是凭空预占。
     *
     * <p>为什么库存失败不回滚订单：订单是主业务事实、库存是派生数据（清晰版 §1.1）；
     * 且销售已在设备侧发生，拦住订单只会让真实交易记录进不了库——比台账暂时不准更糟。
     * 扣减方法自身用独立事务（REQUIRES_NEW），因此这里 catch 不会把主事务打成 rollback-only。
     *
     * @param order 订单管理
     * @return 结果
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public int insertOrder(Order order)
    {
        order.setCreateTime(DateUtils.getNowDate());
        int rows = orderMapper.insertOrder(order);
        if (rows > 0 && DkdContants.ORDER_STATUS_VENDOUT_SUCCESS.equals(order.getStatus()))
        {
            syncSaleInventory(order);
        }
        return rows;
    }

    /**
     * 出货成功后回写库存台账（P0-1）。
     *
     * <p>定位链路：订单表没有 vm_id/channel_id，只能由
     * {@code inner_code → tb_vending_machine.id}、{@code channel_code → tb_channel.id} 反查。
     * 任一环缺失就**不扣减、不阻断订单**——实测 29% 订单的 {@code channel_code} 为空（排期 FIX-21），
     * 这些单只能靠 Phase B 的对账任务收敛，强行扣减会扣到错误的货道。
     *
     * <p>失败一律只 WARN 不抛：调用方是订单落库主链路，库存回写是尽力而为。
     *
     * @param order 已落库（id 已回填）的订单
     */
    private void syncSaleInventory(Order order)
    {
        Long orderId = order.getId();
        try
        {
            if (order.getInnerCode() == null || order.getChannelCode() == null || order.getSkuId() == null)
            {
                log.warn("订单出货成功但缺少设备/货道/商品信息，跳过库存回写：orderId={}，orderNo={}，innerCode={}，"
                        + "channelCode={}，skuId={}", orderId, order.getOrderNo(), order.getInnerCode(),
                        order.getChannelCode(), order.getSkuId());
                return;
            }
            VendingMachine vm = vendingMachineMapper.selectVendingMachineByInnerCode(order.getInnerCode());
            if (vm == null || vm.getId() == null)
            {
                log.warn("订单出货成功但设备不存在，跳过库存回写：orderId={}，innerCode={}", orderId, order.getInnerCode());
                return;
            }
            Channel channel = channelMapper.getChannelInfo(order.getInnerCode(), order.getChannelCode());
            if (channel == null || channel.getId() == null)
            {
                log.warn("订单出货成功但货道不存在，跳过库存回写：orderId={}，innerCode={}，channelCode={}",
                        orderId, order.getInnerCode(), order.getChannelCode());
                return;
            }
            // 幂等：同一订单只允许产生一条销售扣减流水（重复落库/补偿重放时不再扣）
            if (orderId != null && inventoryLogMapper.countSaleByOrderId(orderId) > 0)
            {
                log.info("订单已扣减过销售库存，跳过：orderId={}", orderId);
                return;
            }
            int result = inventoryService.deductForSale(vm.getId(), order.getSkuId(), channel.getId(), orderId);
            if (result < 0)
            {
                log.warn("订单出货成功但库存台账不存在，未扣减（差额待对账）：orderId={}，vmId={}，channelId={}",
                        orderId, vm.getId(), channel.getId());
            }
            else if (result == 0)
            {
                log.warn("订单出货成功但账面库存不足，已按 0 处理（差额待对账）：orderId={}，vmId={}，channelId={}",
                        orderId, vm.getId(), channel.getId());
            }
        }
        catch (Exception e)
        {
            // 订单是主业务事实：库存回写异常只告警，不得让下单失败
            log.warn("订单出货成功但库存回写失败（订单保留，差额待对账）：orderId={}，orderNo={}",
                    orderId, order.getOrderNo(), e);
        }
    }

    /**
     * 修改订单管理
     * 
     * @param order 订单管理
     * @return 结果
     */
    @Override
    public int updateOrder(Order order)
    {
        order.setUpdateTime(DateUtils.getNowDate());
        return orderMapper.updateOrder(order);
    }

    /**
     * 批量删除订单管理
     * 
     * @param ids 需要删除的订单管理主键
     * @return 结果
     */
    @Override
    public int deleteOrderByIds(Long[] ids)
    {
        return orderMapper.deleteOrderByIds(ids);
    }

    /**
     * 删除订单管理信息
     * 
     * @param id 订单管理主键
     * @return 结果
     */
    @Override
    public int deleteOrderById(Long id)
    {
        return orderMapper.deleteOrderById(id);
    }

    /**
     * 获取订单详细信息
     * 
     * @param id 订单主键
     * @return 订单详情DTO
     */
    @Override
    public OrderDetailDto getOrderDetail(Long id)
    {
        // P2-24：业务异常统一走 ServiceException（RuoYi 全局异常处理器转用户可见文案）。
        // 文案逐字保留——OrderController 会把它拼进 "获取订单详情失败: xxx"，前端/运维按此排障。
        if (id == null) {
            throw new ServiceException("订单ID不能为空");
        }
        
        Order order = orderMapper.selectOrderById(id);
        if (order == null) {
            throw new ServiceException("订单不存在，订单ID: " + id);
        }
        
        OrderDetailDto orderDetailDto = new OrderDetailDto();
        
        // 复制基本属性
        orderDetailDto.setId(order.getId());
        orderDetailDto.setOrderNo(order.getOrderNo());
        orderDetailDto.setSkuName(order.getSkuName());
        orderDetailDto.setAmount(order.getAmount());
        orderDetailDto.setStatus(order.getStatus());
        orderDetailDto.setInnerCode(order.getInnerCode());
        orderDetailDto.setAddr(order.getAddr());
        orderDetailDto.setCreateTime(order.getCreateTime());
        orderDetailDto.setUpdateTime(order.getUpdateTime());
        orderDetailDto.setPayType(order.getPayType());
        orderDetailDto.setThirdNo(order.getThirdNo());
        
        // 设置状态描述
        orderDetailDto.setStatusDesc(getStatusDesc(order.getStatus()));
        
        // 设置支付方式描述
        orderDetailDto.setPayTypeDesc(getPayTypeDesc(order.getPayType()));
        
        // 设置操作按钮状态
        orderDetailDto.setCanRefund(canRefund(order.getStatus()));
        orderDetailDto.setCanCancel(canCancel(order.getStatus()));
        
        return orderDetailDto;
    }

    /**
     * 取消订单。
     *
     * <p>口径说明（P2-24 修正，以 {@code docs/CONTRACTS/status-codes.md} 为准）：
     * 取消订单写入的是 {@link DkdContants#ORDER_STATUS_INVALID}（4=失效 INVALID），
     * 原注释「4-已取消」与契约表/库口径冲突——「取消」是业务动作，「失效」是该状态列的取值语义。
     * 订单详情页的状态文案仍显示「已取消」（用户可见文案本轮不变，见 {@link #getStatusDesc}）。
     *
     * @param id 订单主键
     * @param cancelDesc 取消原因
     * @return 结果（订单不存在或当前状态不可取消时返回 0）
     */
    @Override
    public int cancelOrder(Long id, String cancelDesc)
    {
        Order order = orderMapper.selectOrderById(id);
        if (order == null || !canCancel(order.getStatus())) {
            return 0;
        }
        
        Order updateOrder = new Order();
        updateOrder.setId(id);
        // 取消订单落「失效」状态（4=INVALID），不是独立的状态码 5
        updateOrder.setStatus(DkdContants.ORDER_STATUS_INVALID);
        updateOrder.setCancelDesc(cancelDesc);
        updateOrder.setUpdateTime(DateUtils.getNowDate());
        
        return orderMapper.updateOrder(updateOrder);
    }

    /**
     * 申请退款
     * 
     * @param id 订单主键
     * @return 结果
     */
    @Override
    public int refundOrder(Long id)
    {
        Order order = orderMapper.selectOrderById(id);
        if (order == null || !canRefund(order.getStatus())) {
            return 0;
        }
        
        Order updateOrder = new Order();
        updateOrder.setId(id);
        // 支付状态 → 退款中（2）；退款完成(3)由支付回调链路写入，不在本方法
        updateOrder.setPayStatus(DkdContants.PAY_STATUS_REFUNDING);
        updateOrder.setUpdateTime(DateUtils.getNowDate());
        
        return orderMapper.updateOrder(updateOrder);
    }

    /**
     * 获取订单状态描述。
     *
     * <p>P2-24：状态码改用 {@link DkdContants} 常量。因 Java 8 的 switch case 只接受
     * 编译期 int 常量（Long 常量/方法调用都不合法），这里改为 if/else 常量比较；
     * 状态文案逐字保留（前端直接展示），其中 4=失效在页面仍显示「已取消」。
     *
     * @param status 状态码
     * @return 状态描述
     */
    private String getStatusDesc(Long status)
    {
        if (DkdContants.ORDER_STATUS_CREATE.equals(status)) {
            return "待支付";
        }
        if (DkdContants.ORDER_STATUS_PAYED.equals(status)) {
            return "支付完成";
        }
        if (DkdContants.ORDER_STATUS_VENDOUT_SUCCESS.equals(status)) {
            return "出货成功";
        }
        if (DkdContants.ORDER_STATUS_VENDOUT_FAIL.equals(status)) {
            return "出货失败";
        }
        if (DkdContants.ORDER_STATUS_INVALID.equals(status)) {
            return "已取消";
        }
        return "未知";
    }

    /**
     * 获取支付方式描述
     * 
     * @param payType 支付方式
     * @return 支付方式描述
     */
    private String getPayTypeDesc(String payType)
    {
        if (payType == null) {
            return "未知";
        }
        switch (payType) {
            case "1": return "支付宝";
            case "2": return "微信";
            default: return "未知";
        }
    }

    /**
     * 判断订单是否可以退款
     * 
     * @param status 订单状态
     * @return 是否可以退款
     */
    private Boolean canRefund(Long status)
    {
        // 只有出货成功的订单可以申请退款（2=VENDOUT_SUCCESS，口径见契约表）
        return DkdContants.ORDER_STATUS_VENDOUT_SUCCESS.equals(status);
    }

    /**
     * 判断订单是否可以取消
     * 
     * @param status 订单状态
     * @return 是否可以取消
     */
    private Boolean canCancel(Long status)
    {
        // 只有待支付的订单可以取消（0=CREATE，口径见契约表）
        return DkdContants.ORDER_STATUS_CREATE.equals(status);
    }
}
