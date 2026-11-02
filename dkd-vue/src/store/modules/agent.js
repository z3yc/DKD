/**
 * AI 助手抽屉的全局状态（排期任务 0-11）
 *
 * 为什么要独立 store：入口在导航栏（Navbar），面板挂在 layout 上（跨页面常驻），
 * 两者不是父子关系，用 store 通信比 provide/inject 或事件总线更符合本项目 Pinia 惯例。
 *
 * 2-6：设备页/点位页的「AI 诊断」按钮不再直接开旧弹窗，而是带着**预置问题**打开本抽屉
 * （scene=3 诊断场景）；旧单轮入口保留为降级兜底（2-8）。
 */
const useAgentStore = defineStore('agent', {
  state: () => ({
    // 抽屉是否展开
    drawerVisible: false,
    // 降级标记：agent.enabled=false 或 Python 服务不可达时为 true（方案 §3.3）
    degraded: false,
    // 降级原因（直接展示给用户，禁止伪造会话能力）
    degradedReason: '',
    // 2-6 入口透传：打开抽屉时要自动发起的场景与问题（消费后立即清空）
    pendingScene: null,
    pendingPrompt: ''
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
    /**
     * 带预置问题打开助手（设备/点位页的「AI 诊断」入口用）。
     *
     * @param {{ scene?: number, prompt?: string }} payload
     */
    openWith(payload) {
      this.pendingScene = (payload && payload.scene) || null
      this.pendingPrompt = (payload && payload.prompt) || ''
      this.drawerVisible = true
    },
    /** 取出并清空预置问题（抽屉打开后只消费一次，避免下次打开重复发送） */
    consumePending() {
      const pending = { scene: this.pendingScene, prompt: this.pendingPrompt }
      this.pendingScene = null
      this.pendingPrompt = ''
      return pending
    },
    setDegraded(degraded, reason) {
      this.degraded = !!degraded
      this.degradedReason = reason || ''
    }
  }
})

export default useAgentStore
