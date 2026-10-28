package com.dkd.app.service.impl;

import cn.hutool.core.util.ObjectUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.dkd.app.common.constant.TaskTypeConstant;
import com.dkd.app.common.constant.VmStatusConstant;
import com.dkd.app.common.constant.VmSystemConstant;
import com.dkd.app.common.exception.LogicException;
import com.dkd.app.domain.Task;
import com.dkd.app.domain.TaskDetails;
import com.dkd.app.domain.TaskStatus;
import com.dkd.app.domain.TaskType;
import com.dkd.app.domain.dto.CancelTaskDto;
import com.dkd.app.domain.vo.Pager;
import com.dkd.app.mapper.TaskMapper;
import com.dkd.app.service.*;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Service
@Slf4j
public class TaskServiceImpl extends ServiceImpl<TaskMapper, Task> implements TaskService {

    @Autowired
    private TaskStatusService taskStatusService;
    @Autowired
    private TaskTypeService taskTypeService;

    @Autowired
    private VendingMachineService vendingMachineService;

    @Autowired
    private TaskDetailsService taskDetailsService;


    /**
     * 搜索工单
     *
     * @param pageIndex
     * @param pageSize
     * @param innerCode 设备编号
     * @param userId    工单所属人Id
     * @param taskCode  工单编号
     * @param status    工单状态
     * @param isRepair  是否是维修工单
     * @return
     */
    @Override
    public Pager<Task> search(Long pageIndex, Long pageSize, String innerCode, Integer userId, String taskCode, Integer status, Boolean isRepair, LocalDate start, LocalDate end) {
        // 初始化分页参数
        Page<Task> page = new Page<>(pageIndex, pageSize);
        // 创建任务查询条件
        LambdaQueryWrapper<Task> qw = new LambdaQueryWrapper<>();
        // 根据设备编号查询
        if (StrUtil.isNotBlank(innerCode)) {
            qw.eq(Task::getInnerCode, innerCode);
        }
        // 根据用户ID查询
        if (userId != null && userId > 0) {
            qw.eq(Task::getUserId, userId);
        }
        // 根据工单编码查询
        if (StrUtil.isNotBlank(taskCode)) {
            qw.like(Task::getTaskCode, taskCode);
        }
        // 根据工单状态查询
        if (status != null && status > 0) {
            qw.eq(Task::getTaskStatus, status);
        }
        // 根据是否是补货工单查询
        if (isRepair != null) {
            if (isRepair) {
                qw.ne(Task::getProductTypeId, TaskTypeConstant.TASK_TYPE_SUPPLY);
            } else {
                qw.eq(Task::getProductTypeId, TaskTypeConstant.TASK_TYPE_SUPPLY);
            }
        }
        // 根据创建时间查询
        if (ObjectUtil.isNotEmpty(start) && ObjectUtil.isNotEmpty(end)) {
            // 转换为日期时间对象以用于查询
            // 转为日期时间对象
            LocalDateTime minTime = start.atTime(LocalTime.MIN);// 2020-01-01 00:00:01
            LocalDateTime maxTime = end.atTime(LocalTime.MAX); // 2020-12-31 23:59:59
            qw.between(Task::getCreateTime, minTime, maxTime);
        }
        // 按更新时间倒序排序
        // 根据最后更新时间倒序排序
        qw.orderByDesc(Task::getUpdateTime);
        // 执行分页查询
        Page<Task> taskPage = this.page(page, qw);
        // 读取工单类型和工单状态
        readAll(taskPage.getRecords());
        // 构建并返回分页响应
        return Pager.build(taskPage);
    }

    // 读取工单类型和工单状态
    private void readAll(List<Task> taskList) {
        List<TaskType> typeList = taskTypeService.list();
        List<TaskStatus> statusList = new ArrayList<>();
        statusList.add(new TaskStatus(1,"待办"));
        statusList.add(new TaskStatus(2,"进行"));
        statusList.add(new TaskStatus(3,"取消"));
        statusList.add(new TaskStatus(4,"完成"));
        taskList.forEach(task -> {
            //工单类型
            Optional<TaskType> type = typeList.stream().filter(taskTypeEntity -> taskTypeEntity.getTypeId().equals(task.getProductTypeId())).findFirst();
            if (type.isPresent()) {
                task.setTaskType(type.get());
            }
            //工单状态
            Optional<TaskStatus> status = statusList.stream().filter(taskStatusTypeEntity -> taskStatusTypeEntity.getStatusId().equals(task.getTaskStatus())).findFirst();
            if (status.isPresent()) {
                task.setTaskStatusTypeEntity(status.get());
            }
        });

    }

    // 接受工单
    @Override
    public Boolean accept(Long taskId, Integer userId) {
        // 1.判断工单是否存在
        Task task = this.getById(taskId);
        if (ObjectUtil.isEmpty(task)) {
            throw new LogicException("工单不存在");
        }
        // 2.判断工单状态是否为刚创建（待处理）；Integer 比较用 equals，避免包装类型 == 的缓存陷阱
        if (!VmSystemConstant.TASK_STATUS_CREATE.equals(task.getTaskStatus())) {
            throw new LogicException("此工单状态不是待处理，无法接受");
        }
        // 3.判断登录人是否有权接受工单
        if (!userId.equals(task.getUserId())) {
            throw new LogicException("此登录人无权接受工单");
        }
        // 4.修改工单状态
        task.setTaskStatus(VmSystemConstant.TASK_STATUS_PROGRESS);
        // 5.返回结果
        return this.updateById(task);
    }

