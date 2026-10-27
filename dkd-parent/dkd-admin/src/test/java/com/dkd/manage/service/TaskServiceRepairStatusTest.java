package com.dkd.manage.service;

import java.util.ArrayList;

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
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.mapper.TaskMapper;
import com.dkd.manage.service.impl.TaskServiceImpl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 维修工单的设备状态例外（2026-10-26 业务决策，故障诊断 → 派单场景的前置条件）。
 *
 * <p>## 规则
 * 维修工单允许：1-运行中、2-停机（现场停用但未撤机）。
 * 维修工单拒绝：0-未投放（还没装）、3-已撤机（已撤走）、状态为空（脏数据）。
 * 补货/撤机仍只允许运行中——这条例外**不能**被顺手放大到其它工单类型。
 *
 * <p>## 为什么必须有这个用例
 * 原实现写死"维修必须运行中"，而现场停机的设备恰恰最需要修，等于把一批设备锁死在停机状态；
 * 反过来，如果只把条件改成"非 0/3 放行"，将来新增状态（如报废）会被静默当成可维修。
 * 所以这里同时断言**允许路径**与**拒绝路径**：AGENTS §8 明确"该拒绝的没拒绝比该通过的没通过严重得多"。
 *
 * <p>## 为什么用 Mockito 而不是真库
 * 被测语义是"状态 → 建不建单"的分支，与 SQL 无关；AGENTS §8 要求 Java 单测不依赖 MySQL/Redis。
 * 真实落库行为已由 `TaskServiceCreateConflictTest`（唯一键）与 2-7 抽样脚本在真库覆盖。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class TaskServiceRepairStatusTest
{
    private static final String INNER_CODE = "A1000001";

    private static final String REPAIR_REJECT_MESSAGE = "设备未投放或已撤机，无法创建维修工单";

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

    /**
     * 打桩：设备存在且状态为给定值。
     *
     * @param vmStatus 售货机状态（0/1/2/3 或 null）
     */
    private void stubMachine(Long vmStatus)
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(80L);
        vm.setInnerCode(INNER_CODE);
        vm.setVmStatus(vmStatus);
        vm.setRegionId(3L);
        vm.setAddr("北京市海淀区五道口");
        when(vendinngService.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm);
    }

    /**
     * 打桩：状态校验通过之后的建单依赖（无在途工单、员工区域一致、工单号自增）。
     */
    private void stubAfterStatusCheck()
    {
        when(taskMapper.selectInflightTaskList(anyString(), anyLong(), anyList()))
                .thenReturn(new ArrayList<Task>());
        Emp emp = new Emp();
        emp.setId(2L);
        emp.setUserName("张三");
        emp.setRegionId(3L);
        when(empService.selectEmpById(2L)).thenReturn(emp);
        when(redisTemplate.hasKey(anyString())).thenReturn(true);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.increment(anyString())).thenReturn(1L);
    }

    /**
     * @return 维修工单入参（维修工单不需要明细）
     */
    private TaskDto repairTaskDto()
    {
        TaskDto dto = new TaskDto();
        dto.setInnerCode(INNER_CODE);
        dto.setProductTypeId(DkdContants.TASK_TYPE_REPAIR);
        dto.setUserId(2L);
        dto.setDesc("3 号货道连续 4 次已扣款未出货");
        return dto;
    }

    @Test
    void 运行中设备可以创建维修工单()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubAfterStatusCheck();

        assertThat(taskService.insertTaskDto(repairTaskDto())).isEqualTo(1);
        verify(taskMapper).insertTask(any(Task.class));
    }

    @Test
    void 停机设备可以创建维修工单()
    {
        // 本用例就是这条例外本身：原实现会在这里抛"设备不在运行中"
        stubMachine(DkdContants.VM_STATUS_STOPPED);
        stubAfterStatusCheck();

        assertThat(taskService.insertTaskDto(repairTaskDto())).isEqualTo(1);
        verify(taskMapper).insertTask(any(Task.class));
    }

    @Test
    void 未投放设备拒绝创建维修工单()
    {
        stubMachine(DkdContants.VM_STATUS_NODEPLOY);

        assertThatThrownBy(() -> taskService.insertTaskDto(repairTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(REPAIR_REJECT_MESSAGE);
    }

    @Test
    void 已撤机设备拒绝创建维修工单()
    {
        stubMachine(DkdContants.VM_STATUS_REVOKE);

        assertThatThrownBy(() -> taskService.insertTaskDto(repairTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(REPAIR_REJECT_MESSAGE);
    }

    @Test
    void 设备状态为空时拒绝创建维修工单()
    {
        // 脏数据（vm_status 为 NULL）不能被当成"可维修"，否则等于对未知状态放行
        stubMachine(null);

        assertThatThrownBy(() -> taskService.insertTaskDto(repairTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage(REPAIR_REJECT_MESSAGE);
    }

    @Test
    void 停机设备的补货工单仍然被拒绝()
    {
        // 例外只针对维修：给停机设备补货既没意义，还会掩盖故障
        stubMachine(DkdContants.VM_STATUS_STOPPED);
        TaskDto dto = new TaskDto();
        dto.setInnerCode(INNER_CODE);
        dto.setProductTypeId(DkdContants.TASK_TYPE_SUPPLY);
        dto.setUserId(2L);

        assertThatThrownBy(() -> taskService.insertTaskDto(dto))
                .isInstanceOf(ServiceException.class)
                .hasMessage("设备不在运行中，请勿重复补货");
    }
}
