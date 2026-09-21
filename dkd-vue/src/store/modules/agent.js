/**
 * AI 助手抽屉的全局状态（排期任务 0-11）
 *
 * 为什么要独立 store：入口在导航栏（Navbar），面板挂在 layout 上（跨页面常驻），
 * 两者不是父子关系，用 store 通信比 provide/inject 或事件总线更符合本项目 Pinia 惯例。
 */
const useAgentStore = defineStore('agent', {
  state: () => ({
    // 抽屉是否展开
    drawerVisible: false,
    // 降级标记：agent.enabled=false 或 Python 服务不可达时为 true（方案 §3.3）
    degraded: false,
    // 降级原因（直接展示给用户，禁止伪造会话能力）
    degradedReason: ''
  }),
  actions: {
    open() {
      this.drawerVisible = true
    },
    close() {
      this.drawerVisible = false
    },
    toggle() {
      this.drawerVisible = !this.drawerVisible
    },
    setDegraded(degraded, reason) {
      this.degraded = !!degraded
      this.degradedReason = reason || ''
    }
  }
})

export default useAgentStore
