package com.dkd.manage.service.impl;

import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.dkd.manage.domain.AgentDecisionLog;
import com.dkd.manage.mapper.AgentDecisionLogMapper;
import com.dkd.manage.service.IAgentDecisionLogService;

/**
 * 智能体决策留痕Service实现（排期任务 3-7）。
 *
 * <p><b>为什么只有一个转发层、没有业务逻辑：</b>这是审计只读查询，没有校验链、
 * 没有事务、没有跨表写 —— 加“逻辑”反而会让“留痕原样呈现”这个唯一目标走样。
 * 若将来要在列表上做统计（如按结果聚合），再考虑放这里。
 *
 * @author ruoyi
 * @date 2026-10-01
 */
@Service
public class AgentDecisionLogServiceImpl implements IAgentDecisionLogService
{
    @Autowired
    private AgentDecisionLogMapper agentDecisionLogMapper;

    @Override
    public List<AgentDecisionLog> selectAgentDecisionLogList(AgentDecisionLog log)
    {
        return agentDecisionLogMapper.selectAgentDecisionLogList(log);
    }

    @Override
    public AgentDecisionLog selectAgentDecisionLogById(Long id)
    {
        return agentDecisionLogMapper.selectAgentDecisionLogById(id);
    }
}
