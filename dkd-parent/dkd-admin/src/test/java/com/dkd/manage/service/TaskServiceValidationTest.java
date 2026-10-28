package com.dkd.manage.service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
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

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 工单建单校验链的**拒绝路径**回归测试（业务规范 §4.1、AGENTS §8）。
 *
 * <p>## 这批用例防什么
 * 2026-10-28 收口前，建单只校验「设备、状态、防重、员工存在、区域」五件事：
 * <ul>
 *   <li>工单类型不在 1-4 时**没有任何分支命中**，非法类型会被静默放行落库；</li>
 *   <li>停用员工、角色不匹配（如把补货单派给维修员）也能建单，建完没人能在运维端执行；</li>
 *   <li>补货明细可以没有、货道可以不属于该设备、补货数量可以超过货道容量；</li>
 *   <li>{@code PUT /manage/task} 这个通用编辑入口可以直接改 task_status，绕过状态机。</li>
 * </ul>
 * 这些都属于「该拒绝的没拒绝」（AGENTS §8 明确比“该通过的没通过”严重得多），
 * 且失败时才暴露为脏数据，所以在这里逐条钉住**异常文案**——Python 回调会把 message 原样展示给运营。
 *
 * <p>## 为什么用 Mockito 不连真库
 * AGENTS §8：Java 单测不依赖 MySQL/Redis。被测语义是“分支与文案”，与 SQL 无关；
 * 落库真实行为由 {@code TaskServiceCreateConflictTest}（唯一键）与 Phase C 真库验收覆盖。
 *
 * @author ruoyi
 * @date 2026-10-28
 */
@ExtendWith(MockitoExtension.class)
// 不同用例覆盖不同分支，未打桩的协作对象在部分分支不会被调用
@MockitoSettings(strictness = Strictness.LENIENT)
class TaskServiceValidationTest
{
    private static final String INNER_CODE = "A1000001";
    private static final Long EMP_ID = 2L;
    private static final Long REGION_ID = 3L;

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

    /**
     * 打桩：设备存在且状态给定。
     *
     * @param vmStatus 设备状态
     */
    private void stubMachine(Long vmStatus)
    {
        VendingMachine vm = new VendingMachine();
        vm.setId(80L);
        vm.setInnerCode(INNER_CODE);
        vm.setVmStatus(vmStatus);
        vm.setRegionId(REGION_ID);
        vm.setAddr("北京市海淀区五道口");
        when(vendinngService.selectVendingMachineByInnerCode(INNER_CODE)).thenReturn(vm);
    }

    /**
     * 打桩：员工存在/状态/角色/区域给定。
     *
     * @param status 员工状态（1-启用 0-停用）
     * @param roleCode 角色编码（1001/1002/1003）
     */
    private void stubEmp(Long status, String roleCode)
    {
        Emp emp = new Emp();
        emp.setId(EMP_ID);
        emp.setUserName("张三");
        emp.setRegionId(REGION_ID);
        emp.setStatus(status);
        emp.setRoleCode(roleCode);
        when(empService.selectEmpById(EMP_ID)).thenReturn(emp);
    }

