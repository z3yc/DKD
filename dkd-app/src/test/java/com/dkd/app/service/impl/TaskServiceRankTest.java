package com.dkd.app.service.impl;

import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.test.util.ReflectionTestUtils;

import com.dkd.app.common.exception.LogicException;
import com.dkd.app.mapper.TaskMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 员工排名口径的单元测试（P2-26）。
 *
 * <p>## 这批用例防什么
 * 原 {@code TaskController.rank()} 返回硬编码 {@code {total:3, rank:3}}——移动端首页排名是假的。
 * 改造后排名来自两条真实 SQL，本测试钉住 Service 层的口径：
 * <ul>
 *   <li><b>非法登录人</b>：null / 0 / 负数必须抛 {@link LogicException}「未获取到登录人信息」，
 *       且不查库（否则会把 0 号用户当合法 id 去统计）；</li>
 *   <li><b>名次公式</b>：领先 0 人 → rank=1；领先 2 人 → rank=3（rank = 严格更多人 + 1，1-based）；</li>
 *   <li><b>并列最小名次</b>：用「严格大于我」计数，同分者互不领先（本测试以 ahead 计数语义间接钉住）；</li>
 *   <li><b>total 透传</b>：Mapper 的 COUNT 原样给前端，不做二次加工。</li>
 * </ul>
 *
 * <p>## 为什么不连真库
 * AGENTS §8：Java 单测不依赖 MySQL/Redis。排名口径是「计数 + 加一」的纯逻辑；
 * SQL 形状（{@code task_status=4}、{@code GROUP BY ... HAVING}）由 Mapper 注释与
 * 契约表 {@code docs/CONTRACTS/status-codes.md} 约束，真库行为留待 D 阶段验收。
 *
 * <p>## 为什么显式塞 baseMapper
 * {@link TaskServiceImpl} 继承 MyBatis-Plus {@code ServiceImpl}，Mapper 字段 {@code baseMapper}
 * 在父类里由 Spring {@code @Autowired} 注入。纯 Mockito 单测不启 Spring 容器，
 * 这里用 {@code ReflectionTestUtils} 显式注入 mock——不依赖 Mockito 对父类字段的注入行为，
 * 避免测试因版本差异 NPE。
 *
 * @author ruoyi
 * @date 2026-10-29
 */
@ExtendWith(MockitoExtension.class)
// 不同用例覆盖不同分支，未打桩的协作对象在部分分支不会被调用
@MockitoSettings(strictness = Strictness.LENIENT)
class TaskServiceRankTest
{
    private static final Long USER_ID = 7L;

    @Mock
    private TaskMapper taskMapper;

    private TaskServiceImpl taskService;

    @BeforeEach
    void setUp()
    {
        taskService = new TaskServiceImpl();
        ReflectionTestUtils.setField(taskService, "baseMapper", taskMapper);
    }

    @Test
    void 登录人Id为空时拒绝且不查库()
    {
        assertThatThrownBy(() -> taskService.getRank(null))
                .isInstanceOf(LogicException.class)
                .hasMessage("未获取到登录人信息");
        verifyNoInteractions(taskMapper);
    }

    @Test
    void 登录人Id非正数时拒绝且不查库()
    {
        assertThatThrownBy(() -> taskService.getRank(0L))
                .isInstanceOf(LogicException.class)
                .hasMessage("未获取到登录人信息");
        assertThatThrownBy(() -> taskService.getRank(-5L))
                .isInstanceOf(LogicException.class)
                .hasMessage("未获取到登录人信息");
        verifyNoInteractions(taskMapper);
    }

    @Test
    void 领先零人时名次为一()
    {
        when(taskMapper.countFinishedByUserId(USER_ID)).thenReturn(5L);
        when(taskMapper.countUsersAhead(5L)).thenReturn(0L);

        Map<String, Object> result = taskService.getRank(USER_ID);

        assertThat(result)
                .containsEntry("total", 5L)
                .containsEntry("rank", 1L);
        // 领先人数查询必须用「我的完成数」做门槛，不能传 userId 或写死
        verify(taskMapper).countFinishedByUserId(USER_ID);
        verify(taskMapper).countUsersAhead(5L);
    }

    @Test
    void 领先两人时名次为三()
    {
        when(taskMapper.countFinishedByUserId(USER_ID)).thenReturn(5L);
        when(taskMapper.countUsersAhead(5L)).thenReturn(2L);

        Map<String, Object> result = taskService.getRank(USER_ID);

        assertThat(result)
                .as("rank = 严格更多的人 + 1（1-based），并列取最小名次")
                .containsEntry("total", 5L)
                .containsEntry("rank", 3L);
    }

    @Test
    void 没有任何完成工单时total为零名次为一()
    {
        when(taskMapper.countFinishedByUserId(USER_ID)).thenReturn(0L);
        when(taskMapper.countUsersAhead(0L)).thenReturn(0L);

        Map<String, Object> result = taskService.getRank(USER_ID);

        assertThat(result)
                .containsEntry("total", 0L)
                .containsEntry("rank", 1L);
    }

    @Test
    void mapper返回null时兜底为零单与第一名()
    {
        // COUNT 聚合正常不会返回 null；但连接/映射异常时不能把 null 塞给前端
        when(taskMapper.countFinishedByUserId(USER_ID)).thenReturn(null);
        when(taskMapper.countUsersAhead(0L)).thenReturn(null);

        Map<String, Object> result = taskService.getRank(USER_ID);

        assertThat(result)
                .containsEntry("total", 0L)
                .containsEntry("rank", 1L);
    }
}
