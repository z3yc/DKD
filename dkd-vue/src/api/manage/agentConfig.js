import request from '@/utils/request'

/**
 * 智能体配置台 API（排期任务 2-6 / 实现契约 §4、§6.2）
 *
 * 为什么这一层要包起来：配置是 Java 的业务事实（写操作必须经过 RuoYi 权限 +
 * 乐观锁 + 版本历史，见 docs/ddl/agent_config.sql 文件头），组件里直连 axios
 * 会绕过统一鉴权头、重复提交防护与错误信封解析（AGENTS §2.2）。
 *
 * ⚠️ 为什么写接口统一带 `silent: true`（关闭 request.js 拦截器的全局 toast）：
 *   配置台的三种失败必须分开处置，而拦截器只会“弹一条然后 reject”：
 *   ① 409 乐观锁冲突 → 必须用 ElMessageBox 明确告诉用户“别人刚改过”，
 *      并刷新详情；一闪而过的 toast 很容易被当成保存成功前的噪声；
 *   ② 字段校验失败（HTTP 200 + code:400，data 里带字段级映射）→ 页面要定位到表单；
 *   ③ 传输层失败 → 页面给统一兜底文案。
 *   若交给拦截器弹，页面再弹一次就是双提示；而 409 又恰恰是最需要用户停下来看的。
 *   注：request.js 的拦截器会丢弃 code:400 的 data 字段映射（reject 只带 msg），
 *   因此前端已按契约 §4.4 做同规则预校验，服务端 msg（首个错误）作为兜底展示。
 */

/**
 * 查询配置列表（分页）。
 *
 * @param {Object} query code / name / runtime / enabled / pageNum / pageSize
 * @returns TableDataInfo：{ total, rows }；rows 为 AgentConfigVo（camelCase）
 */
export function listAgentConfig(query) {
  return request({
    url: '/manage/agent/config/list',
    method: 'get',
    params: query
  })
}

/**
 * 查询单个配置详情（含完整 systemPrompt，列表 VO 可能不带全量大字段）。
 *
 * @param {number} id 配置主键
 */
export function getAgentConfig(id) {
  return request({
    url: '/manage/agent/config/' + id,
    method: 'get'
  })
}

/**
 * 查询载体元数据：运行载体清单、模型白名单、工具清单、占位符白名单、
 * 默认提示词、参数区间（paramSpecs）等。
 *
 * 为什么前端不硬编码这些：契约 §4.6 明确由后端下发，Java 常量镜像
 * `dkd-agent/app/agent_defaults.py`；前端再抄一份就是第三份定义，
 * 一旦漂移，会出现“页面允许保存、后端拒绝”的错位。
 *
 * @param {string} [runtime] 载体编码；传了才返回该载体的 paramSpecs/defaultPrompt
 */
export function getAgentConfigMeta(runtime) {
  return request({
    url: '/manage/agent/config/meta',
    method: 'get',
    params: runtime ? { runtime } : {}
  })
}

/**
 * 新增自定义智能体（Q1：本期只放开 chat 载体，runtime 固定 chat）。
 *
 * @param {Object} data AgentConfigDto（无 id/version；builtin 强制 0，scene 由后端分配）
 */
export function addAgentConfig(data) {
  return request({
    url: '/manage/agent/config',
    method: 'post',
    data,
    silent: true
  })
}

/**
 * 编辑配置（乐观锁：body 必须带当前 version，版本不一致后端返回 409）。
 *
 * 成功时 data = 更新后的 VO（含 +1 后的 version）。
 *
 * @param {number} id 配置主键
 * @param {Object} data AgentConfigDto（含 version / changeNote）
 */
export function updateAgentConfig(id, data) {
  return request({
    url: '/manage/agent/config/' + id,
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 启用 / 停用配置（S5）。
 *
 * body 带 version 走乐观锁；enabled=0 时后端要求 disabledReason 5~100 字。
 * 停用同样会 +1 版本并写 history（契约 §4.5）——所以提示里要给新版本号。
 *
 * @param {number} id 配置主键
 * @param {Object} data { enabled: 0|1, disabledReason: string|null, version: number }
 */
export function setAgentConfigEnabled(id, data) {
  return request({
    url: '/manage/agent/config/' + id + '/enabled',
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 查询版本历史（只增不改的快照列表）。
 *
 * @param {number} id 配置主键
 * @returns AjaxResult.data = [{ code, version, snapshot, changeNote, createBy, createTime }]
 *          snapshot 为 JSON 字符串（键名 snake_case，params_json 为嵌套对象）
 */
export function listAgentConfigHistory(id) {
  return request({
    url: '/manage/agent/config/' + id + '/history',
    method: 'get'
  })
}

/**
 * 回滚到某个历史版本。
 *
 * 语义是「把历史快照复制为**新版本**」（version = 当前版本 + 1），不是原地回退，
 * 因此历史链不会断，可以再滚回来。
 *
 * @param {number} id 配置主键
 * @param {Object} data { targetVersion: number, version: number, changeNote: string|null }
 *                      targetVersion = 要回滚到的历史版本；version = 当前版本（乐观锁）
 */
export function rollbackAgentConfig(id, data) {
  return request({
    url: '/manage/agent/config/' + id + '/rollback',
    method: 'post',
    data,
    silent: true
  })
}
