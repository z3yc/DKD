package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.dkd.manage.domain.AgentRun;
import com.dkd.manage.domain.AgentStep;
import com.dkd.manage.domain.AgentToolCall;
import com.dkd.manage.mapper.AgentRunMapper;
import com.dkd.manage.service.IAgentRunService;

/**
 * 智能体运行观测Service实现（F-03）。
 *
 * <p>为什么这里只做"分组"而不做业务校验：run/step/tool_call 由 Python 写入，本层是只读投影。
 * 唯一需要加工的地方是"把工具调用挂到对应轮次"——这是展示需要，不是业务规则。
 *
 * @author ruoyi
 * @date 2026-10-26
 */
@Service
public class AgentRunServiceImpl implements IAgentRunService
{
    @Autowired
    private AgentRunMapper agentRunMapper;

    @Override
    public List<AgentRun> selectAgentRunList(AgentRun query)
    {
        return agentRunMapper.selectAgentRunList(query);
    }

    @Override
    public AgentRun selectAgentRunByRunId(String runId)
    {
        return agentRunMapper.selectAgentRunByRunId(runId);
    }

    @Override
    public List<AgentStep> selectStepsWithToolCalls(String runId)
    {
        List<AgentStep> steps = agentRunMapper.selectAgentStepsByRunId(runId);
        List<AgentToolCall> calls = agentRunMapper.selectAgentToolCallsByRunId(runId);

        // 按轮次分组；用 LinkedHashMap 保持 SQL 里的 (step_no, call_seq) 顺序
        Map<Integer, List<AgentToolCall>> callsByStep = new LinkedHashMap<Integer, List<AgentToolCall>>();
        for (AgentToolCall call : calls)
        {
            if (call == null || call.getStepNo() == null)
            {
                // 脏数据（step_no 为空）不挂载：挂到哪一轮都是错的
                continue;
            }
            List<AgentToolCall> bucket = callsByStep.get(call.getStepNo());
            if (bucket == null)
            {
                bucket = new ArrayList<AgentToolCall>();
                callsByStep.put(call.getStepNo(), bucket);
            }
            bucket.add(call);
        }

        for (AgentStep step : steps)
        {
            List<AgentToolCall> bucket = callsByStep.get(step.getStepNo());
            // 空列表而不是 null：前端少一处判空，也不会出现 "undefined.length" 这类错误
            step.setToolCalls(bucket == null ? new ArrayList<AgentToolCall>() : bucket);
        }
        // 找不到对应轮次的工具调用（step_no 不在 steps 里）不展示：
        // 它说明写入侧漏了 step，属于数据缺陷；静默挂到别的轮次会把排障带偏
        return steps;
    }

    @Override
    public Map<String, Object> selectTimeline(String runId)
    {
        Map<String, Object> result = new HashMap<String, Object>();
        AgentRun run = agentRunMapper.selectAgentRunByRunId(runId);
        result.put("run", run);
        result.put("steps", run == null ? new ArrayList<AgentStep>() : selectStepsWithToolCalls(runId));
        return result;
    }
}
