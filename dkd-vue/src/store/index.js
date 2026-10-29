// 显式 import 不能省（P2-27）：RuoYi 模板靠 vite/plugins/auto-import.js 的 pinia preset
// 做隐式注入，而 auto-import 只在 Vite 插件上下文生效。本文件会被 main.js 直接导入，
// 将来也可能被非 Vite 工具（脚本 / 单测 / Node 侧导入）引用——那时隐式注入不存在，
// createPinia 会直接 ReferenceError。这里写明来源，与运行环境无关。
import { createPinia } from 'pinia'

const store = createPinia()

export default store
