package com.dkd.manage.mapper;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.domain.Task;
import com.dkd.manage.domain.vo.TaskVo;

/**
 * 工单Mapper接口
 * 
 * @author ruoyi
 * @date 2025-11-17
 */
public interface TaskMapper 
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
     * 查询设备的在途（未完成）工单——建单防重用（排期 1-8 / FIX-1）。
     *
     * <p>为什么不能复用 {@link #selectTaskList}：那条 SQL 的 task_status 是等值比较
     * （{@code task_status = #{taskStatus}}），只能查单一状态；而“未完成”在数据里有两个状态：
     * 1-待接单、2-进行中。之前只查 2，导致刚建好还没被接单的工单不在防重范围内。
     *
     * <p>状态集合由调用方传入（{@code DkdContants} 常量），不在 SQL 里硬编码数字，
     * 避免以后新增“未完成”状态时漏改这里。
     *
     * @param innerCode 设备编号
     * @param productTypeId 工单类型（补货=2，维修=1/3/4）
     * @param statuses 视为“未完成”的工单状态集合（**不可为空**，空集合会导致 in () 语法错误）
     * @return 在途工单（同一设备 + 同一工单类型），最多 1 条
     */
    List<Task> selectInflightTaskList(@Param("innerCode") String innerCode,
                                     @Param("productTypeId") Long productTypeId,
                                     @Param("statuses") List<Long> statuses);

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
     * 删除工单
     * 
     * @param taskId 工单主键
     * @return 结果
     */
    public int deleteTaskByTaskId(Long taskId);

    /**
     * 批量删除工单
     * 
     * @param taskIds 需要删除的数据主键集合
     * @return 结果
     */
    public int deleteTaskByTaskIds(Long[] taskIds);

    /**
     * 查询工单列表
     *
     * @param task 工单
     * @return 工单集合
     */

    List<TaskVo> selectTaskVoList(Task task);
}
