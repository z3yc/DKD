/**
 * 会话过期的统一处置（登录状态失效时的**唯一实现**）。
 *
 * ## 为什么需要单独一份
 *
 * RuoYi 主链路（`utils/request.js` 的 axios 响应拦截器）本来内置了「提示 + 重新登录」；
 * 但本项目有三处**刻意绕开 axios** 的链路，而它们绕开的正是这段处置：
 *
 * | 链路 | 绕开 axios 的理由 | 原来的会话过期表现 |
 * | --- | --- | --- |
 * | `api/manage/restock.js` | 后端把「业务拒绝」表达为真实 HTTP 状态码（400/409/422/502），axios 拦截器会**丢掉 `msg`** 并改写成「系统接口 409 异常」 | 只标了 `unauthorized: true`，**没有任何视图消费** → 用户看到 RuoYi 原文「请求访问：/agent/restock/plans/97/adjust，认证失败，无法访问系统资源」 |
 * | `api/manage/agent.js`（探活） | 「智能体未启用」是预期状态，不该弹全局错误 | 抽屉横幅文案正确（"登录状态已过期…"），但**没有重新登录入口** |
 * | `utils/sse.js`（对话流） | 需要逐帧读取 + 可取消 | 错误气泡里是 RuoYi 原文，同样没有入口 |
 *
 * 2026-10-03 实测报障即此形态：运营看到一句框架级 401 文案，**既不知道是自己登录过期，
 * 也不知道该做什么**（原话："接口报错"）。所以这里把处置抽成一份——写成两份必然漂移，
 * 而"过期了却没人提示"只在会话真的过期时才暴露，是最难被发现的一类缺口。
 *
 * ## 与 `store/modules/user` 的循环依赖
 *
 * `store/modules/user` → `utils/request` → 本文件；若在模块顶层 import 用户 store 就成环。
 * 故在**用户确认重新登录时**才动态 `import()`（与 `utils/request.js` 的既有行为等价，
 * 只是把时机推迟到点击之后）。
 */
import { ElMessageBox } from 'element-plus'

/**
 * 是否已弹出重新登录框。
 *
 * 与 `utils/request.js` 原语义一致：并发请求同时 401 时只弹一个，不叠窗。
 * `permission.js` 也读写这个标记（页面级鉴权），故从本模块统一导出。
 */
export const isRelogin = { show: false }

/** 用户可见文案：不出现路由、框架名或 HTTP 术语，直接给下一步动作 */
export const SESSION_EXPIRED_MSG = '登录状态已过期'

/**
 * 弹出「重新登录」确认框；确认后清会话并回到登录页。
 *
 * 幂等且**不阻塞调用方**（不返回 Promise）：调用点都在错误分支里，
 * 让它们继续按各自的错误形状返回（气泡/横幅/toast），UI 不会被弹窗卡住。
 */
export function handleSessionExpired() {
  if (isRelogin.show) return
  isRelogin.show = true
  ElMessageBox.confirm(
    SESSION_EXPIRED_MSG + '，您可以继续留在该页面，或者重新登录',
    '系统提示',
    { confirmButtonText: '重新登录', cancelButtonText: '取消', type: 'warning' }
  )
    .then(async () => {
      isRelogin.show = false
      const { default: useUserStore } = await import('@/store/modules/user')
      await useUserStore().logOut()
      location.href = '/index'
    })
    .catch(() => {
      // 用户选择「继续留在该页面」：只复位标记，不改动页面状态
      isRelogin.show = false
    })
}
