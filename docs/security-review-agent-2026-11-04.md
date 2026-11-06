# Agent 安全复核报告（排期 6-6）

> 生成方式：`python docs/scripts/security-review-scan.py --out docs/agent-eval-set/reports/security-review.json`
> 扫描时间：2026-11-04 · 结论：**PASS**（无高危密钥/越权；2 处测试夹具假 key 为低危并已标注）

## 1. 扫描证据

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 仓库明文密钥 | 0 高危 / 2 低危（测试夹具） | `security-review.json` → `repository.findings`；低危为 Service 单测里刻意构造的 `sk-abcdefghijklmnopqrstuvwxyz....`，用于验证“密钥形态值必须被拒绝”，不在配置/运行路径 |
| `.env` 入仓 | 已忽略 | `git check-ignore .env` 返回 0 |
| `agent_llm_profile` 密钥列 | 无 | 列清单只有 `api_key_env`（环境变量名），无 `api_key/secret/token` 值列 |
| 日志/留痕泄漏 `sk-` | 0 | `agent_log`/`agent_message`/`agent_decision_log` 三表 `LIKE '%sk-%'` 均为 0 |
| `dkd_agent` DELETE 权限 | 无 | `SHOW GRANTS` 全量见 JSON；业务表仅 SELECT，`agent_*` 无 DELETE（符合 AGENTS §7.3/§7.4） |
| 业务表写权限 | 无 | GRANTS 中 `tb_*` 均为 SELECT；仅 `agent_*` 有 INSERT/UPDATE |
| MCP 配置密钥形态 | 无违规 | 扫描时无 server 配置；脚本校验 `env_keys_json` 只能为 ENV 名、transport 仅 http、endpoint 必须 http(s) |
| 权限点 | 菜单 9001~9040 已按页面/按钮拆分（含 9020~9034 新页） | `sys_menu.perms` 与 Controller `@PreAuthorize` 同名，见各 DDL 执行记录 |

## 2. 复核结论与剩余风险

**通过项**

- 密钥只存环境变量名：模型档案、MCP、知识库接入均未把密钥写入 DB/日志/留痕；
- Python 账号最小化：业务表只读、`agent_*` 无 DELETE；`agent_tool/agent_mcp_server` 的写权限为
  3-7 同步/状态回写所需，已在 `docs/ddl/create_agent_db_user.sql` 注释风险；
- 出网面收敛：MCP 仅启用 server 参与探测/加载；LLM 走配置 base_url；MCP 客户端显式 `trust_env=False`。

**仍待外部/人工（不伪造为通过）**

1. **凭据轮换**：按 `docs/security-credential-rotation.md`，OSS AK/SK、MySQL/Redis 服务端口令、
   JWT secret、Druid 口令仍待生产窗口轮换（本机已外置，但历史版本曾出现明文，轮换属必须项）🧑。
2. **真实 MCP server 出网白名单**：清单未提供，当前只有本地 mock 验收；生产前需按实际 endpoint
   做网络白名单与只读工具复核 🧑。
3. **权限点矩阵复查**：菜单 9026~9035 为新增页，需在生产角色上逐一点选验证（管理员默认可见，
   其他角色需授权）🧑；本机已执行 `sys_menu` 插入并校验 4~5 行。
4. **列级授权收口**：`agent_tool`/`agent_mcp_server` 的写权限是 MySQL 表级授权（无列级/行级），
   代码只更新约定列；若安全要求更严，应改为 Java 回调写入（评审项，非本批阻塞）。

## 3. 复跑方式

```powershell
$env:DKD_CLEANUP_PASSWORD='***'   # 只读巡检账号即可
python docs/scripts/security-review-scan.py --out docs/agent-eval-set/reports/security-review.json
# verdict=PASS 退出码 0；有高危发现退出码 1（不要把 1 当“扫描失败”）
```

扫描只读，不修改任何文件/数据；`security-review.json` 保留为本次证据。
