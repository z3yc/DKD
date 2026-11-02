package com.dkd.app.service.impl;

import com.dkd.app.common.constant.VmSystemConstant;
import com.dkd.app.common.exception.LogicException;
import com.dkd.app.domain.Task;
import com.dkd.app.domain.dto.CancelTaskDto;
import com.dkd.app.mapper.TaskMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 接单/取消工单的并发安全单元测试（P0-6）。
 *
 * <p>## 这批用例防什么
 * 改造前 {@code accept}/{@code cancel} 是 {@code getById → 判断 → updateById} 的 check-then-act：
 * 两个请求可以同时读到「待处理」并各自更新成功，后写覆盖先写（两人都以为自己接了单）。
 * 改造后状态与归属条件进了同一条 {@code UPDATE ... WHERE}（{@code TaskMapper.acceptIfPending} /
 * {@code cancelIfActive}），由 InnoDB 行锁保证并发下只有一条能命中。因此本测试的核心断言不是
 * 「返回值对不对」，而是：
 * <ul>
 *   <li><b>成功路径不回查</b>：更新命中时不得再 {@code selectById}——一旦回查就退化成先读后写；</li>
 *   <li><b>不走 updateById</b>：{@code updateById} 是无条件按主键更新，等于把并发窗口放回来；</li>
 *   <li><b>条件与目标状态由 Service 传给 SQL</b>：期望状态/目标状态/排除状态必须来自常量，
 *       不能在 SQL 里写死（注解 SQL 无法引用常量，写死就与契约表脱钩）；</li>
 *   <li><b>0 行的四种归因</b>：不存在 / 状态不对 / 不是我的单 / 更新与回查之间又被改走，
 *       前三类文案与改造前逐字一致（前端与运维按这些文案排障）；</li>
 *   <li><b>空登录人</b>：改造前会 NPE，现在给明确文案且不碰数据库。</li>
 * </ul>
 *
 * <p>## 为什么用 Mapper 方法而不是 LambdaUpdateWrapper
 * {@code LambdaUpdateWrapper} 依赖实体的 lambda 元数据缓存，纯 Mockito 单测（不启 Spring）会抛
 * {@code can not find lambda cache for this entity}——SQL 就没法被单测覆盖。改成 Mapper 注解 SQL 后，
 * 这里 mock 的是「条件更新是否被调用、参数是否对」，真 SQL 的并发语义由 Mapper 注释与 D 阶段真库验收保证。
 *
 * @author ruoyi
 * @date 2026-11-02
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class TaskServiceAcceptCancelAtomicTest
{
    private static final Integer USER_ID = 7;
    private static final Long TASK_ID = 9527L;

    @Mock
    private TaskMapper taskMapper;

    private TaskServiceImpl taskService;

    @BeforeEach
    void setUp()
    {
        taskService = new TaskServiceImpl();
        ReflectionTestUtils.setField(taskService, "baseMapper", taskMapper);
    }

    private Task task(Integer status, Integer userId)
    {
        Task task = new Task();
        task.setTaskId(TASK_ID);
        task.setTaskStatus(status);
        task.setUserId(userId);
        return task;
    }

    private void stubAcceptRows(int rows)
    {
        when(taskMapper.acceptIfPending(eq(TASK_ID), eq(USER_ID),
                eq(VmSystemConstant.TASK_STATUS_CREATE),
                eq(VmSystemConstant.TASK_STATUS_PROGRESS),
                any(LocalDateTime.class))).thenReturn(rows);
    }

    private void stubCancelRows(int rows)
    {
        when(taskMapper.cancelIfActive(eq(TASK_ID), eq(USER_ID),
                eq(VmSystemConstant.TASK_STATUS_FINISH),
                eq(VmSystemConstant.TASK_STATUS_CANCEL),
                eq(VmSystemConstant.TASK_STATUS_CANCEL),
                any(),
                any(LocalDateTime.class))).thenReturn(rows);
    }

    @Test
    void 接单命中时直接成功且不回查()
    {
        stubAcceptRows(1);

        assertThat(taskService.accept(TASK_ID, USER_ID)).isTrue();

        verify(taskMapper).acceptIfPending(eq(TASK_ID), eq(USER_ID),
                eq(VmSystemConstant.TASK_STATUS_CREATE),
                eq(VmSystemConstant.TASK_STATUS_PROGRESS),
                any(LocalDateTime.class));
        verify(taskMapper, never()).updateById(any());
        verify(taskMapper, never()).selectById(any());
    }

    @Test
    void 接单零行且工单不存在时报工单不存在()
    {
        stubAcceptRows(0);
        when(taskMapper.selectById(any())).thenReturn(null);

        assertThatThrownBy(() -> taskService.accept(TASK_ID, USER_ID))
                .isInstanceOf(LogicException.class)
                .hasMessage("工单不存在");
    }

    @Test
    void 接单零行且状态已被改走时报状态不是待处理()
    {
        stubAcceptRows(0);
        when(taskMapper.selectById(any()))
                .thenReturn(task(VmSystemConstant.TASK_STATUS_PROGRESS, USER_ID));

        assertThatThrownBy(() -> taskService.accept(TASK_ID, USER_ID))
                .isInstanceOf(LogicException.class)
                .hasMessage("此工单状态不是待处理，无法接受");
    }

    @Test
    void 接单零行且不是我的单时报无权接受()
    {
        stubAcceptRows(0);
        when(taskMapper.selectById(any()))
                .thenReturn(task(VmSystemConstant.TASK_STATUS_CREATE, USER_ID + 1));

        assertThatThrownBy(() -> taskService.accept(TASK_ID, USER_ID))
                .isInstanceOf(LogicException.class)
                .hasMessage("此登录人无权接受工单");
    }

    @Test
    void 接单零行且状态归属都正常时报状态已变化()
    {
        // 模拟 UPDATE 与回查之间又被别人改走：回查看到的仍是待处理且是我的单
        stubAcceptRows(0);
        when(taskMapper.selectById(any()))
                .thenReturn(task(VmSystemConstant.TASK_STATUS_CREATE, USER_ID));

        assertThatThrownBy(() -> taskService.accept(TASK_ID, USER_ID))
                .isInstanceOf(LogicException.class)
                .hasMessage("工单状态已变化，请刷新后重试");
    }

    @Test
    void 接单登录人为空时拒绝且不碰数据库()
    {
        assertThatThrownBy(() -> taskService.accept(TASK_ID, null))
                .isInstanceOf(LogicException.class)
                .hasMessage("未获取到登录人信息，无法接受工单");
        verifyNoInteractions(taskMapper);
    }

    @Test
    void 取消命中时直接成功且带回驳回原因()
    {
        stubCancelRows(1);
        CancelTaskDto dto = new CancelTaskDto();
        dto.setDesc("设备故障");

        assertThat(taskService.cancel(TASK_ID, dto, USER_ID)).isTrue();

        verify(taskMapper).cancelIfActive(eq(TASK_ID), eq(USER_ID),
                eq(VmSystemConstant.TASK_STATUS_FINISH),
                eq(VmSystemConstant.TASK_STATUS_CANCEL),
                eq(VmSystemConstant.TASK_STATUS_CANCEL),
                eq("设备故障"),
                any(LocalDateTime.class));
        verify(taskMapper, never()).updateById(any());
        verify(taskMapper, never()).selectById(any());
    }

    @Test
    void 取消零行且已完成时报状态不可取消()
    {
        stubCancelRows(0);
        when(taskMapper.selectById(any()))
                .thenReturn(task(VmSystemConstant.TASK_STATUS_FINISH, USER_ID));

        assertThatThrownBy(() -> taskService.cancel(TASK_ID, new CancelTaskDto(), USER_ID))
                .isInstanceOf(LogicException.class)
                .hasMessage("此工单状态不是待处理或进行中，无法取消");
    }

    @Test
    void 取消零行且不是我的单时报无权取消()
    {
        stubCancelRows(0);
        when(taskMapper.selectById(any()))
                .thenReturn(task(VmSystemConstant.TASK_STATUS_CREATE, USER_ID + 1));

        assertThatThrownBy(() -> taskService.cancel(TASK_ID, new CancelTaskDto(), USER_ID))
                .isInstanceOf(LogicException.class)
                .hasMessage("此登录人无权取消/拒绝工单");
    }

    @Test
    void 取消登录人为空时拒绝且不碰数据库()
    {
        assertThatThrownBy(() -> taskService.cancel(TASK_ID, new CancelTaskDto(), null))
                .isInstanceOf(LogicException.class)
                .hasMessage("未获取到登录人信息，无法取消工单");
        verifyNoInteractions(taskMapper);
    }
}
