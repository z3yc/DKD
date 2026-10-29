package com.dkd.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.dkd.app.domain.Task;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface TaskMapper extends BaseMapper<Task> {

    /**
     * 统计某员工历史累计已完成工单数（P2-26 排名口径之一）。
     *
     * <p>口径：{@code task_status = 4} 是「已完成」（{@code VmSystemConstant.TASK_STATUS_FINISH}），
     * 字面量取值见 {@code docs/CONTRACTS/status-codes.md}「工单状态 tb_task.task_status」。
     * 注解 SQL 无法引用 Java 常量，故用本注释与常量双向绑定——改契约时两处一起改。
     *
     * <p>不按时间窗口过滤 = 历史累计口径（待产品确认，见 {@code TaskServiceImpl.getRank}）。
     *
     * @param userId 员工Id
     * @return 已完成工单数
     */
    @Select("SELECT COUNT(*) FROM tb_task WHERE user_id = #{userId} AND task_status = 4")
    Long countFinishedByUserId(@Param("userId") Long userId);

    /**
     * 统计「已完成工单数严格多于我」的人数，即排名领先人数（P2-26）。
     *
     * <p>口径：按 {@code user_id} 分组统计每个员工的历史已完成数（{@code task_status = 4}，
     * 见 {@code docs/CONTRACTS/status-codes.md}），再数其中严格大于「我的完成数」的员工。
     * 排名 = 领先人数 + 1（1-based）；并列同分取最小名次（competition ranking）——
     * 例如我 5 单、有 2 人 7 单，则 rank=3；两人都是 5 单则两人 rank 相同。
     *
     * <p>为什么排除 {@code user_id IS NULL}：未指派工单不该被当成一个「员工」参与排名。
     *
     * @param myFinishedCount 我的历史已完成工单数
     * @return 已完成数严格多于我的人数
     */
    @Select("SELECT COUNT(*) FROM ("
            + "SELECT user_id FROM tb_task WHERE task_status = 4 AND user_id IS NOT NULL "
            + "GROUP BY user_id HAVING COUNT(*) > #{myFinishedCount}) ahead")
    Long countUsersAhead(@Param("myFinishedCount") Long myFinishedCount);
}
