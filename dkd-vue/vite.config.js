import { defineConfig, loadEnv } from 'vite'
import path from 'path'
import createVitePlugins from './vite/plugins'

// https://vitejs.dev/config/
export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd())
  const { VITE_APP_ENV } = env
  return {
    // 部署生产环境和开发环境下的URL。
    // 默认情况下，vite 会假设你的应用是被部署在一个域名的根路径上
    // 例如 https://www.ruoyi.vip/。如果应用被部署在一个子路径上，你就需要用这个选项指定这个子路径。例如，如果你的应用被部署在 https://www.ruoyi.vip/admin/，则设置 baseUrl 为 /admin/。
    base: VITE_APP_ENV === 'production' ? '/' : '/',
    plugins: createVitePlugins(env, command === 'build'),
    resolve: {
      // https://cn.vitejs.dev/config/#resolve-alias
      alias: {
        // 设置路径
        '~': path.resolve(__dirname, './'),
        // 设置别名
        '@': path.resolve(__dirname, './src')
      },
      // https://cn.vitejs.dev/config/#resolve-extensions
      extensions: ['.mjs', '.js', '.ts', '.jsx', '.tsx', '.json', '.vue']
    },
    // vite 相关配置
    server: {
      port: 80,
      host: true,
      open: true,
      proxy: {
        // https://cn.vitejs.dev/config/#server-proxy
        '/dev-api': {
          target: 'http://127.0.0.1:8080',
          // target: 'https://api.wzs.pub/mock/13',
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/dev-api/, '')
        }
      }
    },
    //fix:error:stdin>:7356:1: warning: "@charset" must be the first rule in the file
    css: {
      postcss: {
        plugins: [
          {
            postcssPlugin: 'internal:charset-removal',
            AtRule: {
              charset: (atRule) => {
                if (atRule.name === 'charset') {
                  atRule.remove();
                }
              }
            }
          }
        ]
      }
    },
    // P1-17 构建产物瘦身：原先无 build 节点，全部 vendor 打进 entry，
    // 最大 chunk 1.6M（gzip 前）——首屏要下载的代码与路由级懒加载的页面代码
    // 混在一起，改一处业务代码也会让整个大 chunk 缓存失效。
    // 拆分原则：稳定不变的三方库各自成 chunk（浏览器长缓存），业务代码进 entry/页面 chunk。
    // 配合 vite-plugin-compression 产出 .gz，首屏只需 entry + vue-vendor + element-plus。
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            // element-plus 全量注册（main.js），与图标包强耦合，一起拆
            'element-plus': ['element-plus', '@element-plus/icons-vue'],
            // echarts 仅报表页懒加载使用，独立拆出后不进首屏
            'echarts': ['echarts'],
            // 框架核心：变更频率远低于业务代码，独立 chunk 享长缓存
            'vue-vendor': ['vue', 'vue-router', 'pinia'],
            // AI 助手侧边栏的 markdown 渲染链（marked + dompurify），仅聊天时需要
            'markdown': ['marked', 'dompurify']
          }
        }
      }
    }
  }
})
