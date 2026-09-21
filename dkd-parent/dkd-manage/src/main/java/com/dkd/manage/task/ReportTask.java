package com.dkd.manage.task;

import com.dkd.manage.service.IReportService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

@Component("reportTask")
public class ReportTask {

    private static final Logger log = LoggerFactory.getLogger(ReportTask.class);

    @Autowired
    private IReportService reportService;

    public void generateDailyReport() {
        log.info("开始生成日报...");
        try {
            reportService.generateDailyReport();
            log.info("日报生成完成");
        } catch (Exception e) {
            log.error("日报生成异常", e);
        }
    }

    public void generateWeeklyReport() {
        log.info("开始生成周报...");
        try {
            reportService.generateWeeklyReport();
            log.info("周报生成完成");
        } catch (Exception e) {
            log.error("周报生成异常", e);
        }
    }
}
