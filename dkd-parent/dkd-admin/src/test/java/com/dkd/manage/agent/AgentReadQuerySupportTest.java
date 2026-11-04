package com.dkd.manage.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.text.SimpleDateFormat;
import java.util.Date;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import com.dkd.common.exception.ServiceException;
import com.dkd.manage.domain.AgentLog;
import com.github.pagehelper.Page;
import com.github.pagehelper.PageHelper;

/**
 * 智能体只读查询护栏单测（Batch A · 排期 2-4 / 2-5）。
 *
 * <p>被测语义只有两条，但两条都是"该拒绝的必须拒绝"（AGENTS §8：拒绝路径优先）：
 * <ol>
 *   <li><b>时间窗</b>：默认近 24h、最大 30 天，超限/格式错/起止颠倒必须明确报错；
 *       尤其"格式错"不能静默当成没传——否则用户看到一堆无关数据却没有任何提示；</li>
 *   <li><b>分页上限</b>：{@code pageSize > 200} 必须拒绝。单主库无只读从库，
 *       放开上限等于允许前端一次拉全表（AGENTS §1 约束 5 / §10）。</li>
 * </ol>
 *
 * <p>纯内存测试：不连 MySQL、不启 Spring 容器（AGENTS §8）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
class AgentReadQuerySupportTest
{
    private static final long ONE_HOUR_MS = 60L * 60L * 1000L;

    /** 固定"当前时间"，避免用例依赖真实时钟 */
    private static final String NOW_TEXT = "2026-11-04 12:00:00";

    private static Date at(String text) throws Exception
    {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").parse(text);
    }

    @AfterEach
    void clearThreadLocal()
    {
        // PageHelper 的分页条件是 ThreadLocal：测试里必须清掉，否则会串到别的用例
        PageHelper.clearPage();
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    @DisplayName("都不传时间：默认最近 24 小时")
    void defaultsToLast24Hours() throws Exception
    {
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow(null, null, at(NOW_TEXT));

        assertThat(window.getBeginTime()).isEqualTo(at("2026-11-03 12:00:00"));
        assertThat(window.getEndTime()).isEqualTo(at(NOW_TEXT));
    }

    @Test
    @DisplayName("只传开始时间：按 24 小时窗口向后推导（不依赖时钟，便于复现）")
    void derivesEndFromBegin() throws Exception
    {
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow("2026-11-01 08:30:00", null, at(NOW_TEXT));

        assertThat(window.getBeginTime()).isEqualTo(at("2026-11-01 08:30:00"));
        assertThat(window.getEndTime()).isEqualTo(at("2026-11-02 08:30:00"));
    }

    @Test
    @DisplayName("只传结束时间：向前推 24 小时")
    void derivesBeginFromEnd() throws Exception
    {
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow(null, "2026-11-04 09:00:00", at(NOW_TEXT));

        assertThat(window.getBeginTime()).isEqualTo(at("2026-11-03 09:00:00"));
        assertThat(window.getEndTime()).isEqualTo(at("2026-11-04 09:00:00"));
    }

    @Test
    @DisplayName("纯日期上界补齐到当天 23:59:59.999（否则用户选到某天会看不到当天日志）")
    void dateOnlyEndIncludesWholeDay() throws Exception
    {
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow("2026-11-01", "2026-11-03", at(NOW_TEXT));

        assertThat(window.getBeginTime()).isEqualTo(at("2026-11-01 00:00:00"));
        // 11-03 当天整日：23:59:59.999
        assertThat(window.getEndTime().getTime())
                .isEqualTo(at("2026-11-03 23:59:59").getTime() + 999L);
    }

    @Test
    @DisplayName("秒级上界补齐到该秒末尾（DATETIME(3) 里同一秒的日志也要包含）")
    void secondPrecisionEndIncludesWholeSecond() throws Exception
    {
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow(
                "2026-11-04 10:00:00", "2026-11-04 10:00:05", at(NOW_TEXT));

        assertThat(window.getEndTime().getTime())
                .isEqualTo(at("2026-11-04 10:00:05").getTime() + 999L);
    }

    @Test
    @DisplayName("恰好 30 天的日期范围可以通过（上界补齐带来的 <1 秒误差要容忍）")
    void thirtyDayRangeIsAllowed() throws Exception
    {
        // 10-06 ~ 11-04 恰好 30 个自然日
        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow("2026-10-06", "2026-11-04", at(NOW_TEXT));

        long span = window.getEndTime().getTime() - window.getBeginTime().getTime();
        assertThat(span).isLessThanOrEqualTo(30L * 24L * ONE_HOUR_MS + 1000L);
    }

    @Test
    @DisplayName("超过 30 天：拒绝且提示里写清上限与实际天数（不是静默截断）")
    void rejectsRangeBeyondThirtyDays() throws Exception
    {
        assertThatThrownBy(() -> AgentReadQuerySupport.resolveWindow("2026-10-01", "2026-11-04", at(NOW_TEXT)))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("最大 30 天")
                .hasMessageContaining("当前约 35 天");
    }

    @Test
    @DisplayName("起止颠倒：拒绝并给出明确原因")
    void rejectsReversedRange() throws Exception
    {
        assertThatThrownBy(() -> AgentReadQuerySupport.resolveWindow("2026-11-05", "2026-11-04", at(NOW_TEXT)))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("开始时间不能晚于结束时间");
    }

    @Test
    @DisplayName("格式非法：明确报错，不能静默当成没传（否则用户查不到还以为是数据问题）")
    void rejectsInvalidFormatInsteadOfFallingBackToDefault() throws Exception
    {
        assertThatThrownBy(() -> AgentReadQuerySupport.resolveWindow("2026-13-40", null, at(NOW_TEXT)))
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("beginTime 格式不合法")
                .hasMessageContaining("2026-13-40");
    }

    @Test
    @DisplayName("兼容 RuoYi 的 params.beginTime 形式（前端两种传法都不会失效）")
    void supportsRuoYiParamsStyle() throws Exception
    {
        AgentLog query = new AgentLog();
        query.getParams().put("beginTime", "2026-11-01 00:00:00");

        AgentQueryWindow window = AgentReadQuerySupport.resolveWindow(query);

        // 只给了下界 → 上界 = 下界 + 24h（确定性，可断言）
        assertThat(window.getBeginTime()).isEqualTo(at("2026-11-01 00:00:00"));
        assertThat(window.getEndTime()).isEqualTo(at("2026-11-02 00:00:00"));
    }

    @Test
    @DisplayName("pageSize 超过 200：拒绝并给出明确提示")
    void rejectsOversizedPage()
    {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setParameter("pageNum", "1");
        request.setParameter("pageSize", "500");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));

        assertThatThrownBy(() -> AgentReadQuerySupport.startBoundedPage())
                .isInstanceOf(ServiceException.class)
                .hasMessageContaining("pageSize 最大 200");
    }

    @Test
    @DisplayName("分页参数缺省：pageNum=1、pageSize=10")
    void appliesDefaultPagination()
    {
        MockHttpServletRequest request = new MockHttpServletRequest();
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));

        AgentReadQuerySupport.startBoundedPage();

        Page<?> page = PageHelper.getLocalPage();
        assertThat(page).isNotNull();
        assertThat(page.getPageNum()).isEqualTo(1);
        assertThat(page.getPageSize()).isEqualTo(10);
    }

    @Test
    @DisplayName("分页参数在合法区间内：原样生效（20/页）")
    void keepsValidPageSize()
    {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setParameter("pageNum", "3");
        request.setParameter("pageSize", "20");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));

        AgentReadQuerySupport.startBoundedPage();

        Page<?> page = PageHelper.getLocalPage();
        assertThat(page.getPageNum()).isEqualTo(3);
        assertThat(page.getPageSize()).isEqualTo(20);
    }
}
