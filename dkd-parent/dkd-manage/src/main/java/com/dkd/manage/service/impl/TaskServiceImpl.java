package com.dkd.manage.service.impl;

import java.time.Duration;
import java.util.List;
import java.util.stream.Collectors;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.util.StrUtil;
import com.dkd.common.constant.DkdContants;
import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.Emp;
import com.dkd.manage.domain.TaskDetails;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.domain.dto.TaskDetailsDto;
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.domain.vo.TaskVo;
import com.dkd.manage.service.IEmpService;
import com.dkd.manage.service.ITaskDetailsService;
import com.dkd.manage.service.IVendingMachineService;
import org.apache.commons.collections4.EnumerationUtils;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;
import com.dkd.manage.mapper.TaskMapper;
import com.dkd.manage.domain.Task;
import com.dkd.manage.service.ITaskService;
import org.springframework.transaction.annotation.Transactional;

/**
 * 工单Service业务层处理
 * 
 * @author ruoyi
 * @date 2025-11-17
 */
@Service
public class TaskServiceImpl implements ITaskService 
{
    @Autowired
    private TaskMapper taskMapper;

    @Autowired
    private IVendingMachineService vendinngService;

    @Autowired
    private IEmpService empService;

    @Autowired
    private RedisTemplate redisTemplate;

    @Autowired
    private ITaskDetailsService taskDetailsService;

    /**
     * 查询工单
     * 
     * @param taskId 工单主键
     * @return 工单
     */
    @Override
    public Task selectTaskByTaskId(Long taskId)
    {
        return taskMapper.selectTaskByTaskId(taskId);
    }

    /**
     * 查询工单列表
     * 
     * @param task 工单
     * @return 工单
     */
    @Override
    public List<Task> selectTaskList(Task task)
    {
        return taskMapper.selectTaskList(task);
    }

    /**
     * 新增工单
     * 
     * @param task 工单
     * @return 结果
     */
    @Override
    public int insertTask(Task task)
    {
        task.setCreateTime(DateUtils.getNowDate());
        return taskMapper.insertTask(task);
    }

    /**
     * 修改工单
     * 
     * @param task 工单
     * @return 结果
     */
    @Override
    public int updateTask(Task task)
    {
        task.setUpdateTime(DateUtils.getNowDate());
        return taskMapper.updateTask(task);
    }

    /**
     * 批量删除工单
     * 
     * @param taskIds 需要删除的工单主键
     * @return 结果
     */
    @Override
    public int deleteTaskByTaskIds(Long[] taskIds)
    {
        return taskMapper.deleteTaskByTaskIds(taskIds);
    }

    /**
     * 删除工单信息
     * 
     * @param taskId 工单主键
     * @return 结果
     */
    @Override
    public int deleteTaskByTaskId(Long taskId)
    {
        return taskMapper.deleteTaskByTaskId(taskId);
    }

    @Override
    public List<TaskVo> selectTaskVoList(Task task) {
        return taskMapper.selectTaskVoList(task);
    }
    /**
     * 批量新增工单（保留原签名：返回影响行数）。
     */
    @Transactional
    @Override
    public int insertTaskDto(TaskDto taskDto) {
        // 为什么成功后返回 1：校验失败会抛 ServiceException，成功路径下 INSERT 必然影响 1 行
        //（与原实现返回 taskMapper.insertTask 的行数等价，调用方语义不变）
        insertTaskDtoInternal(taskDto);
        return 1;
    }

    /**
     * 创建工单并返回落库后的实体（供智能体回调回传 taskId/taskCode，排期 1-3/1-8）。
     *
     * <p>为什么需要它：{@link #insertTaskDto} 只返回影响行数，Python 侧拿不到工单号，
     * 也就无法回写 {@code agent_restock_plan.task_id}、无法做“重复确认直接返回已建单”的幂等对账。
     *
     * @param taskDto 工单入参
     * @return 已落库的工单（含 taskId / taskCode）
     */
    @Transactional
    @Override
    public Task insertTaskDtoReturningTask(TaskDto taskDto) {
        return insertTaskDtoInternal(taskDto);
    }

