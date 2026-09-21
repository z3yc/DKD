package com.dkd.manage.controller;

import java.util.List;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.core.page.TableDataInfo;
import com.dkd.manage.domain.Report;
import com.dkd.manage.service.IReportService;

/**
 * 报表Controller
 *
 * @author ruoyi
 * @date 2026-04-22
 */
@RestController
@RequestMapping("/manage/report")
public class ReportController extends BaseController
{
    @Autowired
    private IReportService reportService;

    @PreAuthorize("@ss.hasPermi('manage:report:query')")
    @GetMapping("/daily")
    public AjaxResult daily()
    {
        return AjaxResult.success(reportService.selectLatestDailyReport());
    }

    @PreAuthorize("@ss.hasPermi('manage:report:query')")
    @GetMapping("/weekly")
    public AjaxResult weekly()
    {
        return AjaxResult.success(reportService.selectLatestWeeklyReport());
    }

    @PreAuthorize("@ss.hasPermi('manage:report:list')")
    @GetMapping("/list")
    public TableDataInfo list(Report report)
    {
        startPage();
        List<Report> list = reportService.selectReportList(report);
        return getDataTable(list);
    }

    @PreAuthorize("@ss.hasPermi('manage:report:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return AjaxResult.success(reportService.selectReportById(id));
    }

    @PreAuthorize("@ss.hasPermi('manage:report:generate')")
    @PostMapping("/generate")
    public AjaxResult generate(String reportType)
    {
        reportService.generateReport(reportType != null ? reportType : "daily");
        return AjaxResult.success();
    }
   //删除接口
    @PreAuthorize("@ss.hasPermi('manage:report:remove')")
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids){
        return toAjax(reportService.deleteReportByIds(ids));
    }
}
