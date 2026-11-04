package com.dkd.manage.service;

import java.util.List;
import java.util.Map;

import com.dkd.manage.agent.AgentEvalGateResult;
import com.dkd.manage.domain.AgentEvalReport;
import com.dkd.manage.domain.dto.AgentEvalReportDto;

/**
 * 评测报告与保存护栏 Service（排期 5-1）。
 *
 * <p>两层职责：
 * <ol>
 *   <li><b>报告落库/查询</b>：只增证据，给版本历史与页面看；</li>
 *   <li><b>保存护栏判定</b>：{@link #checkGate} 供配置编辑保存调用——
 *       无报告/最新非 scored/准确率低于基线 → 拒绝；开关关闭时调用方直接跳过本层。</li>
 * </ol>
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public interface IAgentEvalReportService
{
    /**
     * 上报一条评测报告（校验结构后只增落库）。
     *
     * @param dto      入参
     * @param operator 当前登录人（DTO 未带 runBy 时作为执行人）
     * @return 落库后的报告
     */
    AgentEvalReport insertReport(AgentEvalReportDto dto, String operator);

    /**
     * 某配置最近 N 条报告（倒序）。
     *
     * @param configCode 配置编码
     * @param limit      条数上限（null → 20；最大 100）
     * @return 报告列表
     */
    List<AgentEvalReport> selectLatestByCode(String configCode, Integer limit);

    /**
     * 护栏状态查询（页面展示用）：{@code {enabled, passed, latest, reason}}。
     *
     * <p>关闭时 passed 恒 true（不拦）并说明"未启用"；configVersion 为空时无法判定，
     * 返回 passed=true + latest=null（真正的判定发生在保存时，以库内当前版本为准）。
     *
     * @param configCode    配置编码
     * @param configVersion 配置版本（可空）
     * @return 状态对象
     */
    Map<String, Object> selectGate(String configCode, Long configVersion);

    /**
     * 按 (code, version) 判定是否允许保存（不做开关判断，调用方决定是否启用护栏）。
     *
     * @param configCode    配置编码
     * @param configVersion 配置版本
     * @return 判定结果（含原因与最新报告）
     */
    AgentEvalGateResult checkGate(String configCode, Long configVersion);
}
