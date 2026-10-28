package com.dkd.manage.task;

import com.dkd.manage.service.IReportService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * AI 报表定时任务（Quartz 调用入口）。
 *
 * <p>为什么失败时必须**抛出异常**而不是只 log（AGENTS §6.4 第 4 条 / 业务规范 §4.7）：
 * RuoYi 的 {@code AbstractQuartzJob} 只有捕获到异常才会把本次执行写成
 * {@code sys_job_log.status=1 + exception_info}；原来这里的 {@code catch (Exception) { log.error }} }
 * 把失败吞掉，调度器看到的是“成功”，于是“日报连续几天没生成”没有任何人会发现
 * ——正是 §6.4 点名的“半夜悄悄失败”。
 *
 * <p>异常消息带**任务名 + 原因**，便于在 sys_job_log 里直接定位；原始异常挂 cause 保留堆栈。
 * 注意只透传异常 message，不把上游完整响应体写进日志（AGENTS §6.1）。
 *
 * @author ruoyi
 * @date 2026-04-22
 */
@Component("reportTask")
public class ReportTask {

    private static final Logger log = LoggerFactory.getLogger(ReportTask.class);

    /** 与 sys_job.job_name 保持一致，便于 sys_job_log.exception_info 与任务清单对照 */
    private static final String DAILY_TASK_NAME = "AI运营日报";
    private static final String WEEKLY_TASK_NAME = "AI运营周报";

    @Autowired
    private IReportService reportService;

    public void generateDailyReport() {
        log.info("开始生成日报...");
        try {
            reportService.generateDailyReport();
            log.info("日报生成完成");
        } catch (Exception e) {
            log.error("日报生成异常", e);
            // 必须抛：否则 AbstractQuartzJob 记不到失败（sys_job_log.status=1），失败不可见
            throw new RuntimeException(DAILY_TASK_NAME + "生成失败：" + e.getMessage(), e);
        }
    }

    public void generateWeeklyReport() {
        log.info("开始生成周报...");
        try {
            reportService.generateWeeklyReport();
            log.info("周报生成完成");
        } catch (Exception e) {
            log.error("周报生成异常", e);
            // 同上：最终失败必须冒泡给调度器，不允许“log 完当成功”
            throw new RuntimeException(WEEKLY_TASK_NAME + "生成失败：" + e.getMessage(), e);
        }
    }
}
