package com.dkd.manage.mapper;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.dkd.manage.domain.AgentEvalReport;

/**
 * 评测报告 Mapper（排期 5-1，表 {@code agent_eval_report}）。
 *
 * <p>只增不改：报告是"这一版配置当时测得怎么样"的证据，也是保存护栏的依据；
 * 能改的报告不能作为护栏证据。清理属保留策略（后续排期），不在这里开放。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface AgentEvalReportMapper
{
    /**
     * 写入一条评测报告（useGeneratedKeys 回填 id）。
     *
     * @param report 报告
     * @return 影响行数
     */
    int insertAgentEvalReport(AgentEvalReport report);

    /**
     * 某配置最近 N 条报告（倒序；页面/版本历史展示用）。
     *
     * @param configCode 配置编码
     * @param limit      条数上限（调用方已限制 ≤100）
     * @return 报告列表
     */
    List<AgentEvalReport> selectLatestByCode(@Param("configCode") String configCode, @Param("limit") int limit);

    /**
     * 某配置**某版本**的最新一条报告（不按 status 过滤——护栏需要知道"最新是 blocked/failed"）。
     *
     * @param configCode    配置编码
     * @param configVersion 配置版本
     * @return 最新报告；从未评测返回 null
     */
    AgentEvalReport selectLatestForVersion(@Param("configCode") String configCode,
            @Param("configVersion") Long configVersion);
}
