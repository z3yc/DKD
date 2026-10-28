package com.dkd.manage.service.impl;

import java.time.Duration;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.util.StrUtil;
import com.dkd.common.constant.DkdContants;
import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.Channel;
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
import com.dkd.manage.mapper.ChannelMapper;
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

    /**
     * 创建类型（tb_task.create_type）：0-自动（智能体回调）/ 1-手动（后台建单）。
     *
     * <p>为什么放进 Service 而不是只靠调用方传：创建类型是**审计口径**（区分“人下的单”与
     * “Agent 建议后人工确认的单”），缺省不能是 null 或 0。两个入口显式赋值
     * （{@code AgentCallbackController} 置 0、{@code TaskController} 置 1），这里再兜一层缺省手动。
     */
    private static final Long CREATE_TYPE_AUTO = 0L;
    private static final Long CREATE_TYPE_MANUAL = 1L;

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

    @Autowired
    private ChannelMapper channelMapper;

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
        // 状态只能经状态机（接单 1→2 / 完成 2→4 / 取消 →3，见 docs/DKD业务规范-清晰版 §3.2）。
        // 为什么在这里拦：TaskController.edit 是**通用编辑**入口，历史上可以顺手把 task_status
        // 改掉，绕过 app 的“操作人=执行人”校验，等于状态机有第二个写入口。
        // 注意 cancelTask 直接调 taskMapper.updateTask（不走本方法），后台取消不受影响。
        Task taskDb = taskMapper.selectTaskByTaskId(task.getTaskId());
        if (taskDb == null) {
            throw new ServiceException("工单不存在");
        }
        if (task.getTaskStatus() != null) {
            if (!task.getTaskStatus().equals(taskDb.getTaskStatus())) {
                throw new ServiceException("请通过接单/完成/取消操作变更工单状态");
            }
            // 即使“同值”也清空：确保通用编辑的 UPDATE 语句根本不写 task_status 列，
            // 状态列只保留状态机一个写入口（未来新增状态流转也不会被这里顺手带过）。
            task.setTaskStatus(null);
        }
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
     * <p>校验顺序与异常文案是**回调契约**：Python 侧会把这些 message 原样展示给运营，
     * 顺序与措辞都按 {@code docs/DKD业务规范-清晰版.md §4.1} 固定，改动前先改规范。
     * 顺序：设备存在 → 类型白名单 → 状态×类型 → 在途防重 → 员工存在/启用/角色/区域 →
     * 补货明细（归属/数量/容量/商品）→ 落库（唯一键冲突转可读文案）。
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
        // 员工必须启用：停用账号派单后无人能接（app 登录即失败），等于把工单挂死在系统里。
        // 用常量.equals(null) 为 false 的写法，脏数据（status 为 NULL）一并按“停用”拒绝。
        if(!DkdContants.EMP_STATUS_NORMAL.equals(emp.getStatus())){
            throw new ServiceException("员工已停用");
        }
        // 员工角色必须匹配工单类型（docs/DKD业务规范-清晰版 §4.1 第 6 条）：
        // 补货 = 运营(1002) 或 管理(1001)；维修/投放/撤机 = 维修(1003) 或 管理(1001)。
        // 为什么必须校验：角色不匹配的工单在运维端不会出现在对应作业队列里，建了也没人执行。
        if(!isRoleAllowedForTaskType(taskDto.getProductTypeId(), emp.getRoleCode())){
            throw new ServiceException("该员工角色不能执行此类工单");
        }
        //校验员工区域和设备区域是否一致（任一为空都视为不一致：null 区域无法做“同区域派单”的对账依据）
        if(emp.getRegionId() == null || vm.getRegionId() == null
                || !emp.getRegionId().equals(vm.getRegionId())){
            throw new ServiceException("员工区域与设备区域不一致，请勿创建工单");
        }
        // 补货工单的货道明细校验（后台与 Agent 回调共用同一条链）：
        // 空单品进入运维端后无法执行；货道/数量/商品任一不符都会导致“补货补错货道”。
        if(DkdContants.TASK_TYPE_SUPPLY.equals(taskDto.getProductTypeId())){
            checkSupplyDetails(taskDto.getDetails(), vm);
        }
        //将dto转换成po并补充属性，保存工单
       Task task = BeanUtil.copyProperties(taskDto,Task.class);
        task.setTaskStatus(DkdContants.TASK_STATUS_CREATE);
        // 创建类型是审计口径：只接受 0/1，缺省按“后台手动”处理（两个 Controller 会显式赋值）
        if(taskDto.getCreateType() != null
                && !CREATE_TYPE_AUTO.equals(taskDto.getCreateType())
                && !CREATE_TYPE_MANUAL.equals(taskDto.getCreateType())){
            throw new ServiceException("创建类型不合法");
        }
        task.setCreateType(taskDto.getCreateType() != null ? taskDto.getCreateType() : CREATE_TYPE_MANUAL);
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
        // 类型白名单必须放在最前面（docs/DKD业务规范-清晰版 §4.1 第 2 条）：
        // 原来的 4 个 if 各自判断类型，未知类型（如 5）**一个分支都进不去**，等于静默放行，
        // 会落库成一张运维端看不懂、状态机也不知道怎么流转的工单。
        if (!isTaskTypeAllowed(productTypeId))
        {
            throw new ServiceException("工单类型不合法");
        }
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

    /**
     * 工单类型白名单：1 投放 / 2 补货 / 3 维修 / 4 撤机（{@link DkdContants} 完整枚举）。
     *
     * <p>为什么显式列出而不是 {@code productTypeId >= 1 && <= 4}：与
     * {@link #isRepairAllowed} 同理——将来类型扩成 5/6 时必须回来做一次判断，
     * 而不是被范围条件自动放行（字典表 {@code tb_task_type} 可交叉验证）。
     *
     * @param productTypeId 工单类型
     * @return true 表示在合法枚举内
     */
    private boolean isTaskTypeAllowed(Long productTypeId)
    {
        return DkdContants.TASK_TYPE_DEPLOY.equals(productTypeId)
                || DkdContants.TASK_TYPE_SUPPLY.equals(productTypeId)
                || DkdContants.TASK_TYPE_REPAIR.equals(productTypeId)
                || DkdContants.TASK_TYPE_REVOKE.equals(productTypeId);
    }

    /**
     * 员工角色是否可执行指定类型的工单。
     *
     * <p>口径（docs/DKD业务规范-清晰版 §4.1，2026-10-28 按"端上真实选人来源"校正）：
     * <ul>
     *   <li>补货 → 1002 运营（后台 {@code businessList}、智能体补货派单都是 1002）；</li>
     *   <li>维修 / 投放 / 撤机 → 1003 维修（后台 {@code operationList}、运维端作业队列都是 1003）；</li>
     *   <li>1001 管理人员对任意类型放行（值班/兜底）。</li>
     * </ul>
     * 为什么不是"投放/撤机按运营"：后台运维工单页的下拉取的是 {@code operationList}（1003），
     * 规则若写 1002，会把"界面上能选到的人"全部拒掉——规则必须与真实入口一致，否则就是新的混乱。
     * 类型白名单已在 {@link #checkCreateTask} 先校验，走到最后的 {@code return false} 只可能是白名单外的值（拒绝优先）。
     *
     * @param productTypeId 工单类型（已过白名单）
     * @param roleCode 员工角色编码（tb_emp.role_code）
     * @return true 表示角色匹配
     */
    private boolean isRoleAllowedForTaskType(Long productTypeId, String roleCode)
    {
        if (DkdContants.TASK_TYPE_REPAIR.equals(productTypeId)
                || DkdContants.TASK_TYPE_DEPLOY.equals(productTypeId)
                || DkdContants.TASK_TYPE_REVOKE.equals(productTypeId))
        {
            return DkdContants.ROLE_CODE_OPERATOR.equals(roleCode)
                    || DkdContants.ROLE_CODE_ADMIN.equals(roleCode);
        }
        if (DkdContants.TASK_TYPE_SUPPLY.equals(productTypeId))
        {
            return DkdContants.ROLE_CODE_BUSINESS.equals(roleCode)
                    || DkdContants.ROLE_CODE_ADMIN.equals(roleCode);
        }
        return false;
    }

    /**
     * 补货工单明细校验（后台与智能体回调共用的唯一校验链）。
     *
     * <p>为什么逐条校验而不是“明细非空就算过”：
     * <ul>
     *   <li>货道不属于该设备 → 运维到了现场按错的货道补货，库存与货道缓存双错；</li>
     *   <li>数量超过货道容量 → app 完成时必然溢出（历史缺陷：无条件累加可超容量）；</li>
     *   <li>数量为空/非正 → 补货完成等于 0 增量，工单“完成了但什么也没补”；</li>
     *   <li>明细 skuId 与货道档案不一致 → 补进去的商品和货道卖的商品不是同一种。</li>
     * </ul>
     * 异常文案保持中文可读，Python 侧会原样展示给运营（AGENTS §6.1）。
     *
     * @param details 工单明细
     * @param vm 设备（用于限定“货道属于该设备”）
     * @throws ServiceException 明细非法
     */
    private void checkSupplyDetails(List<TaskDetailsDto> details, VendingMachine vm)
    {
        if (CollUtil.isEmpty(details))
        {
            throw new ServiceException("补货工单必须包含明细");
        }
        // 一次取回该设备全部货道再在内存里找：逐条明细查库是典型 N+1（AGENTS §10）。
        Map<String, Channel> channelMap = loadChannels(vm.getInnerCode());
        int index = 0;
        for (TaskDetailsDto detail : details)
        {
            index++;
            if (detail == null)
            {
                throw new ServiceException("补货明细第 " + index + " 条为空");
            }
            String channelCode = detail.getChannelCode();
            if (StrUtil.isBlank(channelCode))
            {
                throw new ServiceException("补货明细第 " + index + " 条缺少货道编号(channelCode)");
            }
            Channel channel = channelMap.get(channelCode);
            if (channel == null)
            {
                throw new ServiceException("货道不属于该设备");
            }
            Long expectCapacity = detail.getExpectCapacity();
            if (expectCapacity == null || expectCapacity <= 0L)
            {
                throw new ServiceException("补货数量必须大于 0");
            }
            Long maxCapacity = channel.getMaxCapacity();
            // 容量缺失时 fail-closed：无法证明“没超容量”就不建单，避免历史上“无条件累加”的溢出重演
            if (maxCapacity == null)
            {
                throw new ServiceException("货道未配置最大容量，无法校验补货数量");
            }
            if (expectCapacity > maxCapacity)
            {
                throw new ServiceException("补货数量超出货道容量");
            }
            // skuId 缺省时跳过比对（历史数据/调用方可能不带）；带了就必须与货道档案一致
            if (detail.getSkuId() != null && !detail.getSkuId().equals(channel.getSkuId()))
            {
                throw new ServiceException("货道商品与明细商品不一致");
            }
        }
    }

    /**
     * 取设备全部货道并建立 channelCode → Channel 索引（单次查询，避免 N+1）。
     *
     * @param innerCode 设备编号
     * @return 货道索引（无货道时为空 Map，不返回 null）
     */
    private Map<String, Channel> loadChannels(String innerCode)
    {
        Channel query = new Channel();
        query.setInnerCode(innerCode);
        List<Channel> channels = channelMapper.selectChannelList(query);
        Map<String, Channel> channelMap = new HashMap<String, Channel>();
        if (channels != null)
        {
            for (Channel channel : channels)
            {
                if (channel != null && channel.getChannelCode() != null)
                {
                    channelMap.put(channel.getChannelCode(), channel);
                }
            }
        }
        return channelMap;
    }
}
