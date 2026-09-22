package com.dkd.manage.service;

import java.util.List;
import com.dkd.manage.domain.Task;
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.domain.vo.TaskVo;

/**
 * 工单Service接口
 * 
 * @author ruoyi
 * @date 2025-11-17
 */
public interface ITaskService 
{
    /**
     * 查询工单
     * 
     * @param taskId 工单主键
     * @return 工单
     */
    public Task selectTaskByTaskId(Long taskId);

    /**
     * 查询工单列表
     * 
     * @param task 工单
     * @return 工单集合
     */
    public List<Task> selectTaskList(Task task);

    /**
     * 新增工单
     * 
     * @param task 工单
     * @return 结果
     */
    public int insertTask(Task task);

    /**
     * 修改工单
     * 
     * @param task 工单
     * @return 结果
     */
    public int updateTask(Task task);

    /**
     * 批量删除工单
     * 
     * @param taskIds 需要删除的工单主键集合
     * @return 结果
     */
    public int deleteTaskByTaskIds(Long[] taskIds);

    /**
     * 删除工单信息
     * 
     * @param taskId 工单主键
     * @return 结果
     */
    public int deleteTaskByTaskId(Long taskId);

    /**
     * 查询工单列表
     *
     * @param task 工单
     * @return 工单集合
     */

    List<TaskVo> selectTaskVoList(Task task);

    /**
     * 批量新增工单
     */
    int insertTaskDto(TaskDto taskDto);

    /**
     * 创建工单并返回落库后的实体（含 taskId / taskCode）。
     *
     * <p>为什么需要：智能体回调（排期 1-3）要把工单号回传给 Python 侧，用于
     * {@code agent_restock_plan.task_id} 回写与“重复确认返回已建单”的幂等对账；
     * 原 {@link #insertTaskDto} 只返回影响行数，拿不到工单号。
     *
     * @param taskDto 工单入参
     * @return 已落库的工单
     */
    Task insertTaskDtoReturningTask(TaskDto taskDto);
    /**
     * 工单取消
     */

    int cancelTask(Task task);
}
