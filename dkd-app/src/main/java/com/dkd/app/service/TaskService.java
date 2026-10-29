package com.dkd.app.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.dkd.app.domain.Task;
import com.dkd.app.domain.dto.CancelTaskDto;
import com.dkd.app.domain.vo.Pager;

import java.time.LocalDate;
import java.util.Map;

/**
 * 工单业务逻辑
 */
public interface TaskService extends IService<Task> {

    // 通过条件搜索工单列表
    Pager<Task> search(Long pageIndex, Long pageSize, String innerCode, Integer userId, String taskCode, Integer status, Boolean isRepair, LocalDate start, LocalDate end);

    // 接受工单
    Boolean accept(Long taskId, Integer userId);

    // 拒绝/取消工单
    Boolean cancel(Long taskId, CancelTaskDto cancelTaskDto, Integer userId);

    // 完成工单
    Boolean complete(Long taskId, Integer userId);

    /**
     * 查询员工排名（P2-26，替换 Controller 里原先的硬编码假数据）。
     *
     * <p>口径（**自主决策，待产品确认**）：
     * <ul>
     *   <li>{@code total} = 该员工<b>历史累计已完成</b>工单数（task_status=4，不限时间窗口）；</li>
     *   <li>{@code rank} = 按已完成数降序的名次，<b>1-based</b>，<b>并列取最小名次</b>
     *       （rank = 完成数严格更多的人 + 1）；</li>
     *   <li>只统计已完成工单：进行中/待处理/已取消都不计入。</li>
     * </ul>
     * 若产品要「本月/本季」口径，只需在 Mapper SQL 加时间条件，接口结构不变。
     *
     * @param userId 员工Id（必须 &gt; 0，否则说明登录态缺失）
     * @return {@code {total: Long, rank: Long}}，供移动端首页展示
     * @throws com.dkd.app.common.exception.LogicException userId 为空/非正数时
     */
    Map<String, Object> getRank(Long userId);
}
