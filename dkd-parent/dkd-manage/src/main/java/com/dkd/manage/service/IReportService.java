package com.dkd.manage.service;

import java.util.List;
import com.dkd.manage.domain.Report;

/**
 * 报表Service接口
 *
 * @author ruoyi
 * @date 2026-04-22
 */
public interface IReportService
{
    public Report selectReportById(Long id);

    public List<Report> selectReportList(Report report);

    public Report selectLatestDailyReport();

    public Report selectLatestWeeklyReport();

    public int insertReport(Report report);

    public int updateReport(Report report);

    public void generateDailyReport();

    public void generateWeeklyReport();

    public void generateReport(String reportType);

    int deleteReportByIds(Long[] ids);
}
