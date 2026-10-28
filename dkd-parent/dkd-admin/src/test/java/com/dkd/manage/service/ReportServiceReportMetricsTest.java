package com.dkd.manage.service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import com.dkd.common.constant.DkdContants;
import com.dkd.manage.domain.Report;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.mapper.ReportMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
import com.dkd.manage.service.impl.AiReportServiceImpl;
import com.dkd.manage.service.impl.ReportServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 报表口径与失败可见性的回归测试（业务规范 §4.5 / §4.7、AGENTS §6.4）。
 *
 * <p>## 防什么
 * <ul>
 *   <li><b>故障设备口径</b>：原实现 {@code vm_status != 1} 把「未投放(0)」「已撤机(3)」也算故障，
 *       故障率虚高、AI 分析跟着失真；现在只认停机(2)。</li>
 *   <li><b>库存不足设备数</b>：由 Mapper 负责 `count(distinct vm_id) where current_stock &lt;= min_stock`
 *       （SQL 形状断言在 {@code ReportMapperSqlTest}），这里断言 Service 把它原样写进报表。</li>
 *   <li><b>失败必须可见</b>：原实现用外层 catch 吞掉所有异常，入库失败也“返回成功”；
 *       现在异常必须上抛（由 ReportTask 转成 RuntimeException 写入 sys_job_log）。</li>
 * </ul>
 *
 * <p>为什么不连真库：AGENTS §8 要求 Java 单测不依赖 MySQL/Redis；口径是纯计算/映射，
 * SQL 形状由 ReportMapperSqlTest 在文本层拦住，upsert 的真库行为留待 Phase C 验收。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class ReportServiceReportMetricsTest
{
    @Mock
    private ReportMapper reportMapper;

    @Mock
    private VendingMachineMapper vendingMachineMapper;

    @Mock
    private AiReportServiceImpl aiReportService;

    @InjectMocks
    private ReportServiceImpl reportService;

    private VendingMachine vm(Long vmStatus)
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(80L);
        vm.setInnerCode("A1000001");
        vm.setVmStatus(vmStatus);
        return vm;
    }

    /**
     * 打桩全流程依赖（聚合数字固定，便于断言）。
     */
    private void stubHappyAggregates()
    {
        when(reportMapper.sumRevenueByStatusAndDateRange(anyInt(), anyString(), anyString())).thenReturn(1234L);
        when(reportMapper.countOrdersByStatusAndDateRange(anyInt(), anyString(), anyString())).thenReturn(7);
        when(reportMapper.topNodesByRevenue(anyInt(), anyString(), anyString()))
                .thenReturn(new ArrayList<Map<String, Object>>());
        when(reportMapper.topProductsBySales(anyInt(), anyString(), anyString()))
                .thenReturn(new ArrayList<Map<String, Object>>());
        when(reportMapper.countLowStockDevices()).thenReturn(4);
        when(reportMapper.insertOrIgnoreReport(any(Report.class))).thenReturn(1);
        when(aiReportService.buildReportDataContext(anyString(), anyString(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new LinkedHashMap<String, Object>());
        when(aiReportService.generateReportAnalysis(anyMap())).thenReturn("AI 分析正常");
    }

    @Test
    void 故障设备只统计停机且低库存按设备数写入报表()
    {
        stubHappyAggregates();
        List<VendingMachine> allVm = new ArrayList<VendingMachine>(Arrays.asList(
                vm(DkdContants.VM_STATUS_RUNNING),   // 1 运行中
                vm(DkdContants.VM_STATUS_STOPPED),   // 2 停机 → 故障
                vm(DkdContants.VM_STATUS_STOPPED),   // 2 停机 → 故障
                vm(DkdContants.VM_STATUS_NODEPLOY),  // 0 未投放 → 不是故障
                vm(DkdContants.VM_STATUS_REVOKE)));  // 3 已撤机 → 不是故障
        when(vendingMachineMapper.selectVendingMachineList(any(VendingMachine.class))).thenReturn(allVm);

        reportService.generateReport("daily");

        ArgumentCaptor<Report> captor = ArgumentCaptor.forClass(Report.class);
        verify(reportMapper).insertOrIgnoreReport(captor.capture());
        Report report = captor.getValue();

        assertThat(report.getReportType()).isEqualTo("daily");
        assertThat(report.getTotalDevices()).isEqualTo(5);
        assertThat(report.getFaultDevices())
                .as("故障只能是 vm_status=2（未投放/撤机不是故障）")
                .isEqualTo(2);
        assertThat(report.getLowStockDevices()).as("库存不足设备数取自 Mapper 的 distinct vm 口径").isEqualTo(4);
        assertThat(report.getTotalRevenue()).isEqualTo(1234L);
        assertThat(report.getTotalOrders()).isEqualTo(7);
        assertThat(report.getStatus()).as("AI 成功时报表状态为 1").isEqualTo(1);
        assertThat(report.getAiAnalysis()).isEqualTo("AI 分析正常");
    }

    @Test
    void 入库失败必须上抛而不是被吞掉()
    {
        stubHappyAggregates();
        when(vendingMachineMapper.selectVendingMachineList(any(VendingMachine.class)))
                .thenReturn(Collections.emptyList());
        // 模拟 upsert 时数据库不可用：原实现外层 catch 会把它吞成“生成完成”
        when(reportMapper.insertOrIgnoreReport(any(Report.class)))
                .thenThrow(new IllegalStateException("连接池耗尽"));

        assertThatThrownBy(() -> reportService.generateReport("daily"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("连接池耗尽");
    }

    @Test
    void 聚合查询失败必须上抛()
    {
        when(reportMapper.sumRevenueByStatusAndDateRange(anyInt(), anyString(), anyString()))
                .thenThrow(new IllegalStateException("SQL 超时"));

        assertThatThrownBy(() -> reportService.generateWeeklyReport())
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("SQL 超时");
    }

    @Test
    void AI分析失败降级为纯数据但报表仍入库()
    {
        stubHappyAggregates();
        when(vendingMachineMapper.selectVendingMachineList(any(VendingMachine.class)))
                .thenReturn(Collections.emptyList());
        when(aiReportService.generateReportAnalysis(anyMap()))
                .thenThrow(new IllegalStateException("LLM 超时"));

        reportService.generateReport("daily");

        ArgumentCaptor<Report> captor = ArgumentCaptor.forClass(Report.class);
        verify(reportMapper).insertOrIgnoreReport(captor.capture());
        assertThat(captor.getValue().getAiAnalysis()).isNull();
        assertThat(captor.getValue().getStatus())
                .as("AI 失败是降级不是报表失败：数据仍然入库，status=2 供页面提示")
                .isEqualTo(2);
    }
}
