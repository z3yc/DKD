package com.dkd.manage.mapper;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.domain.AgentLlmProbe;

/**
 * 模型连通性探测记录 Mapper（排期 3-5，表 {@code agent_llm_probe}）。
 *
 * <p>只增不改：探测记录是"上次测试连通的结果"这一证据，后台不提供 update/delete。
 * 清理属排期 4-4 的保留策略，由 Java/运维执行，不在这里开口子。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentLlmProbeMapper
{
    /**
     * 写入一条探测记录。
     *
     * @param probe 记录（ok/httpStatus/latencyMs/modelServed/errorType/errorMsg/requestId/createBy/createTime）
     * @return 影响行数
     */
    int insertAgentLlmProbe(AgentLlmProbe probe);

    /**
     * 按档案编码取探测记录（倒序；分页由 PageHelper 负责）。
     *
     * @param profileCode 档案编码
     * @return 记录列表
     */
    List<AgentLlmProbe> selectAgentLlmProbeList(@Param("profileCode") String profileCode);
}
