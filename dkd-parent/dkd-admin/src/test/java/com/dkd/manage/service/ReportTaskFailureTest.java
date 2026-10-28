package com.dkd.manage.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.dkd.manage.task.ReportTask;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;

/**
 * 报表定时任务「失败必须抛异常」回归测试（AGENTS §6.4 第 4 条 / 业务规范 §4.7）。
 *
 * <p>## 防什么
 * 原 {@code ReportTask} 在 catch 里只 {@code log.error} 就返回，RuoYi 的
 * {@code AbstractQuartzJob} 认为本次执行成功，{@code sys_job_log.status} 永远是 0。
 * 结果是「日报连续几天没生成」没有任何告警——正是 §6.4 点名的“半夜悄悄失败”。
 * 现在异常必须冒泡，且消息带任务名（便于在 sys_job_log.exception_info 里定位）。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@ExtendWith(MockitoExtension.class)
class ReportTaskFailureTest
{
    @Mock
    private IReportService reportService;

    @InjectMocks
    private ReportTask reportTask;

    @Test
    void 日报生成失败必须抛异常并带任务名与原因()
    {
        doThrow(new IllegalStateException("连接池耗尽")).when(reportService).generateDailyReport();

        assertThatThrownBy(() -> reportTask.generateDailyReport())
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("AI运营日报")
                .hasMessageContaining("连接池耗尽")
                .hasCauseInstanceOf(IllegalStateException.class);
    }

    @Test
    void 周报生成失败必须抛异常并带任务名与原因()
    {
        doThrow(new IllegalStateException("表不存在")).when(reportService).generateWeeklyReport();

        assertThatThrownBy(() -> reportTask.generateWeeklyReport())
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("AI运营周报")
                .hasMessageContaining("表不存在")
                .hasCauseInstanceOf(IllegalStateException.class);
    }

    @Test
    void 成功时不得误抛()
    {
        assertThatCode(() -> reportTask.generateDailyReport()).doesNotThrowAnyException();
        verify(reportService).generateDailyReport();
    }
}
