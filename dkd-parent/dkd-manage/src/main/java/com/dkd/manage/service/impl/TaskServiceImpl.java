package com.dkd.manage.service.impl;

import java.time.Duration;
import java.util.Arrays;
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
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.DuplicateKeyException;
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
    private static final Logger log = LoggerFactory.getLogger(TaskServiceImpl.class);

    /**
     * 唯一键冲突时对用户展示的统一文案（FIX-1 配套改动）。
     *
     * <p>为什么不再让它变成“系统异常”：加了 {@code uk_task_active_dedup} 唯一约束后，
     * 并发建单的落败方不再收到“设备有未完成工单”，而是 MyBatis 抛出的 1062 —— 用户看到的是
     * 不可读的错误。文案同时覆盖两种唯一键：
     * ① 活跃工单去重键（同设备同类型已有未完成工单）；② {@code tb_task.task_code} 唯一键
     * （Redis 流水被清空/多实例编号撞车时也会抛 1062）。
     */
    private static final String TASK_CREATE_CONFLICT_MESSAGE = "设备有未完成工单或工单号冲突，请刷新重试";

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

        // 检查设备是否有未完成的工单（防重）
        // 排期 1-8 / FIX-1 修正：原先固定查 TASK_STATUS_PROGRESS(2-进行中)，
        // 而刚建好的工单是 TASK_STATUS_CREATE(1-待接单)——最需要防的那一类反而查不到，
        // 于是“先查后插”只在“已有人接单”时生效（Agent 回调建单时几乎总是 status=1）。
        // 现改为查「未完成 = 1-待接单 + 2-进行中」，与异常文案“设备有未完成工单”一致。
        // 注意：这里仍是先查后插，**并发窗口依旧存在**（表上无 (inner_code, product_type_id, status)
        // 唯一索引），需要强一致时由调用方在自有表上做 CAS/幂等键（Agent 侧见排期 1-8）。
        List<Task> taskList = taskMapper.selectInflightTaskList(
                taskDto.getInnerCode(),
                taskDto.getProductTypeId(),
                Arrays.asList(DkdContants.TASK_STATUS_CREATE, DkdContants.TASK_STATUS_PROGRESS));
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
        // 为什么把“插入 + 明细”一起包在 try 里（FIX-1 的 Java 配套改动，2026-10-01）：
        // 唯一约束（uk_task_active_dedup）会把并发落败方从“ServiceException 业务拒绝”变成
        // DataIntegrityViolationException / DuplicateKeyException（1062）——
        // 对用户而言就是“系统异常”，而正确的语义是“已经有人建了单 / 工单号撞了”。
        // 这里把两类完整性异常都翻译成同一条**可读业务文案**；原始异常必须挂在 cause 上
        // （数据完整性异常的原因不止一种，日志要能查到真因，不能因为改了文案就丢掉证据）。
        // @Transactional 下抛出 ServiceException（RuntimeException）会整体回滚，不会留半个工单。
        try {
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
        } catch (DuplicateKeyException e) {
            // 唯一键冲突：活跃工单去重键或 task_code（业务语义清晰，可原样重试）
            log.warn("创建工单命中唯一键冲突，转业务文案：innerCode={} productTypeId={} taskCode={}",
                    taskDto.getInnerCode(), taskDto.getProductTypeId(), task.getTaskCode(), e);
            throw new ServiceException(TASK_CREATE_CONFLICT_MESSAGE, e);
        } catch (DataIntegrityViolationException e) {
            // 其它完整性约束（非唯一键）在正常入参下不应发生；一并转成可读文案，
            // 但保留原异常作为 cause，排障时从日志能拿到真正的约束名。
            log.warn("创建工单命中数据完整性约束，转业务文案：innerCode={} productTypeId={} taskCode={}",
                    taskDto.getInnerCode(), taskDto.getProductTypeId(), task.getTaskCode(), e);
            throw new ServiceException(TASK_CREATE_CONFLICT_MESSAGE, e);
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
     * 校验「设备状态 × 工单类型」是否允许建单。
     *
     * <p>2026-10-26 变更（业务决策，故障诊断 → 派单场景的前置条件）：**维修工单允许"停机"设备创建**。
     * 原实现要求维修工单必须 `vm_status=1（运行中）`，但现场停机（未撤机）的设备恰恰最需要维修，
     * 于是形成"停机 → 建不了维修单 → 永远停机"的死循环。现允许 {1-运行中, 2-停机}，
     * 仍拒绝 0-未投放（还没装）与 3-已撤机（已撤走），两者都不属于维修范围。
     *
     * <p>为什么改用常量.equals 而不是 ==：productTypeId / vmStatus 都是 Long 包装类型，
     * `==` 比的是引用，只因 Long 的 -128~127 缓存才碰巧成立；换 `.equals` 后不再依赖缓存实现，
     * 也不会在 productTypeId 为 null 时 NPE（常量在左侧）。
     *
     * @param vmStatus 售货机当前状态（tb_vending_machine.vm_status）
     * @param productTypeId 工单类型（1-投放 2-补货 3-维修 4-撤机）
     */
    private void checkCreateTask(Long vmStatus, Long productTypeId)
    {
        // 投放：设备已在运行中，重复投放没有意义
        if (DkdContants.TASK_TYPE_DEPLOY.equals(productTypeId)
                && DkdContants.VM_STATUS_RUNNING.equals(vmStatus))
        {
            throw new ServiceException("设备正在运行中，请勿重复投放");
        }
        // 维修：运行中或停机均可（停机≠撤机，现场仍需维修）
        if (DkdContants.TASK_TYPE_REPAIR.equals(productTypeId) && !isRepairAllowed(vmStatus))
        {
            throw new ServiceException("设备未投放或已撤机，无法创建维修工单");
        }
        // 补货：必须在运行中（给停机设备补货没有意义，还会掩盖故障）
        if (DkdContants.TASK_TYPE_SUPPLY.equals(productTypeId)
                && !DkdContants.VM_STATUS_RUNNING.equals(vmStatus))
        {
            throw new ServiceException("设备不在运行中，请勿重复补货");
        }
        // 撤机：必须在运行中（未投放无需撤机；已撤机不可重复撤机）
        if (DkdContants.TASK_TYPE_REVOKE.equals(productTypeId)
                && !DkdContants.VM_STATUS_RUNNING.equals(vmStatus))
        {
            throw new ServiceException("设备不在运行中，请勿重复撤机");
        }
    }

    /**
     * 维修工单允许的设备状态：运行中（1）与停机（2）。
     *
     * <p>为什么显式列出允许集合，而不是"只要不是 0/3 就放行"：将来新增状态（如报废）时，
     * 显式集合会**默认拒绝**并强制回来做一次判断；"非 0/3"这种写法会让新状态静默变成"可维修"。
     *
     * @param vmStatus 售货机状态
     * @return true 表示允许创建维修工单
     */
    private boolean isRepairAllowed(Long vmStatus)
    {
        return DkdContants.VM_STATUS_RUNNING.equals(vmStatus)
                || DkdContants.VM_STATUS_STOPPED.equals(vmStatus);
    }
}
