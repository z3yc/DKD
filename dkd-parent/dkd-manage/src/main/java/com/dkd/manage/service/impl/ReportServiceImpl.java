package com.dkd.manage.service.impl;

import java.text.SimpleDateFormat;
import java.util.*;
import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.Report;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.mapper.ReportMapper;
import com.dkd.manage.mapper.VendingMachineMapper;
import com.dkd.manage.service.IReportService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import cn.hutool.json.JSONUtil;

/**
 * 报表Service业务层处理
 *
 * @author ruoyi
 * @date 2026-04-22
 */
@Service
public class ReportServiceImpl implements IReportService
{
    private static final Logger log = LoggerFactory.getLogger(ReportServiceImpl.class);

    private static final int ORDER_STATUS_SUCCESS = 2;
    private static final int MAX_RETRY = 3;
    private static final double LOW_STOCK_THRESHOLD = 0.2;

    @Autowired
    private ReportMapper reportMapper;

    @Autowired
    private VendingMachineMapper vendingMachineMapper;

    @Autowired
    private AiReportServiceImpl aiReportService;

    @Override
    public Report selectReportById(Long id)
    {
        return reportMapper.selectReportById(id);
    }

    @Override
    public List<Report> selectReportList(Report report)
    {
        return reportMapper.selectReportList(report);
    }

    @Override
    public Report selectLatestDailyReport()
    {
        return reportMapper.selectLatestReport("daily");
    }

    @Override
    public Report selectLatestWeeklyReport()
    {
        return reportMapper.selectLatestReport("weekly");
    }

    @Override
    public int insertReport(Report report)
    {
        return reportMapper.insertReport(report);
    }

    @Override
    public int updateReport(Report report)
    {
        return reportMapper.updateReport(report);
    }

    @Override
    public void generateDailyReport()
    {
        generateReport("daily");
    }

    @Override
    public void generateWeeklyReport()
    {
        generateReport("weekly");
    }

    @Override
    public void generateReport(String reportType)
    {
        try {
        Calendar cal = Calendar.getInstance();
        Date endTime = DateUtils.parseDate(DateUtils.getDate() + " 00:00:00");
        cal.setTime(endTime);
        if ("weekly".equals(reportType)) {
            cal.add(Calendar.DAY_OF_MONTH, -7);
        } else {
            cal.add(Calendar.DAY_OF_MONTH, -1);
        }
        Date startTime = cal.getTime();
        Date reportDate = cal.getTime();

        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
        String beginTime = sdf.format(startTime);
        String endTimeStr = sdf.format(endTime);

        // 聚合数据
        Long totalRevenue = reportMapper.sumRevenueByStatusAndDateRange(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);
        Integer totalOrders = reportMapper.countOrdersByStatusAndDateRange(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);
        List<Map<String, Object>> topNodes = reportMapper.topNodesByRevenue(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);
        List<Map<String, Object>> topProducts = reportMapper.topProductsBySales(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);

        // 设备统计（对齐前端逻辑）
        List<VendingMachine> allVm = vendingMachineMapper.selectVendingMachineList(new VendingMachine());
        int totalDevices = allVm.size();
        int onlineDevices = 0;
        int faultDevices = 0;
        for (VendingMachine vm : allVm) {
            // 在线：runningStatus JSON 中 status === true
            if (vm.getRunningStatus() != null) {
                try {
                    cn.hutool.json.JSONObject json = JSONUtil.parseObj(vm.getRunningStatus());
                    if (json.getBool("status", false)) {
                        onlineDevices++;
                    }
                } catch (Exception ignored) {}
            }
            // 故障：vmStatus != 1
            if (vm.getVmStatus() != null && vm.getVmStatus() != 1L) {
                faultDevices++;
            }
        }

        // 库存不足设备
        Integer lowStockDevices = reportMapper.countLowStockDevices();

        // 构建AI分析数据
        Map<String, Object> contextData = aiReportService.buildReportDataContext(
                reportType, new SimpleDateFormat("yyyy-MM-dd").format(reportDate),
                totalRevenue, totalOrders, totalDevices, onlineDevices, faultDevices, lowStockDevices);
        contextData.put("高收入点位", topNodes);
        contextData.put("热销商品", topProducts);

        // 调用AI生成分析
        String aiAnalysis = null;
        boolean aiFailed = false;
        try {
            aiAnalysis = aiReportService.generateReportAnalysis(contextData);
            if (aiAnalysis != null
                    && (aiAnalysis.contains("系统处理失败") || aiAnalysis.contains("AI API 报错"))) {
                aiFailed = true;
            }
        } catch (Exception e) {
            log.error("AI报表分析调用异常", e);
            aiFailed = true;
        }

        // 组装报表（无论AI是否成功都入库）
        Report report = new Report();
        report.setReportType(reportType);
        report.setReportDate(reportDate);
        report.setTotalRevenue(totalRevenue != null ? totalRevenue : 0L);
        report.setTotalOrders(totalOrders != null ? totalOrders : 0);
        report.setTotalDevices(totalDevices);
        report.setOnlineDevices(onlineDevices);
        report.setFaultDevices(faultDevices);
        report.setLowStockDevices(lowStockDevices != null ? lowStockDevices : 0);
        report.setTopProducts(JSONUtil.toJsonStr(topProducts));
        report.setTopNodes(JSONUtil.toJsonStr(topNodes));
        report.setAiAnalysis(aiFailed ? null : aiAnalysis);
        report.setStatus(aiFailed ? 2 : 1);
        report.setRetryCount(0);

        reportMapper.insertOrIgnoreReport(report);
        log.info("{}报表生成完成，日期：{}，AI状态：{}", reportType,
                new SimpleDateFormat("yyyy-MM-dd").format(reportDate),
                aiFailed ? "失败" : "成功");

        } catch (Exception e) {
            log.error("{}报表生成失败", reportType, e);
        }
    }

    @Override
    public int deleteReportByIds(Long[] ids) {
        return reportMapper.deleteReportByIds(ids);

    }
}
