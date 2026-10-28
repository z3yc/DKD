package com.dkd.manage.service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import com.dkd.common.constant.DkdContants;
import com.dkd.common.exception.ServiceException;
import com.dkd.manage.domain.Channel;
import com.dkd.manage.domain.Emp;
import com.dkd.manage.domain.Task;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.domain.dto.TaskDetailsDto;
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.mapper.ChannelMapper;
import com.dkd.manage.mapper.TaskMapper;
import com.dkd.manage.service.impl.TaskServiceImpl;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * FIX-1 的 Java 配套改动：**唯一键冲突（1062）必须变成业务文案**（排期 §八 FIX-1 / §十 任务 3）。
 *
 * <p>## 这个用例防的是什么
 *
 * <p>`docs/ddl/add_task_active_dedup_unique.sql` 给 `tb_task` 加了「同设备同类型至多一张活跃工单」
 * 的唯一约束。约束生效后，**并发建单落败的一方收到的就不再是**
 * `ServiceException("设备有未完成工单，请勿重复创建工单")`，而是 MyBatis/Spring 抛出的
 * `DuplicateKeyException`（1062）——用户看到“系统异常”，且会怀疑是服务坏了。
 *
 * <p>这类缺陷的典型特征是：**单线程手工测试永远撞不到**（先查后插在无并发时总是走“查得到→拒绝”
 * 的业务分支），只有并发压测或唯一键撞车时才暴露；而它一旦暴露，前端展示与技术排障两头都是错的。
 *
 * <p>## 为什么用 Mockito 而不是真库
 *
 * <p>AGENTS §8：Java 单测不得依赖 MySQL/Redis。这里的被测语义很窄——**异常翻译与 cause 保留**，
 * 用打桩把 `insertTask` 变成“抛 1062”即可精确复现，且能顺带断言“明细不再插入”（事务不应继续）。
 * 真库的约束行为已在 `add_task_active_dedup_unique.sql` 的临时探针表上单独验过（5 条全符）。
 *
 * @author ruoyi
 * @date 2026-10-01
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class TaskServiceCreateConflictTest
{
    /** 与实现约定一致：冲突文案（改这里就等于改回调契约，Python 侧会原样展示给运营） */
    private static final String CONFLICT_MESSAGE = "设备有未完成工单或工单号冲突，请刷新重试";

    @Mock
    private TaskMapper taskMapper;

    @Mock
    private IVendingMachineService vendinngService;

    @Mock
    private IEmpService empService;

    @Mock
    private ITaskDetailsService taskDetailsService;

    @Mock
    private ChannelMapper channelMapper;

    @Mock
    private RedisTemplate<Object, Object> redisTemplate;

    @Mock
    private ValueOperations<Object, Object> valueOperations;

    @InjectMocks
    private TaskServiceImpl taskService;

    private void stubHappyPathBeforeInsert()
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(80L);
        vm.setInnerCode("A1000001");
        vm.setVmStatus(DkdContants.VM_STATUS_RUNNING);
        vm.setRegionId(3L);
        vm.setAddr("北京市海淀区五道口");
        Emp emp = new Emp();
        emp.setId(2L);
        emp.setUserName("张三");
        emp.setRegionId(3L);
        // 建单校验链已补「员工启用 + 角色匹配」：补货工单必须由运营(1002)或管理(1001)执行
        emp.setStatus(DkdContants.EMP_STATUS_NORMAL);
        emp.setRoleCode(DkdContants.ROLE_CODE_BUSINESS);
        // 补货明细校验按 innerCode 一次取货道：明细里的 "1-1" 必须真实存在且容量足够
        Channel channel = new Channel();
        channel.setId(11L);
        channel.setChannelCode("1-1");
        channel.setInnerCode("A1000001");
        channel.setSkuId(1L);
        channel.setMaxCapacity(16L);

        when(vendinngService.selectVendingMachineByInnerCode("A1000001")).thenReturn(vm);
        when(taskMapper.selectInflightTaskList(anyString(), anyLong(), anyList()))
                .thenReturn(new ArrayList<Task>());
        when(empService.selectEmpById(2L)).thenReturn(emp);
        when(channelMapper.selectChannelList(any(Channel.class)))
                .thenReturn(new ArrayList<Channel>(Collections.singletonList(channel)));
        // 走“key 已存在”的分支，避免 hasKey/set 的额外打桩
        when(redisTemplate.hasKey(anyString())).thenReturn(true);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.increment(anyString())).thenReturn(1L);
    }

    private TaskDto supplyTaskDto()
    {
        TaskDto dto = new TaskDto();
        dto.setInnerCode("A1000001");
        dto.setProductTypeId(DkdContants.TASK_TYPE_SUPPLY);
        dto.setUserId(2L);
        TaskDetailsDto detail = new TaskDetailsDto();
        detail.setChannelCode("1-1");
        detail.setExpectCapacity(8L);
        detail.setSkuId(1L);
        dto.setDetails(new ArrayList<TaskDetailsDto>(Collections.singletonList(detail)));
        return dto;
    }

    @Test
    void 主表唯一键冲突时转成业务文案且保留原始异常()
    {
        stubHappyPathBeforeInsert();
        DuplicateKeyException dbError =
                new DuplicateKeyException("Duplicate entry 'A1000001:2' for key 'uk_task_active_dedup'");
        when(taskMapper.insertTask(any(Task.class))).thenThrow(dbError);

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(CONFLICT_MESSAGE)
                // 关键：文案换了，真因不能丢（否则排障时查不到是哪条约束）
                .hasCauseInstanceOf(DuplicateKeyException.class);

        // 事务应中止：主表都没插进去，明细更不该继续插
        verify(taskDetailsService, never()).insertTaskDetailsBatch(anyList());
    }

    /**
     * `task_code` 唯一键也会抛 1062（Redis 当天流水被清空 / 多实例编号撞车），
     * 文案必须同样可读——这正是本次改动与“活跃工单唯一约束”**解耦**的原因：
     * 即使业务最终否决了那条 DDL，这条翻译逻辑依然有价值。
     */
    @Test
    void 工单号唯一键冲突时同样转成业务文案()
    {
        stubHappyPathBeforeInsert();
        when(taskMapper.insertTask(any(Task.class)))
                .thenThrow(new DuplicateKeyException(
                        "Duplicate entry '202610010001' for key 'task_code'"));

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(CONFLICT_MESSAGE)
                .hasCauseInstanceOf(DuplicateKeyException.class);
    }

    @Test
    void 明细批量插入冲突时也转成业务文案()
    {
        stubHappyPathBeforeInsert();
        when(taskMapper.insertTask(any(Task.class))).thenAnswer(invocation -> {
            Task saved = invocation.getArgument(0);
            saved.setTaskId(567L);
            return 1;
        });
        DuplicateKeyException dbError = new DuplicateKeyException("Duplicate entry '567:1-1'");
        doThrow(dbError).when(taskDetailsService).insertTaskDetailsBatch(anyList());

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(CONFLICT_MESSAGE)
                .hasCauseInstanceOf(DuplicateKeyException.class);
    }

    /**
     * 原有业务拒绝路径必须**逐字不变**：Python 侧把这些文案原样展示给运营，
     * 改了就等于改了回调契约（AGENTS §4.3 文档同步义务）。
     */
    @Test
    void 业务拒绝文案不受本次改动影响()
    {
        stubHappyPathBeforeInsert();
        Task pending = new Task();
        pending.setTaskStatus(DkdContants.TASK_STATUS_CREATE);
        when(taskMapper.selectInflightTaskList(anyString(), anyLong(), anyList()))
                .thenReturn(new ArrayList<Task>(Arrays.asList(pending)));

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("设备有未完成工单，请勿重复创建工单")
                .hasNoCause();
    }

    /** 非冲突类异常不得被误吞成“工单冲突”（否则真实故障会被伪装成业务提示）。 */
    @Test
    void 其它运行时异常原样上抛()
    {
        stubHappyPathBeforeInsert();
        when(taskMapper.insertTask(any(Task.class))).thenThrow(new IllegalStateException("连接池耗尽"));

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("连接池耗尽");
    }

    /** 新增接口保持签名兼容：`insertTaskDto` 也必须走同一套翻译（内部共用同一方法）。 */
    @Test
    void 原签名入口同样转换冲突文案()
    {
        stubHappyPathBeforeInsert();
        when(taskMapper.insertTask(any(Task.class)))
                .thenThrow(new DuplicateKeyException("Duplicate entry 'A1000001:2'"));

        assertThatThrownBy(() -> taskService.insertTaskDto(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(CONFLICT_MESSAGE);
    }
}
