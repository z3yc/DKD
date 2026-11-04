package com.dkd.manage.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.dkd.common.exception.ServiceException;
import com.dkd.common.utils.StringUtils;
import com.dkd.manage.agent.AgentFieldErrorException;
import com.dkd.manage.domain.AgentTool;
import com.dkd.manage.domain.dto.AgentToolEditDto;
import com.dkd.manage.mapper.AgentToolMapper;
import com.dkd.manage.service.IAgentToolService;

/**
 * 智能体工具中心 Service 实现（排期 3-4）。
 *
 * <p>本类的核心职责不是"更新一行"，而是**拦住不可变字段的修改**：
 * {@code code/source/readOnly/builtin} 一旦能被改，{@code read_only=0} 的写工具就可能被
 * 改成"可被模型直接调用"，绕过 AGENTS §7.9「写操作必须人工确认」。因此这里对每个不可变字段
 * 都做"传了且与库内不同 → 字段错误"的判断（静默忽略会让用户以为改成功了），
 * SQL 侧也不 set 这些列（纵深防御）。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
@Service
public class AgentToolServiceImpl implements IAgentToolService
{
    /** 工具超时下限（毫秒）：低于 100ms 的查询基本必然超时，等于把工具配成"永远失败" */
    private static final int TIMEOUT_MS_MIN = 100;

    /**
     * 工具超时上限（毫秒）。30000 而不是无限：工具调用在 LLM 轮次内同步执行，
     * 单次超过 30s 会让对话轮次与连接池一起被拖住（与 Python 侧 read_tools 的护栏同一量级）。
     */
    private static final int TIMEOUT_MS_MAX = 30000;

    /** 返回行数下限 */
    private static final int ROW_LIMIT_MIN = 1;

    /** 返回行数上限 5000：与表单/HTML 渲染能力相匹配，再大只会让模型上下文爆掉 */
    private static final int ROW_LIMIT_MAX = 5000;

    @Autowired
    private AgentToolMapper agentToolMapper;

    @Override
    public List<AgentTool> selectAgentToolList(AgentTool query)
    {
        AgentTool condition = query == null ? new AgentTool() : query;
        // 过滤值去空白："  chat  " 会匹配不到任何行，且不会有任何提示
        condition.setScope(trimToNull(condition.getScope()));
        condition.setSource(trimToNull(condition.getSource()));
        List<AgentTool> list = agentToolMapper.selectAgentToolList(condition);
        return list == null ? new ArrayList<AgentTool>() : list;
    }

    @Override
    @Transactional
    public AgentTool updateAgentTool(Long id, AgentToolEditDto dto, String operator)
    {
        if (id == null)
        {
            throw new ServiceException("工具ID不能为空");
        }
        if (dto == null)
        {
            throw new ServiceException("请求体不能为空");
        }
        AgentTool current = agentToolMapper.selectAgentToolById(id);
        if (current == null)
        {
            throw new ServiceException("工具不存在：" + id);
        }
        rejectImmutableChanges(current, dto);
        if (dto.getEnabled() == null && dto.getTimeoutMs() == null && dto.getRowLimit() == null)
        {
            throw new ServiceException("没有需要修改的字段（enabled/timeoutMs/rowLimit 至少传一个）");
        }

        int enabled = dto.getEnabled() == null ? intValue(current.getEnabled()) : dto.getEnabled().intValue();
        int timeoutMs = dto.getTimeoutMs() == null ? intValue(current.getTimeoutMs()) : dto.getTimeoutMs().intValue();
        int rowLimit = dto.getRowLimit() == null ? intValue(current.getRowLimit()) : dto.getRowLimit().intValue();
        validateEnabled(enabled);
        validateTimeoutMs(timeoutMs);
        validateRowLimit(rowLimit);

        AgentTool update = new AgentTool();
        update.setId(id);
        update.setEnabled(Integer.valueOf(enabled));
        update.setTimeoutMs(Integer.valueOf(timeoutMs));
        update.setRowLimit(Integer.valueOf(rowLimit));
        update.setUpdateBy(operator);
        update.setUpdateTime(new Date());
        int rows = agentToolMapper.updateAgentTool(update);
        if (rows != 1)
        {
            // 不存在已在上方拦截；走到这里说明读取后被并发删除，必须报错而不是假装成功
            throw new ServiceException("工具更新失败：记录可能已被删除（id=" + id + "）");
        }
        return agentToolMapper.selectAgentToolById(id);
    }

    /**
     * 拦住不可变字段的修改。
     *
     * <p>为什么"没传"要放过：前端编辑弹窗可能只提交三个可变字段，
     * DTO 里不可变字段为 null 属于正常情况，不能因此报错。
     *
     * @param current 库内当前行
     * @param dto     入参
     */
    private void rejectImmutableChanges(AgentTool current, AgentToolEditDto dto)
    {
        String code = trimToNull(dto.getCode());
        if (code != null && !code.equals(current.getCode()))
        {
            throw new AgentFieldErrorException("code", "工具编码不可修改：库内为 " + current.getCode());
        }
        String source = trimToNull(dto.getSource());
        if (source != null && !source.equals(current.getSource()))
        {
            throw new AgentFieldErrorException("source", "工具来源不可修改：库内为 " + current.getSource());
        }
        if (dto.getReadOnly() != null && dto.getReadOnly().intValue() != intValue(current.getReadOnly()))
        {
            // 写工具（read_only=0）绝不能被改成只读，否则模型就能直接调用会产生副作用的能力
            throw new AgentFieldErrorException("readOnly", "只读属性不可修改（写工具必须保持 read_only=0）");
        }
        if (dto.getBuiltin() != null && dto.getBuiltin().intValue() != intValue(current.getBuiltin()))
        {
            throw new AgentFieldErrorException("builtin", "内置标记不可修改");
        }
    }

    private void validateEnabled(int enabled)
    {
        if (enabled != 0 && enabled != 1)
        {
            throw new AgentFieldErrorException("enabled", "启用状态只能是 0 或 1");
        }
    }

    private void validateTimeoutMs(int timeoutMs)
    {
        if (timeoutMs < TIMEOUT_MS_MIN || timeoutMs > TIMEOUT_MS_MAX)
        {
            throw new AgentFieldErrorException("timeoutMs",
                    "超时需在 " + TIMEOUT_MS_MIN + "~" + TIMEOUT_MS_MAX + " 毫秒之间（当前 " + timeoutMs + "）");
        }
    }

    private void validateRowLimit(int rowLimit)
    {
        if (rowLimit < ROW_LIMIT_MIN || rowLimit > ROW_LIMIT_MAX)
        {
            throw new AgentFieldErrorException("rowLimit",
                    "行数上限需在 " + ROW_LIMIT_MIN + "~" + ROW_LIMIT_MAX + " 之间（当前 " + rowLimit + "）");
        }
    }

    private int intValue(Integer value)
    {
        return value == null ? 0 : value.intValue();
    }

    private String trimToNull(String value)
    {
        return StringUtils.isEmpty(StringUtils.trim(value)) ? null : StringUtils.trim(value);
    }
}
