# Changelog

本文件记录 DKD 平台的用户可见变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)，权威来源是仓库根 `VERSION` 文件。

分类：`Added` 新增 / `Changed` 变更 / `Fixed` 修复 / `Security` 安全 / `Removed` 移除 / `Docs` 文档。
版本与 tag 规则见 `AGENTS.md` §5.3。

## [Unreleased]

### Added

- 故障诊断 M4（审核官）：计划中，见 `docs/agent-fault-scenario-plan-2026-10-26.md`。

## [0.3.0] - 2026-10-28

故障诊断派单闭环（M3）。

### Added

- **派单提案（F-08）**：诊断完成后生成派单提案——接单人按设备区域匹配、优先级由证据强度决定
  （高严重度证据→高）、`desc` 带设备号/证据条数/在途工单数；提案必须引用 `basedOnEvidence` 与
  `assigneeBasis`（区域/候选数/在途数），引用越界或缺依据直接拒绝生成。提案**只落库、不建单**，
  run 停在 `waiting_confirm` 等人工确认。
- **派单确认（F-09）**：运营确认后经 Java 回调创建**维修工单**（`productTypeId=3`）；human-in-the-loop：
  重复确认返回同一工单（幂等，不重复回调）；业务拒绝（设备有在途工单/区域不一致）与传输失败回退到待确认并留 step 错误；
  人工驳回需填原因且不建单；确认/驳回都写 `agent_step` 与 `agent_decision_log`（含操作人 user_id）。
- **接口与网关（F-09b）**：`POST /agent/fault/runs`（需 `manage:vm:query`）、
  `POST /agent/fault/runs/{runId}/confirm`（需 `manage:task:add`）；Java 网关按方法+路径形状判权，
  非法方法/runId 一律 404。
- **前端确认入口（F-09c）**：运行时间线页新增「发起故障诊断」、故障运行抽屉里的「派单提案」卡片、
  「确认建单 / 驳回」（驳回原因必填）；无 `manage:task:add` 权限不显示按钮；确认结果区分
  confirmed / duplicate / 未建单三种提示。

### Changed

- 确认流程实现采用「DB 状态机两阶段」而非 LangGraph `interrupt()`（决策 D11：确认是跨进程 HTTP 动作，
  DB 状态机即协调器，幂等兜底是 Java 在途工单防重 + claim 回查）。

### Security

- 起跑与确认使用**两个不同权限点**（能看设备 ≠ 能建单）；确认/驳回强制 `X-Agent-User`，身份进决策留痕。

## [0.2.0] - 2026-10-27

运行观测与故障诊断第一阶段（M1 / M2）。

### Added

- **运行观测三表** `agent_run` / `agent_step` / `agent_tool_call`：一次运行、每一轮调度、每次工具调用可逐轮回放；
  DDL 见 `docs/ddl/agent_run_step.sql`（含回滚），已在本地库执行。
- **Python 运行写入器** `app/obs/run_recorder.py`：失败只 WARN 不阻断业务、参数只存 sha256 摘要（不落 PII）、
  同轮次 UPSERT 幂等；启动扫描把遗留 `running` 收尾为 `interrupted`。
- **管理端时间线**：`GET /manage/agent/run/list|/{runId}|/{runId}/steps`（复用 `manage:agentAudit:*` 权限），
  前端页面 `views/manage/agentRun`，菜单 9010「运行时间线」。
- **故障诊断场景 M2**：`fault_state.py` + `fault_graph.py`（主管代码路由 + 诊断官），复用 `compose_diagnosis`
  与 6 个只读工具；**证据回溯** `trace_evidence()`——证据必须能指回本轮工具返回行，指不回去的丢弃，
  丢光则判不可下结论并禁止派单。

### Security

- 证据 `evidenceId → toolCallId` 强制落库，避免"看起来有依据"的结论。

## [0.1.0] - 2026-10-26

安全加固基线（P0）与维修工单业务例外。

### Security

- **移动端 `dkd-app` 鉴权链**：JWT 签名密钥从源码硬编码 `"dkd"` 改为 `DKD_APP_JWT_SECRET`（<32 字节拒绝启动）；
  拦截器改 fail-closed（无令牌/非法令牌一律 401）；验证码改 6 位随机 + 60 秒频控，短信未配置时 fail-closed；
  删除 `/direct-login`；`EmpHolder` 在 `afterCompletion` 清理，消除跨请求身份串号。
- **智能体网关**：取消 `@RequestMapping("/**")` 泛化代理，改显式路径白名单（白名单外 404）；
  补货读接口要求 `manage:task:list`、写动作要求 `manage:task:add`，判权先于转发。
- **Python 服务**：`/agent/**` 强制校验 `X-Agent-Secret`（回环来源除外，密钥未配置 503 fail-closed）。
- **前端**：AI 诊断输出改为"HTML 转义 → 换行 → DOMPurify（仅允许 `<br>`）"，堵存储型 XSS；
  移除"记住密码"的可逆口令 Cookie，退出统一清理记住登录信息。
- **部署面**：nginx 显式拒绝 `/prod-api/druid`、接口文档与 `/agent/callback`；swagger 默认关闭。

### Fixed

- **维修工单**：允许 `vm_status=2（停机）` 设备建维修单（运行中/停机放行，未投放/已撤机仍拒绝），
  同时修正维修分支误用的"请勿重复补货"文案与 `Long` 用 `==` 比较的隐患。

### Docs

- 新增 `docs/security-audit-2026-10-21.md`（19 项发现、误报复核、P0 修复记录）与
  `docs/security-credential-rotation.md` 的 dkd-app JWT / agent 服务密钥轮换补充。
