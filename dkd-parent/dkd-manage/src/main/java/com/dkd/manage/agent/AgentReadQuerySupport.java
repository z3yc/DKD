package com.dkd.manage.agent;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Date;

import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.domain.AgentTimeRangeQuery;
import com.github.pagehelper.PageHelper;
import com.dkd.common.core.page.PageDomain;
import com.dkd.common.core.page.TableSupport;

/**
 * 智能体只读查询的**统一护栏**（Batch A · 排期 2-4 / 2-5）。
 *
 * <p>本类只有两件事，但两件都属于"必须在一处定死"的规则，散落到 Controller/Service 就会漂移：
 *
 * <ol>
 *   <li><b>时间窗</b>（{@link #resolveWindow}）：默认近 24h、最大 30 天，超限**拒绝并给出可读原因**。
 *       为什么必须强制：{@code agent_log} / {@code agent_message} 随每次对话持续增长，
 *       且本机/生产都是**单主库无只读从库**（AGENTS §1 约束 5）——不带时间窗的日志/会话查询
 *       会退化成全表扫描，跟业务抢同一个库。宁可让页面拿到一句"请缩小时间范围"，
 *       也不要让一个查"全部历史"的请求把主库拖慢。</li>
 *   <li><b>分页上限</b>（{@link #startBoundedPage}）：{@code pageSize} 上限 200。
 *       RuoYi 原生的 {@code startPage()} 不做上限，前端传 5000 就真的查 5000 行
 *       （PageHelper 会照单执行 LIMIT 5000），在单主库上是明确的风险。</li>
 * </ol>
 *
 * <p>其余过滤条件（level/status/runId/requestId/keyword…）不做业务校验：它们都是
 * 精确匹配或参数化的 {@code like}，SQL 全部 {@code #{}} 绑定，非法值只会查不到数据。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public final class AgentReadQuerySupport
{
    /** 未传时间范围时的默认窗口（小时） */
    public static final int DEFAULT_WINDOW_HOURS = 24;

    /** 时间窗上限（天）——超过直接拒绝，不是静默截断 */
    public static final int MAX_WINDOW_DAYS = 30;

    /** 单页最大条数 */
    public static final int MAX_PAGE_SIZE = 200;

    /** 未传 pageSize 时的默认值（与 RuoYi 默认一致） */
    public static final int DEFAULT_PAGE_SIZE = 10;

    /** 未传 pageNum 时的默认值 */
    public static final int DEFAULT_PAGE_NUM = 1;

    private static final long ONE_HOUR_MS = 60L * 60L * 1000L;

    private static final long ONE_DAY_MS = 24L * ONE_HOUR_MS;

    /**
     * 30 天 + 1 秒的容差：日期/秒精度归一（日期串补到 23:59:59.999）会让"恰好 30 天"的窗口
     * 多出不到 1 秒。若不容忍这一点，用户选"最近 30 天"会被莫名其妙地拒绝。
     * 容差只有 1 秒，不会让 31 天窗口混过去（差值恒 > 1 天）。
     */
    private static final long MAX_WINDOW_SPAN_MS = MAX_WINDOW_DAYS * ONE_DAY_MS + 1000L;

    /** 允许的时间格式；长度与格式一一对应，解析时用长度先做一次筛选 */
    private static final String[] TIME_PATTERNS = {
            "yyyy-MM-dd",
            "yyyy-MM-dd HH:mm",
            "yyyy-MM-dd HH:mm:ss",
            "yyyy-MM-dd HH:mm:ss.SSS" };

    private AgentReadQuerySupport()
    {
    }

    /**
     * 解析并校验查询对象上的时间窗（兼容 {@code params.beginTime} 形式）。
     *
     * @param query 查询对象；为 null 时抛业务异常
     * @return 归一化后的时间窗（下界/上界均含）
     * @throws ServiceException 格式非法、起止颠倒、超过 30 天
     */
    public static AgentQueryWindow resolveWindow(AgentTimeRangeQuery query)
    {
        if (query == null)
        {
            throw new ServiceException("查询条件不能为空");
        }
        return resolveWindow(rawBeginTime(query), rawEndTime(query), new Date());
    }

    /**
     * 解析并校验时间窗（供单测固定"当前时间"，避免用例依赖真实时钟）。
     *
     * <p>缺省规则（两端都缺省时就是"最近 24 小时"）：
     * <ul>
     *   <li>都未传：{@code [now-24h, now]}；</li>
     *   <li>只传 beginTime：{@code [begin, begin+24h]}——不依赖时钟，便于复现；</li>
     *   <li>只传 endTime：{@code [end-24h, end]}。</li>
     * </ul>
     *
     * @param beginText beginTime 原文（可为空）
     * @param endText   endTime 原文（可为空）
     * @param now       当前时间（测试注入；null 时取系统时间）
     * @return 归一化后的时间窗
     * @throws ServiceException 格式非法、起止颠倒、超过 30 天
     */
    public static AgentQueryWindow resolveWindow(String beginText, String endText, Date now)
    {
        Date current = now == null ? new Date() : now;
        Date begin = parseTime(beginText, "beginTime");
        Date end = parseTime(endText, "endTime");

        if (begin == null && end == null)
        {
            end = current;
            begin = new Date(end.getTime() - DEFAULT_WINDOW_HOURS * ONE_HOUR_MS);
        }
        else if (begin == null)
        {
            begin = new Date(end.getTime() - DEFAULT_WINDOW_HOURS * ONE_HOUR_MS);
        }
        else if (end == null)
        {
            end = new Date(begin.getTime() + DEFAULT_WINDOW_HOURS * ONE_HOUR_MS);
        }
        else
        {
            // 两端都显式给出时，上界按请求精度"包含到该精度末尾"
            end = inclusiveEnd(end, endText);
        }

        if (begin.after(end))
        {
            throw new ServiceException("开始时间不能晚于结束时间（beginTime="
                    + StringUtils.nvl(beginText, "") + "，endTime=" + StringUtils.nvl(endText, "") + "）");
        }

        long span = end.getTime() - begin.getTime();
        if (span > MAX_WINDOW_SPAN_MS)
        {
            // 向上取整成"天"，让提示与用户选择的日期范围一致（如"当前约 31 天"）
            long days = (span + ONE_DAY_MS - 1) / ONE_DAY_MS;
            throw new ServiceException("查询时间范围最大 " + MAX_WINDOW_DAYS + " 天（当前约 " + days
                    + " 天），请缩小 beginTime/endTime 后重试");
        }
        return new AgentQueryWindow(begin, end);
    }

    /**
     * 启动分页，并强制 {@code pageSize <= 200}。
     *
     * <p>为什么是"拒绝"而不是"静默截断到 200"：静默截断会让页面显示"第 1 页 200 条"，
     * 而用户请求的是 500 条——看起来像数据变少了，排障时会先怀疑数据而不是分页参数。
     * 明确报错才能让人改请求。
     *
     * <p>注意调用顺序：必须先校验业务护栏（时间窗）再调用本方法。PageHelper 的分页条件是
     * <b>线程变量</b>，只有真正执行一次查询才会被消费；先分页后校验失败会让它残留在线程上，
     * 污染同一线程的下一次查询（表现为"随便点个详情只返回了 10 条"）。
     *
     * @throws ServiceException pageSize 超过 200
     */
    public static void startBoundedPage()
    {
        PageDomain pageDomain = TableSupport.buildPageRequest();
        Integer pageNum = pageDomain.getPageNum();
        Integer pageSize = pageDomain.getPageSize();
        if (pageNum == null || pageNum.intValue() < 1)
        {
            pageNum = Integer.valueOf(DEFAULT_PAGE_NUM);
        }
        if (pageSize == null || pageSize.intValue() < 1)
        {
            pageSize = Integer.valueOf(DEFAULT_PAGE_SIZE);
        }
        if (pageSize.intValue() > MAX_PAGE_SIZE)
        {
            throw new ServiceException("pageSize 最大 " + MAX_PAGE_SIZE + "（请求 " + pageSize
                    + "），请调小分页或缩小时间范围后重试");
        }
        // 不接收前端 orderBy：两个列表的排序由 SQL 固定（日志/会话都按时间倒序），
        // 放开 orderByColumn 等于把排序列暴露成可拼接口，收益为零、风险为正
        PageHelper.startPage(pageNum.intValue(), pageSize.intValue()).setReasonable(Boolean.TRUE);
    }

    /**
     * 解析单个时间字符串；空串视为"未传"，非法格式抛业务异常。
     *
     * <p>为什么不用 {@code DateUtils.parseDate}：它解析失败会**静默返回 null**
     * （RuoYi 惯例），于是一个拼错的时间会被当成"没传"而悄悄退化为默认 24h——
     * 用户看到的是一堆"不相关"的日志，却没有任何提示。这里选择明确报错。
     *
     * @param text  原文
     * @param field 字段名（用于错误提示）
     * @return 解析结果；未传返回 null
     */
    private static Date parseTime(String text, String field)
    {
        if (StringUtils.isEmpty(text))
        {
            return null;
        }
        String value = text.trim();
        for (String pattern : TIME_PATTERNS)
        {
            if (value.length() != pattern.length())
            {
                continue;
            }
            SimpleDateFormat format = new SimpleDateFormat(pattern);
            // 非宽松：2026-13-40 这类"看起来像日期"的值必须报错，不能被顺延成别的日子
            format.setLenient(false);
            try
            {
                return format.parse(value);
            }
            catch (ParseException ignored)
            {
                // 长度相同但内容不符（如 2026-xx-04），继续试下一个格式；
                // 全部失败后统一抛下面的业务异常（不允许静默当成"未传"）
            }
        }
        throw new ServiceException(field + " 格式不合法：" + value
                + "（支持 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss）");
    }

    /**
     * 把上界补齐到请求精度的末尾，使 SQL 用闭区间比较即可。
     *
     * @param end     解析出的上界
     * @param rawText 上界原文（null 表示该端是推导出来的，不做补齐）
     * @return 补齐后的上界
     */
    private static Date inclusiveEnd(Date end, String rawText)
    {
        if (rawText == null)
        {
            return end;
        }
        int length = rawText.trim().length();
        if (length == 10)
        {
            // 纯日期：包含当天 23:59:59.999（DATETIME(3) 的最大粒度）
            return new Date(end.getTime() + ONE_DAY_MS - 1L);
        }
        if (length < 23)
        {
            // 只精确到分/秒：包含该秒的 .999 毫秒
            return new Date(end.getTime() + 999L);
        }
        return end;
    }

    /** 请求上的 beginTime；为空时回落 RuoYi 约定的 {@code params.beginTime}。 */
    private static String rawBeginTime(AgentTimeRangeQuery query)
    {
        return firstNonEmpty(query.getBeginTime(), asText(query.getParams().get("beginTime")));
    }

    /** 请求上的 endTime；为空时回落 RuoYi 约定的 {@code params.endTime}。 */
    private static String rawEndTime(AgentTimeRangeQuery query)
    {
        return firstNonEmpty(query.getEndTime(), asText(query.getParams().get("endTime")));
    }

    private static String firstNonEmpty(String first, String second)
    {
        return StringUtils.isNotEmpty(first) ? first : second;
    }

    private static String asText(Object value)
    {
        return value == null ? null : String.valueOf(value);
    }
}