    /**
     * 工单创建的核心校验链与落库（两个公开入口共用）。
     *
     * <p>**校验顺序与异常文案保持原样**：它们是 Python 侧要原样展示给运营人员的失败原因
     *（设备状态不符 / 设备有未完成工单 / 员工区域不一致），改动会直接影响回调契约。
     *
     * @param taskDto 工单入参
     * @return 已落库的工单
     */
    private Task insertTaskDtoInternal(TaskDto taskDto) {
        //查询售货机是否存在
        VendingMachine vm = vendinngService.selectVendingMachineByInnerCode(taskDto.getInnerCode());
        if(vm == null){
            throw new ServiceException("售货机不存在");
        }
        //校验售货机状态与工单类型是否一致
        checkCreateTask(vm.getVmStatus(),taskDto.getProductTypeId());
        //检查设备是否有未完成的工单
       //创建对象，并设置设备编号、工单类型、创建人、创建时间
        Task taskParam = new Task();
        taskParam.setInnerCode(taskDto.getInnerCode());
        taskParam.setProductTypeId(taskDto.getProductTypeId());
        taskParam.setTaskStatus(DkdContants.TASK_STATUS_PROGRESS);
        // 注意：这里是“先查后插”，**并发下可能双建单**（表上无 (inner_code, product_type_id, status)
        // 唯一索引）。排期 1-8 在 Agent 侧用计划状态机 + 幂等键兜住并发确认；
        // 本方法保持原语义不动（避免影响既有调用方与前端行为）。
        //查询符合条件的工单
        List<Task> taskList = taskMapper.selectTaskList(taskParam);
        //如果有未完成工单，抛出异常
        if(taskList != null && taskList.size() > 0){
            throw new ServiceException("设备有未完成工单，请勿重复创建工单");
        }
        //查询并校验员工是否存在
        Emp emp = empService.selectEmpById(taskDto.getUserId());
        if(emp == null){
            throw new ServiceException("员工不存在");
        }
        //校验员工区域和设备区域是否一致
        if(!emp.getRegionId().equals(vm.getRegionId())){
            throw new ServiceException("员工区域与设备区域不一致，请勿创建工单");
        }
        //将dto转换成po并补充属性，保存工单
       Task task = BeanUtil.copyProperties(taskDto,Task.class);
        task.setTaskStatus(DkdContants.TASK_STATUS_CREATE);
        task.setUserName(emp.getUserName());
        task.setRegionId(vm.getRegionId());
        task.setAddr(vm.getAddr());
        task.setCreateTime(DateUtils.getNowDate());
        task.setTaskCode(generateTaskCode());
        taskMapper.insertTask(task);
        //判断是否为补货工单
        if(taskDto.getProductTypeId().equals(DkdContants.TASK_TYPE_SUPPLY)){
        //获取补货工单详
            List<TaskDetailsDto> details = taskDto.getDetails();
            if (CollUtil.isNotEmpty(details)) {
                // 将dto转换成po
                List<TaskDetails> taskDetailsList =  details.stream().map(dto -> {
                    TaskDetails taskDetails = BeanUtil.copyProperties(dto, TaskDetails.class);
                    taskDetails.setTaskId(task.getTaskId());
                    return taskDetails;
                }).collect(Collectors.toList());
                //批量新增
                taskDetailsService.insertTaskDetailsBatch(taskDetailsList);
            }
        }
        return task;
    }
    /**
     * 取消工单
     */

    @Override
    public int cancelTask(Task task) {
        //判断工单状态
        //根据工单id查询数据库
        Task taskDb = taskMapper.selectTaskByTaskId(task.getTaskId());
        if(taskDb.getTaskStatus().equals(DkdContants.TASK_STATUS_CANCEL)){
            throw new ServiceException("工单已取消,不能再次取消");
        }
        //判断工单状态
        if(taskDb.getTaskStatus().equals(DkdContants.TASK_STATUS_FINISH)){
            throw new ServiceException("工单已完成，不能取消");
        }
        //设置更新字段
        task.setTaskStatus(DkdContants.TASK_STATUS_CANCEL);
        task.setUpdateTime(DateUtils.getNowDate());


        return taskMapper.updateTask(task);
    }

    //生成并获取当天的工单编号（唯一标识）
    private String generateTaskCode(){
        //获取当前日期并格式化
        String date = DateUtils.getDate().replace("-", "");
       //根据日期生成redis的键
        String key = "dkd.task.code:" + date;
        //判断key是否存在
        if(!redisTemplate.hasKey(key)){
            //判断key不存在，则设置key，并设置过期时间
            redisTemplate.opsForValue().set(key, 1, Duration.ofDays(1));
            //生成工单编号
            return date + "0001";
        }
        //如果key存在，则获取key的值，并自增1


        return date + StrUtil.padPre(redisTemplate.opsForValue().increment(key).toString(), 4, "0");
    }
    /**
     * 校验工单信息
     */
    private void checkCreateTask(Long vmStatus,Long productTypeId){

         //如果是投放工单，设备在运行当中，抛出异常
        if (productTypeId == DkdContants.TASK_TYPE_DEPLOY && vmStatus == DkdContants.VM_STATUS_RUNNING){
            throw new ServiceException("设备正在运行中，请勿重复投放");

        }
        //如果是投放工单，设备不在运行当中，抛出异常
        if (productTypeId == DkdContants.TASK_TYPE_REPAIR && vmStatus != DkdContants.VM_STATUS_RUNNING){
            throw new ServiceException("设备不在运行中，请勿重复补货");
        }
        //如果是补货工单，设备不在运行当中，抛出异常
        if (productTypeId == DkdContants.TASK_TYPE_SUPPLY && vmStatus != DkdContants.VM_STATUS_RUNNING){
            throw new ServiceException("设备不在运行中，请勿重复补货");
        }
        //如果是撤机工单，设备不在运行当中，抛出异常
        if (productTypeId == DkdContants.TASK_TYPE_REVOKE && vmStatus != DkdContants.VM_STATUS_RUNNING){
            throw new ServiceException("设备不在运行中，请勿重复撤机");
        }


    }
}
