package com.dkd.agent;

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
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import com.dkd.common.constant.DkdContants;
import com.dkd.common.exception.ServiceException;
import com.dkd.manage.domain.Emp;
import com.dkd.manage.domain.Task;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.domain.dto.TaskDetailsDto;
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.mapper.TaskMapper;
import com.dkd.manage.service.IEmpService;
import com.dkd.manage.service.ITaskDetailsService;
import com.dkd.manage.service.IVendingMachineService;
import com.dkd.manage.service.impl.TaskServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * <p>工单创建服务的新入口单测（排期 1-3）：{@code insertTaskDtoReturningTask} 要能回传
 * taskId/taskCode，供智能体回调用工单号做幂等对账。
 *
 * <p>为什么用 Mockito 纯单测而不是真库集成测试：AGENTS §8 要求 Java 单测不依赖 MySQL/Redis；
 * 这里把 Mapper/Service/Redis 全部打桩，只验证**校验链的顺序与异常文案**以及返回实体的字段填充
 * （这些正是回调契约的一部分，变了 Python 侧就会展示错误的失败原因）。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@ExtendWith(MockitoExtension.class)
// 为什么放宽严格度：不同用例覆盖的校验分支不同，未被打桩的协作对象在某些分支上不会被调用
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentTaskCreateServiceTest
{
    @Mock
    private TaskMapper taskMapper;

    @Mock
    private IVendingMachineService vendinngService;

    @Mock
    private IEmpService empService;

    @Mock
    private ITaskDetailsService taskDetailsService;

    @Mock
    private RedisTemplate<Object, Object> redisTemplate;

    @Mock
    private ValueOperations<Object, Object> valueOperations;

    @InjectMocks
    private TaskServiceImpl taskService;

    private VendingMachine runningVm(Long regionId)
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(80L);
        vm.setInnerCode("A1000001");
        vm.setVmStatus(DkdContants.VM_STATUS_RUNNING);
        vm.setRegionId(regionId);
        vm.setAddr("北京市海淀区五道口");
        return vm;
    }

    private Emp empOfRegion(Long regionId)
    {
        Emp emp = new Emp();
        emp.setId(2L);
        emp.setUserName("张三");
        emp.setRegionId(regionId);
        return emp;
    }

    private TaskDto supplyTaskDto()
    {
        TaskDto dto = new TaskDto();
        dto.setInnerCode("A1000001");
        dto.setProductTypeId(DkdContants.TASK_TYPE_SUPPLY);
        dto.setUserId(2L);
        dto.setDesc("智能体建议补货");
        TaskDetailsDto detail = new TaskDetailsDto();
        detail.setChannelCode("1-1");
        detail.setExpectCapacity(8L);
        detail.setSkuId(1L);
        detail.setSkuName("可口可乐");
        dto.setDetails(new ArrayList<TaskDetailsDto>(Collections.singletonList(detail)));
        return dto;
    }

    @Test
    void 建单成功时回传工单号且字段按档案填充()
    {
        when(vendinngService.selectVendingMachineByInnerCode("A1000001")).thenReturn(runningVm(3L));
        when(taskMapper.selectTaskList(any(Task.class))).thenReturn(new ArrayList<Task>());
        when(empService.selectEmpById(2L)).thenReturn(empOfRegion(3L));
        when(redisTemplate.hasKey(anyString())).thenReturn(false);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        // 模拟 MyBatis useGeneratedKeys 回填主键
        when(taskMapper.insertTask(any(Task.class))).thenAnswer(invocation -> {
            Task saved = invocation.getArgument(0);
            saved.setTaskId(567L);
            return 1;
        });

        Task created = taskService.insertTaskDtoReturningTask(supplyTaskDto());

        assertThat(created).as("必须回传落库实体（否则 Python 侧拿不到 taskId）").isNotNull();
        assertThat(created.getTaskId()).isEqualTo(567L);
        assertThat(created.getTaskCode()).as("工单号应按当天流水生成").isNotNull().endsWith("0001");
        assertThat(created.getTaskStatus()).isEqualTo(DkdContants.TASK_STATUS_CREATE);
        assertThat(created.getRegionId()).as("区域必须取自设备档案").isEqualTo(3L);
        assertThat(created.getAddr()).isEqualTo("北京市海淀区五道口");
        assertThat(created.getUserName()).as("接单人姓名取自员工档案").isEqualTo("张三");
        verify(taskDetailsService).insertTaskDetailsBatch(any(List.class));
    }

    @Test
    void 原签名入口保持返回影响行数()
    {
        when(vendinngService.selectVendingMachineByInnerCode("A1000001")).thenReturn(runningVm(3L));
        when(taskMapper.selectTaskList(any(Task.class))).thenReturn(new ArrayList<Task>());
        when(empService.selectEmpById(2L)).thenReturn(empOfRegion(3L));
        when(redisTemplate.hasKey(anyString())).thenReturn(true);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.increment(anyString())).thenReturn(2L);
        when(taskMapper.insertTask(any(Task.class))).thenReturn(1);

        assertThat(taskService.insertTaskDto(supplyTaskDto())).isEqualTo(1);
    }

    @Test
    void 设备有未完成工单时拒绝且不落库()
    {
        when(vendinngService.selectVendingMachineByInnerCode("A1000001")).thenReturn(runningVm(3L));
        when(taskMapper.selectTaskList(any(Task.class)))
                .thenReturn(new ArrayList<Task>(Arrays.asList(new Task())));

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("设备有未完成工单，请勿重复创建工单");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 员工区域与设备不一致时拒绝且不落库()
    {
        when(vendinngService.selectVendingMachineByInnerCode("A1000001")).thenReturn(runningVm(3L));
        when(taskMapper.selectTaskList(any(Task.class))).thenReturn(new ArrayList<Task>());
        when(empService.selectEmpById(2L)).thenReturn(empOfRegion(9L));

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("员工区域与设备区域不一致，请勿创建工单");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 售货机不存在时拒绝()
    {
        when(vendinngService.selectVendingMachineByInnerCode("A1000001")).thenReturn(null);

        assertThatThrownBy(() -> taskService.insertTaskDtoReturningTask(supplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("售货机不存在");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }
}