    /**
     * 打桩：无在途工单 + 工单号流水可用。
     */
    private void stubNoInflightAndTaskCode()
    {
        when(taskMapper.selectInflightTaskList(anyString(), anyLong(), anyList()))
                .thenReturn(new ArrayList<Task>());
        when(redisTemplate.hasKey(anyString())).thenReturn(true);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.increment(anyString())).thenReturn(1L);
    }

    /**
     * 打桩：设备货道档案（默认 1-1 / skuId=1 / 容量 16）。
     *
     * @param channels 货道
     */
    private void stubChannels(Channel... channels)
    {
        when(channelMapper.selectChannelList(any(Channel.class)))
                .thenReturn(new ArrayList<Channel>(Arrays.asList(channels)));
    }

    private Channel channel(String channelCode, Long skuId, Long maxCapacity)
    {
        Channel channel = new Channel();
        channel.setId(11L);
        channel.setInnerCode(INNER_CODE);
        channel.setChannelCode(channelCode);
        channel.setSkuId(skuId);
        channel.setMaxCapacity(maxCapacity);
        return channel;
    }

    private TaskDetailsDto detail(String channelCode, Long expectCapacity, Long skuId)
    {
        TaskDetailsDto detail = new TaskDetailsDto();
        detail.setChannelCode(channelCode);
        detail.setExpectCapacity(expectCapacity);
        detail.setSkuId(skuId);
        detail.setSkuName("可口可乐");
        return detail;
    }

    private TaskDto supplyTaskDto(List<TaskDetailsDto> details)
    {
        TaskDto dto = new TaskDto();
        dto.setInnerCode(INNER_CODE);
        dto.setProductTypeId(DkdContants.TASK_TYPE_SUPPLY);
        dto.setUserId(EMP_ID);
        dto.setDetails(details);
        return dto;
    }

    private TaskDto oneDetailSupplyTaskDto()
    {
        return supplyTaskDto(new ArrayList<TaskDetailsDto>(Collections.singletonList(
                detail("1-1", 8L, 1L))));
    }

    private TaskDto repairTaskDto()
    {
        TaskDto dto = new TaskDto();
        dto.setInnerCode(INNER_CODE);
        dto.setProductTypeId(DkdContants.TASK_TYPE_REPAIR);
        dto.setUserId(EMP_ID);
        dto.setDesc("3 号货道连续 4 次已扣款未出货");
        return dto;
    }

    @Test
    void 工单类型不在白名单时拒绝且不落库()
    {
        // 5 不是 DkdContants 里的任何类型：改前 4 个 if 一个都不命中 → 静默放行
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        TaskDto dto = new TaskDto();
        dto.setInnerCode(INNER_CODE);
        dto.setProductTypeId(5L);
        dto.setUserId(EMP_ID);

        assertThatThrownBy(() -> taskService.insertTaskDto(dto))
                .isInstanceOf(ServiceException.class)
                .hasMessage("工单类型不合法");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 停用员工不能建单()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_DISABLE, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));

        assertThatThrownBy(() -> taskService.insertTaskDto(oneDetailSupplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("员工已停用");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 补货工单派给维修角色时拒绝()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_OPERATOR);
        stubNoInflightAndTaskCode();

        assertThatThrownBy(() -> taskService.insertTaskDto(oneDetailSupplyTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("该员工角色不能执行此类工单");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 维修工单派给运营角色时拒绝()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();

        assertThatThrownBy(() -> taskService.insertTaskDto(repairTaskDto()))
                .isInstanceOf(ServiceException.class)
                .hasMessage("该员工角色不能执行此类工单");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 补货工单没有明细时拒绝()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();

        assertThatThrownBy(() -> taskService.insertTaskDto(supplyTaskDto(null)))
                .isInstanceOf(ServiceException.class)
                .hasMessage("补货工单必须包含明细");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 明细货道不属于该设备时拒绝()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));

        TaskDto dto = supplyTaskDto(new ArrayList<TaskDetailsDto>(Collections.singletonList(
                detail("9-9", 8L, 1L))));

        assertThatThrownBy(() -> taskService.insertTaskDto(dto))
                .isInstanceOf(ServiceException.class)
                .hasMessage("货道不属于该设备");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 补货数量超过货道容量时拒绝()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));

        TaskDto dto = supplyTaskDto(new ArrayList<TaskDetailsDto>(Collections.singletonList(
                detail("1-1", 17L, 1L))));

        assertThatThrownBy(() -> taskService.insertTaskDto(dto))
                .isInstanceOf(ServiceException.class)
                .hasMessage("补货数量超出货道容量");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 补货数量必须大于零()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));

        TaskDto dto = supplyTaskDto(new ArrayList<TaskDetailsDto>(Collections.singletonList(
                detail("1-1", 0L, 1L))));

        assertThatThrownBy(() -> taskService.insertTaskDto(dto))
                .isInstanceOf(ServiceException.class)
                .hasMessage("补货数量必须大于 0");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 明细商品与货道档案不一致时拒绝()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));

        // 货道 1-1 档案是 skuId=1，明细却要补 skuId=99：补进去的货不对路
        TaskDto dto = supplyTaskDto(new ArrayList<TaskDetailsDto>(Collections.singletonList(
                detail("1-1", 8L, 99L))));

        assertThatThrownBy(() -> taskService.insertTaskDto(dto))
                .isInstanceOf(ServiceException.class)
                .hasMessage("货道商品与明细商品不一致");
        verify(taskMapper, never()).insertTask(any(Task.class));
    }

    @Test
    void 后台建单缺省创建类型为手动()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));
        when(taskMapper.insertTask(any(Task.class))).thenReturn(1);

        // createType 未赋值（后台 Controller 会显式置 1；Service 兜底也必须落成手动）
        assertThat(taskService.insertTaskDto(oneDetailSupplyTaskDto())).isEqualTo(1);

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskMapper).insertTask(captor.capture());
        assertThat(captor.getValue().getCreateType())
                .as("创建类型缺省必须是 1-手动，不能是 null/0（审计口径）")
                .isEqualTo(1L);
        verify(taskDetailsService).insertTaskDetailsBatch(anyList());
    }

    @Test
    void 智能体回调显式创建类型为自动时保留()
    {
        stubMachine(DkdContants.VM_STATUS_RUNNING);
        stubEmp(DkdContants.EMP_STATUS_NORMAL, DkdContants.ROLE_CODE_BUSINESS);
        stubNoInflightAndTaskCode();
        stubChannels(channel("1-1", 1L, 16L));
        when(taskMapper.insertTask(any(Task.class))).thenReturn(1);

        TaskDto dto = oneDetailSupplyTaskDto();
        dto.setCreateType(0L);
        taskService.insertTaskDto(dto);

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskMapper).insertTask(captor.capture());
        assertThat(captor.getValue().getCreateType()).isEqualTo(0L);
    }

    @Test
    void 通用编辑接口不允许直接改工单状态()
    {
        Task dbTask = new Task();
        dbTask.setTaskId(99L);
        dbTask.setTaskStatus(DkdContants.TASK_STATUS_CREATE);
        when(taskMapper.selectTaskByTaskId(99L)).thenReturn(dbTask);

        Task input = new Task();
        input.setTaskId(99L);
        input.setTaskStatus(DkdContants.TASK_STATUS_FINISH);

        assertThatThrownBy(() -> taskService.updateTask(input))
                .isInstanceOf(ServiceException.class)
                .hasMessage("请通过接单/完成/取消操作变更工单状态");
        verify(taskMapper, never()).updateTask(any(Task.class));
    }

    @Test
    void 通用编辑接口即使状态同值也不写状态列()
    {
        Task dbTask = new Task();
        dbTask.setTaskId(99L);
        dbTask.setTaskStatus(DkdContants.TASK_STATUS_CREATE);
        when(taskMapper.selectTaskByTaskId(99L)).thenReturn(dbTask);
        when(taskMapper.updateTask(any(Task.class))).thenReturn(1);

        Task input = new Task();
        input.setTaskId(99L);
        input.setTaskStatus(DkdContants.TASK_STATUS_CREATE);
        input.setDesc("改成这个描述");

        assertThat(taskService.updateTask(input)).isEqualTo(1);

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskMapper).updateTask(captor.capture());
        assertThat(captor.getValue().getTaskStatus())
                .as("通用编辑不得携带 task_status（状态列只有一个写入口）")
                .isNull();
    }
}
