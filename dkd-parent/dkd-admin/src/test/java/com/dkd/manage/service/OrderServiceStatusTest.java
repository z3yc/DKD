package com.dkd.manage.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.common.constant.DkdContants;
import com.dkd.common.exception.ServiceException;
import com.dkd.manage.domain.Order;
import com.dkd.manage.domain.dto.OrderDetailDto;
import com.dkd.manage.mapper.OrderMapper;
import com.dkd.manage.service.impl.OrderServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 订单状态口径与异常类型的回归测试（P2-24、契约表 {@code docs/CONTRACTS/status-codes.md}）。
 *
 * <p>## 这批用例防什么
 * <ul>
 *   <li><b>异常类型</b>：{@code getOrderDetail} 原抛裸 {@code RuntimeException}，
 *       在 RuoYi 全局异常处理器里会退化成「系统异常」而不是业务提示；改为
 *       {@link ServiceException} 后文案必须逐字不变（{@code OrderController} 会把它拼进
 *       "获取订单详情失败: xxx"，前端按此排障）。</li>
 *   <li><b>状态字面量</b>：{@code 4L/2L/0L} 原散落在 {@code cancelOrder}/{@code refundOrder}/
 *       {@code canCancel}/{@code canRefund}/{@code getStatusDesc} 五处，契约表一改就漏；
 *       现统一走 {@link DkdContants} 常量，本测试用常量断言（而非再写一遍字面量）。</li>
 *   <li><b>拒绝路径</b>：已支付订单不可取消、待支付订单不可退款——AGENTS §8 明确
 *       「该拒绝的没拒绝」比「该通过的没通过」严重得多，这里逐条钉住。</li>
 *   <li><b>用户可见文案</b>：4=失效（INVALID）在详情页仍显示「已取消」，P2-24 只统一
 *       注释/常量口径，不改显示，故这里锁住 {@code statusDesc} 文案。</li>
 * </ul>
 *
 * <p>## 为什么用 Mockito 不连真库
 * AGENTS §8：Java 单测不依赖 MySQL/Redis。被测语义是「分支、异常类型、写入字段」，
 * 与 SQL 无关；SQL 形状由 {@code OrderMapperSqlTest} 在文本层守护。
 *
 * @author ruoyi
 * @date 2026-10-29
 */
@ExtendWith(MockitoExtension.class)
// 不同用例覆盖不同分支，未打桩的协作对象在部分分支不会被调用
@MockitoSettings(strictness = Strictness.LENIENT)
class OrderServiceStatusTest
{
    private static final Long ORDER_ID = 7L;

    @Mock
    private OrderMapper orderMapper;

    @InjectMocks
    private OrderServiceImpl orderService;

    /**
     * 构造一条只关心状态/文案的订单记录。
     *
     * @param status 订单状态（null 表示库里状态为空）
     * @return 订单实体
     */
    private Order order(Long status)
    {
        Order order = new Order();
        order.setId(ORDER_ID);
        order.setOrderNo("DKD202610290001");
        order.setSkuName("可口可乐");
        order.setAmount(350L);
        order.setStatus(status);
        return order;
    }

    @Test
    void 订单ID为空时抛业务异常()
    {
        assertThatThrownBy(() -> orderService.getOrderDetail(null))
                .isInstanceOf(ServiceException.class)
                .hasMessage("订单ID不能为空");
        verify(orderMapper, never()).selectOrderById(any(Long.class));
    }

    @Test
    void 订单不存在时抛业务异常且文案保持原样()
    {
        when(orderMapper.selectOrderById(99L)).thenReturn(null);

        assertThatThrownBy(() -> orderService.getOrderDetail(99L))
                .isInstanceOf(ServiceException.class)
                .hasMessage("订单不存在，订单ID: 99");
    }

    @Test
    void 失效订单详情沿用已取消文案且不可退款不可取消()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(order(DkdContants.ORDER_STATUS_INVALID));

        OrderDetailDto dto = orderService.getOrderDetail(ORDER_ID);

        assertThat(dto.getStatus()).isEqualTo(DkdContants.ORDER_STATUS_INVALID);
        assertThat(dto.getStatusDesc())
                .as("4=失效 在页面仍显示历史文案「已取消」，P2-24 不改用户可见文案")
                .isEqualTo("已取消");
        assertThat(dto.getCanCancel()).isFalse();
        assertThat(dto.getCanRefund()).isFalse();
    }

    @Test
    void 待支付订单取消写入失效状态与取消原因()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(order(DkdContants.ORDER_STATUS_CREATE));
        when(orderMapper.updateOrder(any(Order.class))).thenReturn(1);

        int affected = orderService.cancelOrder(ORDER_ID, "用户主动取消");

        assertThat(affected).isEqualTo(1);
        ArgumentCaptor<Order> captor = ArgumentCaptor.forClass(Order.class);
        verify(orderMapper).updateOrder(captor.capture());
        Order update = captor.getValue();
        assertThat(update.getId()).isEqualTo(ORDER_ID);
        assertThat(update.getStatus())
                .as("取消订单落契约表 4=失效 INVALID（不是 5，也不是无状态）")
                .isEqualTo(DkdContants.ORDER_STATUS_INVALID);
        assertThat(update.getCancelDesc()).isEqualTo("用户主动取消");
        assertThat(update.getUpdateTime()).as("写库必须带更新时间").isNotNull();
    }

    @Test
    void 已支付订单不可取消且不写库()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(order(DkdContants.ORDER_STATUS_PAYED));

        assertThat(orderService.cancelOrder(ORDER_ID, "想取消")).isEqualTo(0);
        verify(orderMapper, never()).updateOrder(any(Order.class));
    }

    @Test
    void 出货成功订单申请退款写入退款中()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));
        when(orderMapper.updateOrder(any(Order.class))).thenReturn(1);

        int affected = orderService.refundOrder(ORDER_ID);

        assertThat(affected).isEqualTo(1);
        ArgumentCaptor<Order> captor = ArgumentCaptor.forClass(Order.class);
        verify(orderMapper).updateOrder(captor.capture());
        assertThat(captor.getValue().getPayStatus())
                .as("申请退款只改支付状态为 2=退款中，不改订单状态列")
                .isEqualTo(DkdContants.PAY_STATUS_REFUNDING);
        assertThat(captor.getValue().getStatus()).as("订单状态列不应被退款动作覆盖").isNull();
    }

    @Test
    void 待支付订单不可退款且不写库()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(order(DkdContants.ORDER_STATUS_CREATE));

        assertThat(orderService.refundOrder(ORDER_ID)).isEqualTo(0);
        verify(orderMapper, never()).updateOrder(any(Order.class));
    }

    @Test
    void 订单不存在时取消与退款都返回零且不写库()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(null);

        assertThat(orderService.cancelOrder(ORDER_ID, "取消")).isEqualTo(0);
        assertThat(orderService.refundOrder(ORDER_ID)).isEqualTo(0);
        verify(orderMapper, never()).updateOrder(any(Order.class));
    }

    @Test
    void 状态为空的订单不可取消不可退款()
    {
        when(orderMapper.selectOrderById(ORDER_ID)).thenReturn(order(null));

        assertThat(orderService.cancelOrder(ORDER_ID, "取消")).isEqualTo(0);
        assertThat(orderService.refundOrder(ORDER_ID)).isEqualTo(0);
        verify(orderMapper, never()).updateOrder(any(Order.class));
    }
}
