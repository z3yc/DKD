package com.dkd.manage.service.impl;

import java.util.List;
import com.dkd.common.utils.DateUtils;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
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
    @Autowired
    private OrderMapper orderMapper;

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
     * 新增订单管理
     * 
     * @param order 订单管理
     * @return 结果
     */
    @Override
    public int insertOrder(Order order)
    {
        order.setCreateTime(DateUtils.getNowDate());
        return orderMapper.insertOrder(order);
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
        if (id == null) {
            throw new RuntimeException("订单ID不能为空");
        }
        
        Order order = orderMapper.selectOrderById(id);
        if (order == null) {
            throw new RuntimeException("订单不存在，订单ID: " + id);
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
     * 取消订单
     * 
     * @param id 订单主键
     * @param cancelDesc 取消原因
     * @return 结果
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
        updateOrder.setStatus(4L); // 4-已取消
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
        updateOrder.setPayStatus(2L); // 2-退款中
        updateOrder.setUpdateTime(DateUtils.getNowDate());
        
        return orderMapper.updateOrder(updateOrder);
    }

    /**
     * 获取订单状态描述
     * 
     * @param status 状态码
     * @return 状态描述
     */
    private String getStatusDesc(Long status)
    {
        if (status == null) {
            return "未知";
        }
        switch (status.intValue()) {
            case 0: return "待支付";
            case 1: return "支付完成";
            case 2: return "出货成功";
            case 3: return "出货失败";
            case 4: return "已取消";
            default: return "未知";
        }
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
        // 只有出货成功的订单可以申请退款
        return status != null && status == 2L;
    }

    /**
     * 判断订单是否可以取消
     * 
     * @param status 订单状态
     * @return 是否可以取消
     */
    private Boolean canCancel(Long status)
    {
        // 只有待支付的订单可以取消
        return status != null && status == 0L;
    }
}
