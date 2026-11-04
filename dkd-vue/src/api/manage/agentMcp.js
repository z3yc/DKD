import request from '@/utils/request'

/**
 * 智能体 MCP 接入 API（排期任务 3-7，页面 S12）。
 *
 * 安全边界（AGENTS §7 + 缺口文档 §四）：
 *  - **只接只读工具**：只有 `read_only=1` 的工具允许同步进模型工具清单；写工具与
 *    「缺少只读标记」一律拒绝（写操作仍只能人工确认后回调 Java，§7.9）。拦截在 Python 接入层做，
 *    页面负责把结论（rejected 数）如实展示出来——不展示就等于让“被拒”看起来像“没同步”。
 *  - **默认停用**：新 server `enabled=0`，必须人工完成安全复核（出网白名单/超时/行数）后才启用。
 *  - **密钥只存环境变量名**：`envKeys` 是 `{HTTP头名: 环境变量名}` 的映射，密钥值在部署环境，
 *    本层没有任何密钥读写接口，页面也没有密钥输入框。
 *
 * 写接口（新增/编辑/启停/探测/同步）统一 `silent: true`：失败原因要持久展示在抽屉/弹窗里，
 * 交给拦截器只会闪一条 toast。
 */

/**
 * 查询 MCP server 列表。
 *
 * @returns AjaxResult：data = rows（id/code/name/transport/endpoint/envKeys/enabled/
 *          timeoutMs/rowLimit/readOnlyOnly/status/lastProbeTime/lastProbeMsg）
 */
export function listAgentMcpServers() {
  return request({
    url: '/manage/agent/mcp/list',
    method: 'get'
  })
}

/**
 * 新增 MCP server（默认 enabled=0，需安全复核后再开启）。
 *
 * @param {Object} data { code, name, transport, endpoint, timeoutMs, rowLimit, envKeys, enabled }
 */
export function addAgentMcpServer(data) {
  return request({
    url: '/manage/agent/mcp',
    method: 'post',
    data,
    silent: true
  })
}

/**
 * 编辑 MCP server（code 创建后不可改）。
 *
 * @param {number} id server 主键
 * @param {Object} data 同新增字段（transport 本期固定 http）
 */
export function updateAgentMcpServer(id, data) {
  return request({
    url: '/manage/agent/mcp/' + id,
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 查询单个 server 详情。
 *
 * 页面编辑优先用列表行（列表已返回全部可编辑字段），此接口留给“列表过期后取最新”的场景，
 * 避免为了打开抽屉多一次请求、也多一个权限面（GET /{id} 在菜单里没有独立按钮权限）。
 *
 * @param {number} id server 主键
 */
export function getAgentMcpServer(id) {
  return request({
    url: '/manage/agent/mcp/' + id,
    method: 'get'
  })
}

/**
 * 启用 / 停用 server。
 *
 * @param {number} id server 主键
 * @param {Object} data { enabled: 0|1 }
 */
export function setAgentMcpServerEnabled(id, data) {
  return request({
    url: '/manage/agent/mcp/' + id + '/enabled',
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 探测 server（Python 侧真正连一次 MCP server，并**自动同步只读工具**）。
 *
 * 返回里的 tools 是探测到的只读工具（name/description/inputSchema），rejectedCount 是
 * 因为写工具/缺只读标记被拒绝的数量——两者都要展示：只显示“成功”会掩盖越权工具的拒绝事实。
 *
 * @param {number} id server 主键
 * @returns AjaxResult：data = { ok, latencyMs, tools:[{name,description,inputSchema}],
 *          rejectedCount, errorType, errorMsg }
 */
export function probeAgentMcpServer(id) {
  return request({
    url: '/manage/agent/mcp/' + id + '/probe',
    method: 'post',
    silent: true
  })
}

/**
 * 同步工具清单（探测成功后也可手动再同步）：
 * synced=新增/更新、rejected=写工具/缺只读标记被拒、disabled=server 上已消失的工具被标记停用（不删除）。
 *
 * @param {number} id server 主键
 * @returns AjaxResult：data = { synced, rejected, disabled }
 */
export function syncAgentMcpServer(id) {
  return request({
    url: '/manage/agent/mcp/' + id + '/sync',
    method: 'post',
    silent: true
  })
}

/**
 * 查询某 server 已同步的工具（只读工具才在清单里）。
 *
 * @param {number} id server 主键
 * @returns AjaxResult：data = rows（code/name/readOnly/enabled/description）
 */
export function listAgentMcpTools(id) {
  return request({
    url: '/manage/agent/mcp/' + id + '/tools',
    method: 'get'
  })
}
