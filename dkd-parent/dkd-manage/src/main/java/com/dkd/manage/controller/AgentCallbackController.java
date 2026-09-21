package com.dkd.manage.controller;

import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import com.dkd.common.annotation.Anonymous;
import com.dkd.common.annotation.RateLimiter;
import com.dkd.common.constant.DkdContants;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.LimitType;
import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.domain.dto.TaskDetailsDto;
import com.dkd.manage.domain.dto.TaskDto;
import com.dkd.manage.service.ITaskService;

/**
 * 智能体服务写操作回调（任务 0-7，方案 §3.4 / §4.3）。
 *
 * <p>为什么类上标 {@link Anonymous}：调用方是 Python 服务（服务间），没有用户 JWT。
 * 鉴权走独立的服务间密钥（{@link com.dkd.common.agent.filter.AgentCallbackAuthFilter}
 * 校验 X-Agent-Secret + 路径白名单），与用户 JWT 是两套体系，不得混用（AGENTS §7.5）。
 * 也就是说：本类不含用户身份，绝不能在这里调 {@code getUserId()} 之类的会话方法。
 *
 * <p>为什么只做参数校验 + 转调 {@code ITaskService}：Python 侧**永远不得直连数据库写入**
 * （AGENTS §1 约束 1）。工单的设备状态、未完成工单防重、员工区域一致性等校验必须复用
 * {@code TaskServiceImpl.insertTaskDto} 的既有校验链；其抛出的 ServiceException 由 RuoYi
 * 全局异常处理器原样回传 message，正是 Python 侧要展示给运营人员的失败原因。
 *
 * @author ruoyi
 * @date 2026-09-21
 */
@Anonymous
@RestController
@RequestMapping("/agent/callback")
public class AgentCallbackController extends BaseController
{
    private static final Logger log = LoggerFactory.getLogger(AgentCallbackController.class);

    @Autowired
    private ITaskService taskService;

    /**
     * 创建工单（补货/维修/投放/撤机通用入口）。
     *
     * <p>为什么加 @RateLimiter：回调接口在路径白名单内、仅限内网，但一旦 Python 侧逻辑出错
     * （例如确认按钮连点触发的重试风暴），会以服务间密钥合法身份批量建单。限流是该风险的最后一道闸
     * （方案 §8“服务间回调被滥用”缓解项）。限流命中抛 ServiceException 后直接拒绝，属 fail-closed。
     *
     * @param taskDto 工单入参（字段映射见 {@code TaskDto}），由 Python 侧按设备/货道档案组装
     * @return 成功信封；失败原因由 ServiceException 的 message 原样回传
     */
    @RateLimiter(time = 60, count = 60, limitType = LimitType.IP)
    @PostMapping("/task")
    public AjaxResult createTask(@RequestBody TaskDto taskDto)
    {
        validate(taskDto);
        // 注意：回调没有登录用户上下文，因此不设置 assignorId（不伪造创建人）。
        // 工单的创建人语义是“谁下的这单”，Python 侧已有 agent_restock_plan 记录确认人，两边可对账。
        taskService.insertTaskDto(taskDto);
        // TODO(dkd-agent 1-3, 2026-09-21): 回传 taskId/taskCode —— insertTaskDto 当前只返回影响行数，
        // 需服务层改造（返回 Task 实体）或按 task_code 回查；Python 侧幂等与
        // agent_restock_plan.task_id 回写依赖它，排期任务 1-3/1-8 落地时一并处理。
        log.info("智能体回调建单成功 innerCode={} assigneeId={} taskType={}", taskDto.getInnerCode(),
                taskDto.getUserId(), taskDto.getProductTypeId());
        return AjaxResult.success("工单创建成功");
    }

    /**
     * 参数校验（拒绝路径优先，AGENTS §8）。
     *
     * <p>为什么在进入业务 Service 前先校验：这些字段缺失会让 insertTaskDto 走到一半才失败
     * （例如 TaskDetailsMapper 的 insert 全字段用 &lt;if&gt; 包裹，缺字段会写成 NULL，可能触发库表约束报 SQL 错误），
     * 错误信息对运营人员毫无意义；
     * 提前拦住才能给出“该谁去补什么数据”的可执行提示。
     *
     * @param taskDto 工单入参
     * @throws ServiceException 参数非法
     */
    private void validate(TaskDto taskDto)
    {
        if (taskDto == null)
        {
            throw new ServiceException("工单参数不能为空");
        }
        if (StringUtils.isEmpty(taskDto.getInnerCode()))
        {
            throw new ServiceException("设备编号(innerCode)不能为空");
        }
        if (taskDto.getProductTypeId() == null)
        {
            throw new ServiceException("工单类型(productTypeId)不能为空");
        }
        if (taskDto.getUserId() == null)
        {
            throw new ServiceException("接单人(userId)不能为空");
        }
        if (DkdContants.TASK_TYPE_SUPPLY.equals(taskDto.getProductTypeId()))
        {
            validateSupplyDetails(taskDto.getDetails());
        }
    }

    /**
     * 补货工单明细校验：明细为空会让“补货工单”变成没有货道的空单，运维端无法执行。
     *
     * @param details 工单明细
     * @throws ServiceException 明细非法
     */
    private void validateSupplyDetails(List<TaskDetailsDto> details)
    {
        if (details == null || details.isEmpty())
        {
            throw new ServiceException("补货工单必须包含工单明细(details)");
        }
        int index = 0;
        for (TaskDetailsDto detail : details)
        {
            index++;
            if (detail == null)
            {
                throw new ServiceException("补货明细第 " + index + " 条为空");
            }
            if (StringUtils.isEmpty(detail.getChannelCode()))
            {
                throw new ServiceException("补货明细第 " + index + " 条缺少货道编号(channelCode)");
            }
            if (detail.getExpectCapacity() == null)
            {
                throw new ServiceException("补货明细第 " + index + " 条缺少补货数量(expectCapacity)");
            }
        }
    }
}