    // 拒绝/取消工单
    @Override
    public Boolean cancel(Long taskId, CancelTaskDto cancelTaskDto, Integer userId) {
        // 1.判断工单是否存在
        Task tassk = this.getById(taskId);
        if (ObjectUtil.isEmpty(tassk)) {
            throw new LogicException("工单不存在");
        }
        // 2.判断工单状态是否为刚创建（待处理）；Integer 比较用 equals，避免包装类型 == 的缓存陷阱
        if (VmSystemConstant.TASK_STATUS_FINISH.equals(tassk.getTaskStatus()) ||
                VmSystemConstant.TASK_STATUS_CANCEL.equals(tassk.getTaskStatus())) {
            throw new LogicException("此工单状态不是待处理或进行中，无法取消");
        }
        // 3.判断登录人是否有权取消工单
        if (!userId.equals(tassk.getUserId())) {
            throw new LogicException("此登录人无权取消/拒绝工单");
        }
        // 4.修改工单状态
        tassk.setTaskStatus(VmSystemConstant.TASK_STATUS_CANCEL);
        tassk.setDesc(cancelTaskDto.getDesc());
        boolean b = this.updateById(tassk);

        // 5.返回结果
        return b;
    }

    /**
     * 完成工单（a~d 四件事在同一事务里，任一步失败整体回滚，工单不完成）。
     *
     * <p>清晰版 §2.2 / §4.2：
     * <ul>
     *   <li>a. 工单状态 → 4；</li>
     *   <li>b/c/d. 按类型产生副作用：投放→设备运行中；撤机→设备已撤机；补货→货道缓存 + 库存台账 + 流水；</li>
     *   <li>Integer 状态/类型比较必须用 equals：这些常量是 java.lang.Integer 包装类型，
     *       用 == 只在 -128~127 的缓存区间内"碰巧"成立，是典型的静默失效写法。</li>
     * </ul>
     *
     * @param taskId 工单Id
     * @param userId 登录人Id（必须等于工单执行人）
     * @return 是否成功
     */
    @Transactional(rollbackFor = Exception.class)
    @Override
    public Boolean complete(Long taskId, Integer userId) {
        // 1.判断工单是否存在
        Task task = this.getById(taskId);
        if (ObjectUtil.isEmpty(task)) {
            throw new LogicException("工单不存在");
        }
        // 2.判断工单状态（必须是进行中；Integer 比较用 equals，避免包装类型 == 的缓存陷阱）
        if (VmSystemConstant.TASK_STATUS_FINISH.equals(task.getTaskStatus()) ||
                VmSystemConstant.TASK_STATUS_CANCEL.equals(task.getTaskStatus()) ||
                VmSystemConstant.TASK_STATUS_CREATE.equals(task.getTaskStatus())) {
            throw new LogicException("此工单状态不是进行中，无法完成");
        }
        // 3.判断登录人是否有权完成工单
        if (userId == null) {
            throw new LogicException("未获取到登录人信息，无法完成工单");
        }
        if (!userId.equals(task.getUserId())) {
            throw new LogicException("此登录人无权完成工单");
        }
        // 4.修改工单状态
        task.setTaskStatus(VmSystemConstant.TASK_STATUS_FINISH);
        boolean b = this.updateById(task);
        if (!b) {
            throw new LogicException("工单状态更新失败，请刷新后重试");
        }
        // 5. 副作用：设备状态 / 补货库存回写（同一事务）
        //判断工单类型  如果是投放工单，则修改为运行中状态
        if (TaskTypeConstant.TASK_TYPE_DEPLOY.equals(task.getProductTypeId())) {
            b = vendingMachineService.updateStatus(task.getInnerCode(), VmStatusConstant.VM_STATUS_RUNNING);
        }
        //判断工单类型  如果是撤机工单，则修改为撤机状态
        else if (TaskTypeConstant.TASK_TYPE_REVOKE.equals(task.getProductTypeId())) {
            b = vendingMachineService.updateStatus(task.getInnerCode(), VmStatusConstant.VM_STATUS_REVOKE);
        }
        //判断如果是补货工单：货道缓存 + 库存台账 + 库存流水（清晰版 §2.2 的 b/c/d）
        else if (TaskTypeConstant.TASK_TYPE_SUPPLY.equals(task.getProductTypeId())) {
            // 查询工单明细
            List<TaskDetails> details = taskDetailsService.getByTaskId(task.getTaskId());
            // operator 传工单执行人姓名：流水 tb_inventory_log.operator_name 是审计必写字段
            b = vendingMachineService.supply(task.getInnerCode(), details, task.getUserName());
        }
        // 6.副作用任何一步没生效都必须抛异常回滚：不允许"返回 false 但工单已提交"
        if (!b) {
            throw new LogicException("工单完成失败：设备状态或库存回写未生效，请重试");
        }
        return b;
    }
}
