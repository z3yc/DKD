import request from '@/utils/request'

/**
 * 智能体工具中心 API（排期任务 3-4）。
 *
 * 只读清单 + **受限写**：后端只允许改 `enabled / timeoutMs / rowLimit` 三项。
 * 为什么 code/readOnly 不能改：
 *  - `code` 必须能映射到代码里的工具名（启动校验），改了等于让注册表指向不存在的工具；
 *  - `readOnly` 是安全边界：把写工具改成「只读」就能被模型直接调用，等于绕过
 *    AGENTS §7.9 的「写操作必须人工确认 + 回调 Java」红线。页面不开这两个入口是刻意的，
 *    不是「还没做」。
 *
 * 写接口统一带 `silent: true`（关闭 request.js 拦截器的全局 toast）：
 * 保存失败的字段级原因需要在弹窗里**持久**展示，一闪而过的 toast 会被当成保存成功前的噪声。
 */

/**
 * 查询工具注册表全量清单（工具数量很少，后端不分页）。
 *
 * @returns AjaxResult：data = [{ id, code, name, description, scope, source,
 *            readOnly, enabled, timeoutMs, rowLimit, builtin }]
 */
export function listAgentTools() {
  return request({
    url: '/manage/agent/tool/list',
    method: 'get'
  })
}

/**
 * 修改工具的启停 / 超时 / 行数上限（只有这三项）。
 *
 * 带齐三项而不是只传改动的那个：后端 DTO 若把缺省当「置空」，只传一半会把另一项
 * 悄悄写成 null（工具级护栏失效是最难发现的一类退化）。
 *
 * @param {number} id 工具主键
 * @param {Object} data { enabled: 0|1, timeoutMs: number, rowLimit: number }
 */
export function updateAgentTool(id, data) {
  return request({
    url: '/manage/agent/tool/' + id,
    method: 'put',
    data,
    silent: true
  })
}
