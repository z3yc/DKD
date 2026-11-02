package com.dkd.app.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.dkd.app.domain.Task;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;

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

    /**
     * 条件接单（P0-6）：把「状态仍是待处理 + 执行人是我」放进同一条 UPDATE 的 WHERE。
     *
     * <p>为什么用注解 SQL 而不是 MyBatis-Plus 的 {@code LambdaUpdateWrapper}：
     * <ol>
     *   <li>并发正确性靠这条 SQL 的语义，写死在 Mapper 里最不容易被后来者改回 check-then-act；</li>
     *   <li>{@code LambdaUpdateWrapper} 依赖实体的 lambda 元数据缓存，纯单测（不启 Spring）会抛
     *       {@code can not find lambda cache for this entity}，SQL 就没法被单测覆盖到；</li>
     *   <li>注解 SQL 不能引用 Java 常量，故状态值一律由调用方以参数传入（不在 SQL 里写死 1/2），
     *       契约仍由 {@code docs/CONTRACTS/status-codes.md} 单一维护。</li>
     * </ol>
     *
     * <p>返回 0 表示「并发下已被别人改走 / 不存在 / 不是我的单」——由 Service 回查后归因，
     * 不能靠影响行数区分原因。
     *
     * @param taskId 工单Id
     * @param userId 登录人Id
     * @param expectStatus 期望的当前状态（待处理）
     * @param targetStatus 目标状态（进行中）
     * @param updateTime 更新时间（应用侧统一时钟，与 MyBatis-Plus 的自动填充保持一致）
     * @return 影响行数（1 成功，0 未命中）
     */
    @Update("UPDATE tb_task SET task_status = #{targetStatus}, update_time = #{updateTime} "
            + "WHERE task_id = #{taskId} AND task_status = #{expectStatus} AND user_id = #{userId}")
    int acceptIfPending(@Param("taskId") Long taskId,
                        @Param("userId") Integer userId,
                        @Param("expectStatus") Integer expectStatus,
                        @Param("targetStatus") Integer targetStatus,
                        @Param("updateTime") LocalDateTime updateTime);

    /**
     * 条件取消/拒绝工单（P0-6）：只有「未完成且未取消」且执行人是我的单才能被改。
     *
     * <p>与 {@link #acceptIfPending} 同一思路；额外写入驳回原因 {@code desc}
     * （列名是 MySQL 关键字，必须反引号）。
     *
     * @param taskId 工单Id
     * @param userId 登录人Id
     * @param finishedStatus 已完成状态（排除值之一）
     * @param cancelledStatus 已取消状态（排除值之一）
     * @param targetStatus 目标状态（已取消）
     * @param desc 取消/拒绝原因
     * @param updateTime 更新时间
     * @return 影响行数（1 成功，0 未命中）
     */
    @Update("UPDATE tb_task SET task_status = #{targetStatus}, `desc` = #{desc}, update_time = #{updateTime} "
            + "WHERE task_id = #{taskId} AND task_status NOT IN (#{finishedStatus}, #{cancelledStatus}) "
            + "AND user_id = #{userId}")
    int cancelIfActive(@Param("taskId") Long taskId,
                       @Param("userId") Integer userId,
                       @Param("finishedStatus") Integer finishedStatus,
                       @Param("cancelledStatus") Integer cancelledStatus,
                       @Param("targetStatus") Integer targetStatus,
                       @Param("desc") String desc,
                       @Param("updateTime") LocalDateTime updateTime);
}
