package com.dkd.manage.service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.common.exception.ServiceException;
import com.dkd.manage.domain.Inventory;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.domain.dto.InventoryAlertDto;
import com.dkd.manage.domain.dto.RestockSuggestionDto;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.mapper.InventoryLogMapper;
import com.dkd.manage.mapper.InventoryMapper;
import com.dkd.manage.mapper.SkuMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
import com.dkd.manage.service.impl.InventoryServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * P1-11 预警候选合并 SQL 与 P1-15 反查下沉的回归测试。
 *
 * <p>## 防什么
 * <ul>
 *   <li><b>重复加载回归</b>：原 generateInventoryAlerts / generateRestockSuggestions 各自调
 *       selectLowInventoryList + selectOutOfStockList（每接口 2 次无条件全表）再内存 Set 去重，
 *       两段逻辑几乎逐行重复；现在收敛为单条 selectAlertCandidates（OR 合并天然去重）——
 *       本测试断言旧的两次全表查询路径**不再被调用**。</li>
 *   <li><b>缺货只出一条</b>：current_stock=0 的货道在旧实现里命中两段口径（0 &lt;= min 且 =0），
 *       若退回 addAll 不去重会出两条预警/建议。</li>
 *   <li><b>错误文案不变</b>：insertInventoryByDisplay 自 Controller 下沉后，
 *       反查失败文案必须与旧 Controller 版逐字一致（前端有依赖）。</li>
 * </ul>
 *
 * <p>为什么不连真库：AGENTS §8 Java 单测不依赖 MySQL；SQL 形状（OR 合并）由
 * InventoryMapperSqlTest 在文本层守护，这里守护 Service 行为。
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class InventoryServiceAlertCandidatesTest
{
    @Mock
    private InventoryMapper inventoryMapper;

    @Mock
    private InventoryLogMapper inventoryLogMapper;

    @Mock
    private VendingMachineMapper vendingMachineMapper;

    @Mock
    private SkuMapper skuMapper;

    @Mock
    private ChannelMapper channelMapper;

    @InjectMocks
    private InventoryServiceImpl inventoryService;

    private Inventory inv(long id, int quantity, Integer minQuantity, Integer maxQuantity)
    {
        Inventory inventory = new Inventory();
        inventory.setId(id);
        inventory.setVmId(80L);
        inventory.setSkuId(1L);
        inventory.setChannelId(10L);
        inventory.setInnerCode("A1000001");
        inventory.setSkuName("测试商品");
        inventory.setChannelCode("01");
        inventory.setQuantity(quantity);
        inventory.setMinQuantity(minQuantity);
        inventory.setMaxQuantity(maxQuantity);
        return inventory;
    }

    /** 候选打桩：一条缺货（0/min=5）+ 一条低库存（3/min=5）。 */
    private void stubCandidates()
    {
        when(inventoryMapper.selectAlertCandidates()).thenReturn(new ArrayList<>(Arrays.asList(
                inv(1L, 0, 5, 10),
                inv(2L, 3, 5, 10))));
    }

    @Test
    void 预警与建议共用单条合并查询且旧双查询路径不再被调用()
    {
        stubCandidates();

        List<InventoryAlertDto> alerts = inventoryService.generateInventoryAlerts();
        List<RestockSuggestionDto> suggestions = inventoryService.generateRestockSuggestions();

        // 两方法各自走一次合并查询（共 2 次）；旧路径「每方法 2 次全表」必须不再被调用
        verify(inventoryMapper, times(2)).selectAlertCandidates();
        verify(inventoryMapper, never()).selectLowInventoryList();
        verify(inventoryMapper, never()).selectOutOfStockList();

        assertThat(alerts).hasSize(2);
        assertThat(suggestions).hasSize(2);
    }

    @Test
    void 缺货货道只产出一条严重预警不重复()
    {
        stubCandidates();

        List<InventoryAlertDto> alerts = inventoryService.generateInventoryAlerts();

        // id=1（current_stock=0）在旧实现里命中低库存+缺货两段口径，靠内存 Set 去重；
        // 合并 SQL 天然只出一次——若回归去重逻辑，此处会是 3 条
        long outOfStockAlerts = alerts.stream().filter(a -> a.getAlertType() == 2).count();
        assertThat(outOfStockAlerts).as("缺货（0）只出一条严重预警").isEqualTo(1);
        assertThat(alerts).as("低库存（3<=5）一条，共两条").hasSize(2);
        assertThat(alerts.get(0).getAlertType()).isEqualTo(2);
        assertThat(alerts.get(0).getAlertLevel()).isEqualTo(3);
    }

    @Test
    void 补货建议缺货优先级为紧急且不重复()
    {
        stubCandidates();

        List<RestockSuggestionDto> suggestions = inventoryService.generateRestockSuggestions();

        assertThat(suggestions).hasSize(2);
        assertThat(suggestions.get(0).getPriority()).as("缺货建议优先级 4（紧急）").isEqualTo(4);
    }

    @Test
    void 显示字段反查失败文案与旧Controller版逐字一致()
    {
        when(vendingMachineMapper.selectVendingMachineByInnerCode("NOPE")).thenReturn(null);

        Inventory inventory = new Inventory();
        inventory.setInnerCode("NOPE");
        inventory.setQuantity(5);

        assertThatThrownBy(() -> inventoryService.insertInventoryByDisplay(inventory))
                .isInstanceOf(ServiceException.class)
                .hasMessage("售货机编号【NOPE】不存在");
    }

    @Test
    void 显示字段反查成功后落ID再入库()
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(88L);
        when(vendingMachineMapper.selectVendingMachineByInnerCode("A1000001")).thenReturn(vm);
        when(inventoryMapper.insertInventory(any(Inventory.class))).thenReturn(1);
        // insertInventory 前置的防重查询返回空
        when(inventoryMapper.selectByVmIdAndSkuIdAndChannelId(any(), any(), any())).thenReturn(null);
        when(inventoryMapper.updateInventoryStockState(any(), any(), any(), any(), any())).thenReturn(1);

        Inventory inventory = new Inventory();
        inventory.setInnerCode("A1000001");
        inventory.setSkuId(1L);
        inventory.setChannelId(10L);
        inventory.setQuantity(5);

        int result = inventoryService.insertInventoryByDisplay(inventory);

        assertThat(result).isEqualTo(1);
        assertThat(inventory.getVmId()).as("innerCode 已反查为 vmId").isEqualTo(88L);
    }
}
