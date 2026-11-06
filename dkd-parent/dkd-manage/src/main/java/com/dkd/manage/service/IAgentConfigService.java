package com.dkd.manage.service;

import java.util.List;
import java.util.Map;

import com.dkd.manage.domain.AgentConfig;
import com.dkd.manage.domain.AgentConfigHistory;
import com.dkd.manage.domain.dto.AgentConfigDto;
import com.dkd.manage.domain.dto.AgentConfigEnabledDto;
import com.dkd.manage.domain.dto.AgentConfigRollbackDto;
import com.dkd.manage.domain.vo.AgentConfigVo;

/**
 * 智能体配置 Service（排期 2-2，契约 §4）。
 *
 * <p>写方法都要求调用方传入 {@code operator}（当前登录账号）：本层**不直接读
 * {@code SecurityUtils}**——把「操作者是谁」留在 Web 层取，既符合「Controller 只做参数
 * 校验 + 调 Service」的边界，也让单测不必伪造安全上下文（AGENTS §8：Java 单测不依赖运行期环境）。
 *
 * <p>字段级校验失败抛 {@code AgentConfigValidationException}（→ HTTP 200 + code 400），
 * 乐观锁冲突抛 {@code AgentConfigConflictException}（→ HTTP 409），其余业务错误抛
 * {@code ServiceException}（→ RuoYi 惯例 code 500）。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public interface IAgentConfigService
{
    /**
     * 配置列表（分页由 Controller 的 startPage 控制）。
     *
     * @param query 过滤条件（code/name 模糊，runtime/enabled/builtin 精确）
     * @return 配置列表（paramsJson 已转为对象）
     */
    List<AgentConfigVo> selectAgentConfigList(AgentConfig query);

    /**
     * 配置详情。
     *
     * @param id 主键
     * @return 配置详情
     */
    AgentConfigVo selectAgentConfigById(Long id);

    /**
     * 某配置的全部历史版本（按版本倒序，含快照全文）。
     *
     * @param id 配置主键（内部按 code 查历史）
     * @return 历史列表
     */
    List<AgentConfigHistory> selectHistoryByConfigId(Long id);

    /**
     * 元数据（契约 §4.6）：runtimes/models/tools/placeholders/defaultPrompt/defaultParams/paramSpecs/sceneBase。
     *
     * @param runtime 目标载体（为空/未知时按 chat 返回，保证新增表单总有可用的默认值）
     * @return 元数据
     */
    Map<String, Object> selectMeta(String runtime);

    /**
     * 新增配置（本期只允许 chat 载体；builtin 强制 0；scene 自动分配 ≥101）。
     *
     * @param dto      入参
     * @param operator 操作者
     * @return 落库后的配置（version=1）
     */
    AgentConfigVo insertAgentConfig(AgentConfigDto dto, String operator);

    /**
     * 编辑配置（乐观锁；版本 +1 并写历史快照；内置/自定义的 code/runtime/builtin 只读）。
     *
     * @param dto      入参（version 必带）
     * @param operator 操作者
     * @return 保存后的配置（version 已 +1）
     */
    AgentConfigVo updateAgentConfig(AgentConfigDto dto, String operator);

    /**
     * 启用/停用（版本 +1 并写历史快照；停用必填原因 5~100 字）。
     *
     * @param id       配置主键
     * @param dto      入参
     * @param operator 操作者
     * @return 保存后的配置（version 已 +1）
     */
    AgentConfigVo updateEnabled(Long id, AgentConfigEnabledDto dto, String operator);

    /**
     * 版本回滚：把历史快照复制成新版本（当前 version + 1），再写一条历史。
     *
     * @param id       配置主键
     * @param dto      入参（targetVersion/version 必带）
     * @param operator 操作者
     * @return 回滚后的配置（version 已 +1）
     */
    AgentConfigVo rollback(Long id, AgentConfigRollbackDto dto, String operator);
}
