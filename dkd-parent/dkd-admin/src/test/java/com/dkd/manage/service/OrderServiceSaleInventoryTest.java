package com.dkd.manage.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.common.constant.DkdContants;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.domain.Order;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.mapper.InventoryLogMapper;
import com.dkd.manage.mapper.OrderMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
import com.dkd.manage.service.impl.OrderServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 出货成功回写销售库存的单元测试（P0-1 交易链路接线）。
 *
 * <p>## 这批用例防什么
 * 梳理报告 P0-1 指出「库存扣减入口零调用，下单不扣库存」——`updateInventoryQuantity` 是死代码。
 * 接线后最容易踩的三个坑，本测试逐条钉住：
 * <ul>
 *   <li><b>扣错状态</b>：只有 {@code status=2 出货成功} 才扣；0/1/3/4 一律不扣
 *       （在支付完成扣会与退款纠缠，在待支付扣是凭空预占）；</li>
 *   <li><b>扣错货道</b>：定位链路必须经 {@code inner_code → 设备}、{@code channel_code → 货道}；
 *       任一环缺失就跳过并 WARN，绝不"按 sku 猜一个货道"；</li>
 *   <li><b>把订单带崩</b>：库存回写是尽力而为（订单是主业务事实、库存是派生数据）。
 *       台账不存在 / 库存不足 / 扣减抛异常，都必须仍返回成功——否则真实销售记录会进不了库。</li>
 * </ul>
 * 另覆盖幂等：同一订单已有销售流水时不得重复扣减。
 *
 * <p>## 为什么不连真库
 * AGENTS §8：Java 单测不依赖 MySQL/Redis。被测语义是「分支、参数、异常边界」；SQL 形状由
 * {@code OrderMapperSqlTest} 与 Mapper 注解守护，真库行为留 M2/D 阶段验收。
 *
 * @author ruoyi
 * @date 2026-11-02
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class OrderServiceSaleInventoryTest
{
    private static final Long ORDER_ID = 9001L;
    private static final Long VM_ID = 11L;
    private static final Long CHANNEL_ID = 22L;
    private static final Long SKU_ID = 33L;
    private static final String INNER_CODE = "A1000001";
    private static final String CHANNEL_CODE = "1-1";

    @Mock
    private OrderMapper orderMapper;

    @Mock
    private IInventoryService inventoryService;

    @Mock
    private VendingMachineMapper vendingMachineMapper;

    @Mock
    private ChannelMapper channelMapper;

    @Mock
    private InventoryLogMapper inventoryLogMapper;

    @InjectMocks
    private OrderServiceImpl orderService;

    private Order order(Long status)
    {
        Order order = new Order();
        order.setOrderNo("DKD202611020001");
        order.setInnerCode(INNER_CODE);
        order.setChannelCode(CHANNEL_CODE);
        order.setSkuId(SKU_ID);
        order.setStatus(status);
        return order;
    }

    /** 订单落库：模拟 useGeneratedKeys 把主键回填到实体上。 */
    private void stubInsert()
    {
        when(orderMapper.insertOrder(any(Order.class))).thenAnswer(invocation -> {
            Order inserted = invocation.getArgument(0);
            inserted.setId(ORDER_ID);
            return 1;
        });
    }

    private VendingMachine vm()
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(VM_ID);
        vm.setInnerCode(INNER_CODE);
        return vm;
    }

    private Channel channel()
    {
        Channel channel = new Channel();
        channel.setId(CHANNEL_ID);
        channel.setChannelCode(CHANNEL_CODE);
        return channel;
    }

    @Test
    void 出货成功时按订单扣减一次且带订单主键()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenReturn(0);
        when(inventoryService.deductForSale(VM_ID, SKU_ID, CHANNEL_ID, ORDER_ID)).thenReturn(1);

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows).isEqualTo(1);
        // 订单主键必须回填（否则流水里 order_id 为空，审计答不了"这 1 件被哪张订单扣的"）
        verify(inventoryService).deductForSale(VM_ID, SKU_ID, CHANNEL_ID, ORDER_ID);
        verify(inventoryLogMapper).countSaleByOrderId(ORDER_ID);
    }

    @Test
    void 非出货成功状态不扣减()
    {
        stubInsert();

        orderService.insertOrder(order(DkdContants.ORDER_STATUS_CREATE));

        verifyNoInteractions(inventoryService, vendingMachineMapper, channelMapper, inventoryLogMapper);
    }

    @Test
    void 货道编码为空时跳过扣减但订单仍成功()
    {
        stubInsert();
        Order order = order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS);
        order.setChannelCode(null);

        int rows = orderService.insertOrder(order);

        assertThat(rows).isEqualTo(1);
        verifyNoInteractions(inventoryService, vendingMachineMapper, channelMapper);
    }

    @Test
    void 设备不存在时跳过扣减但订单仍成功()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(null);

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows).isEqualTo(1);
        verifyNoInteractions(inventoryService);
    }

    @Test
    void 货道不存在时跳过扣减但订单仍成功()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(null);

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows).isEqualTo(1);
        verifyNoInteractions(inventoryService);
    }

    @Test
    void 已扣减过的订单不重复扣()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenReturn(1);

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows).isEqualTo(1);
        verify(inventoryService, never()).deductForSale(anyLong(), anyLong(), anyLong(), anyLong());
    }

    @Test
    void 库存台账不存在时订单仍成功()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenReturn(0);
        when(inventoryService.deductForSale(VM_ID, SKU_ID, CHANNEL_ID, ORDER_ID)).thenReturn(-1);

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows)
                .as("台账缺失不得回滚订单（差额待对账，订单是主业务事实）")
                .isEqualTo(1);
    }

    @Test
    void 账面库存不足时订单仍成功()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenReturn(0);
        when(inventoryService.deductForSale(VM_ID, SKU_ID, CHANNEL_ID, ORDER_ID)).thenReturn(0);

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows).isEqualTo(1);
    }

    @Test
    void 库存回写抛异常时订单仍成功()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenReturn(0);
        when(inventoryService.deductForSale(VM_ID, SKU_ID, CHANNEL_ID, ORDER_ID))
                .thenThrow(new RuntimeException("库存库连接超时"));

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows)
                .as("库存异常只 WARN，不得让下单失败")
                .isEqualTo(1);
    }

    @Test
    void 幂等查询本身异常时订单仍成功()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenThrow(new RuntimeException("查询超时"));

        int rows = orderService.insertOrder(order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS));

        assertThat(rows).isEqualTo(1);
        verify(inventoryService, never()).deductForSale(anyLong(), anyLong(), anyLong(), anyLong());
    }

    @Test
    void 扣减成功时创建时间被写入()
    {
        stubInsert();
        when(vendingMachineMapper.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm());
        when(channelMapper.getChannelInfo(INNER_CODE, CHANNEL_CODE)).thenReturn(channel());
        when(inventoryLogMapper.countSaleByOrderId(ORDER_ID)).thenReturn(0);
        Order order = order(DkdContants.ORDER_STATUS_VENDOUT_SUCCESS);

        orderService.insertOrder(order);

        assertThat(order.getCreateTime()).isNotNull();
    }
}
