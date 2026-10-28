package com.dkd.manage.service.impl;

import java.text.SimpleDateFormat;
import java.util.*;
import com.dkd.common.constant.DkdContants;
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

    /** 统计营收/订单只认「出货成功」（业务规范 §4.5；3-出货失败、4-失效不得计入） */
    private static final int ORDER_STATUS_SUCCESS = 2;

    /** tb_report.status：0-生成中 1-成功 2-失败 */
    private static final int REPORT_STATUS_FAILED = 2;

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

    /**
     * 生成指定类型的报表（daily/weekly）。
     *
     * <p>为什么这里**不再**用 try/catch 包住全流程（业务规范 §4.7 / AGENTS §6.4）：
     * 原实现把整段逻辑包在 {@code catch (Exception e) { log.error(...) }} 里，SQL 失败、
     * upsert 失败都会变成“接口返回成功、Quartz 也记录成功”，报表缺数据却无人知晓。
     * 现在让异常原样上抛：手工触发时由全局异常处理器返回可读错误，定时任务由
     * {@code ReportTask} 转成 RuntimeException 写入 {@code sys_job_log.status=1}。
     *
     * <p>AI 分析失败仍是**降级**而非报表失败（§2.5「LLM 失败降级为纯数据」）：
     * 只吞 AI 子链路的异常并写 status=2，报表数字照常入库。
     *
     * @param reportType daily / weekly
     */
    @Override
    public void generateReport(String reportType)
    {
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

        Report report = buildReport(reportType, reportDate, beginTime, endTimeStr);
        // 同名同日期覆盖（tb_report.uk_type_date），见 ReportMapper.insertOrIgnoreReport 的 upsert 说明。
        // 若库层失败（例如连接中断），异常向上抛，不允许“报表没生成却报成功”。
        reportMapper.insertOrIgnoreReport(report);
        log.info("{}报表生成完成，日期：{}，AI状态：{}", reportType,
                new SimpleDateFormat("yyyy-MM-dd").format(reportDate),
                Integer.valueOf(REPORT_STATUS_FAILED).equals(report.getStatus()) ? "失败" : "成功");
    }

    /**
     * 聚合指标 + 设备统计 + AI 解读，组装待入库的报表实体。
     *
     * @param reportType daily / weekly
     * @param reportDate 报表归属日期
     * @param beginTime 统计窗口开始（含）
     * @param endTimeStr 统计窗口结束（不含，半开区间）
     * @return 报表实体（未入库）
     */
    private Report buildReport(String reportType, Date reportDate, String beginTime, String endTimeStr)
    {
        // 聚合数据（四条 SQL 统一限定 status=2 + 半开时间窗，见 ReportMapper.xml 顶部说明）
        Long totalRevenue = reportMapper.sumRevenueByStatusAndDateRange(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);
        Integer totalOrders = reportMapper.countOrdersByStatusAndDateRange(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);
        List<Map<String, Object>> topNodes = reportMapper.topNodesByRevenue(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);
        List<Map<String, Object>> topProducts = reportMapper.topProductsBySales(ORDER_STATUS_SUCCESS, beginTime, endTimeStr);

        // 设备统计
        List<VendingMachine> allVm = vendingMachineMapper.selectVendingMachineList(new VendingMachine());
        if (allVm == null) {
            allVm = Collections.emptyList();
        }
        int totalDevices = allVm.size();
        int onlineDevices = countOnlineDevices(allVm);
        int faultDevices = countFaultDevices(allVm);

        // 库存不足设备数：Mapper 以 vm_id 去重统计（同一台设备多个货道只算一台，业务规范 §4.5）
        Integer lowStockDevices = reportMapper.countLowStockDevices();

        // 构建AI分析数据
        Map<String, Object> contextData = aiReportService.buildReportDataContext(
                reportType, new SimpleDateFormat("yyyy-MM-dd").format(reportDate),
                totalRevenue, totalOrders, totalDevices, onlineDevices, faultDevices, lowStockDevices);
        contextData.put("高收入点位", topNodes);
        contextData.put("热销商品", topProducts);

        // 调用AI生成分析（失败降级为纯数据，不影响报表数字入库）
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
        report.setStatus(aiFailed ? REPORT_STATUS_FAILED : 1);
        report.setRetryCount(0);
        return report;
    }

    /**
     * 在线设备数：runningStatus 心跳 JSON 中 {@code status === true}（沿用设备列表页口径）。
     *
     * <p>脏 JSON 只影响该设备一台：记 debug 后按“离线”统计，不能因为一条坏心跳让整张日报失败。
     *
     * @param allVm 全部设备
     * @return 在线设备数
     */
    private int countOnlineDevices(List<VendingMachine> allVm)
    {
        int onlineDevices = 0;
        for (VendingMachine vm : allVm) {
            if (vm == null || vm.getRunningStatus() == null) {
                continue;
            }
            try {
                cn.hutool.json.JSONObject json = JSONUtil.parseObj(vm.getRunningStatus());
                if (json.getBool("status", false)) {
                    onlineDevices++;
                }
            } catch (Exception e) {
                log.debug("设备心跳状态 JSON 解析失败，按离线统计：innerCode={}", vm.getInnerCode(), e);
            }
        }
        return onlineDevices;
    }

    /**
     * 故障设备数：**仅 vm_status=2（停机）**。
     *
     * <p>为什么不能用“vm_status != 1”：未投放(0) 与已撤机(3) 是正常生命周期状态，
     * 把它们算成故障会让故障率虚高、AI 分析失真（业务规范 §4.5）。
     *
     * @param allVm 全部设备
     * @return 故障设备数
     */
    private int countFaultDevices(List<VendingMachine> allVm)
    {
        int faultDevices = 0;
        for (VendingMachine vm : allVm) {
            if (vm != null && DkdContants.VM_STATUS_STOPPED.equals(vm.getVmStatus())) {
                faultDevices++;
            }
        }
        return faultDevices;
    }

    @Override
    public int deleteReportByIds(Long[] ids) {
        return reportMapper.deleteReportByIds(ids);

    }
}
