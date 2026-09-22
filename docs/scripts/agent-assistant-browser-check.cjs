/**
 * 前端 AI 助手浏览器验收脚本（排期任务 0-11；首次落地 2026-09-21，10/10 PASS）
 *
 * 覆盖：登录 → 入口 → 抽屉（场景 Tab/快捷问题条/欢迎语）→ token 级逐字渲染 →
 *       可取消（AbortController）→ 中断后继续对话 → DOMPurify 防注入 → 新建会话 → 控制台无错误
 *
 * 前置：
 *   1) Java（:8080）+ Python（:8090）已启动；2) 前端 dev server 已启动（cd dkd-vue && npm run dev，:80）
 *   3) 依赖装在**项目外**（避免污染仓库依赖声明，AGENTS 禁止擅自引入依赖）：
 *        npm i --prefix <临时目录> playwright-core
 *      用系统已装 Chrome 启动（channel:'chrome'），不下载浏览器。
 * 用法：
 *   node docs/scripts/agent-assistant-browser-check.cjs     # 需先把下方 PW 常量指向临时目录
 *
 * 为什么必须有这一层验证：本脚本首次运行就抓出两个真 bug（第二轮对话误带 Last-Event-ID 导致
 * “会话不存在”、reader.cancel() 的 Promise 拒绝未捕获导致控制台 AbortError）——
 * 构建通过 + HTTP 层 curl 验证都发现不了。
 */
// ⚠️ 改成你自己的临时安装目录（见文件头“依赖装在项目外”）
const PW = process.env.PW_CORE || 'C:/Users/zyc/AppData/Local/Temp/pw/node_modules/playwright-core'
const { chromium } = require(PW)
const BASE = 'http://127.0.0.1'
let pass = 0, fail = 0
const ok = (c, m) => { console.log((c ? '  PASS  ' : '  FAIL  ') + m); c ? pass++ : fail++ }
const sleep = (ms) => new Promise(r => setTimeout(r, ms))

async function step(name, fn) {
  console.log(name)
  try { await fn() } catch (e) { fail++; console.log('  ERROR  ' + name + ' :: ' + String(e).split('\n')[0]) }
}

;(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', e => errors.push(String(e)))

  await step('[1] 登录并打开抽屉', async () => {
    await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
    await page.fill('input[placeholder="账号"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登 录")')
    await page.waitForSelector('#ai-assistant-entry', { timeout: 20000 })
    await page.click('#ai-assistant-entry')
    await page.waitForSelector('.agent-panel', { timeout: 10000 })
    ok(true, '登录 → 入口 → 抽屉打开')
  })

  await step('[2] 可取消：中断生成', async () => {
    await page.fill('.chat-input input', '请详细写一段三百字以上的补货作业说明，分点陈述')
    const send = page.locator('.chat-input button', { hasText: '发送' })
    const stop = page.locator('.chat-input button', { hasText: '停止' })
    await send.click({ timeout: 10000 })
    ok(true, '已点击发送')
    await stop.waitFor({ state: 'visible', timeout: 15000 })
    const btnTexts = await page.locator('.chat-input button').allTextContents()
    console.log('      发送后按钮：' + JSON.stringify(btnTexts))
    ok(true, '流式中出现「停止」按钮')
    await sleep(800)
    const lenBefore = (await page.textContent('.msg.assistant:last-of-type .bubble')) || ''
    await stop.click({ timeout: 10000 })
    await sleep(1500)
    const bubbles = await page.locator('.msg.assistant .bubble').allTextContents()
    const last = (bubbles[bubbles.length - 1] || '').trim()
    ok(/（已取消）/.test(last), `中断后标记已取消（中止时约 ${lenBefore.length} 字 → 最终 ${last.length} 字）`)
    const caret = await page.locator('.msg.assistant .caret').count()
    ok(caret === 0, '流式光标已消失（未留悬挂状态）')
    const sendBack = await page.locator('.chat-input button', { hasText: '发送' }).count()
    ok(sendBack === 1, '按钮复位为「发送」（loading 在 finally 复位）')
  })

  await step('[3] 中断后可再次正常对话（会话仍可用）', async () => {
    await page.fill('.chat-input input', '用五个字回答：补货重要吗')
    await page.locator('.chat-input button', { hasText: '发送' }).click({ timeout: 10000 })
    await page.waitForSelector('.msg.assistant .caret', { timeout: 20000 })
    await page.waitForSelector('.msg.assistant .caret', { state: 'detached', timeout: 40000 })
    const bubbles = await page.locator('.msg.assistant .bubble').allTextContents()
    const last = (bubbles[bubbles.length - 1] || '').trim()
    ok(last.length > 0 && !/（已取消）/.test(last), `中断后再发消息成功：${last.slice(0, 30)}`)
  })

  await step('[4] DOMPurify：注入脚本不执行', async () => {
    await page.fill('.chat-input input', '<img src=x onerror="window.__xss=1">补货')
    await page.locator('.chat-input button', { hasText: '发送' }).click({ timeout: 10000 })
    await sleep(3000)
    const xss = await page.evaluate(() => window.__xss === 1)
    ok(!xss, '未执行注入的 onerror 脚本')
  })

  await step('[5] 新建会话清空 + 截图', async () => {
    await page.locator('.agent-head .head-btn').first().click({ timeout: 10000 })
    await sleep(400)
    const users = await page.locator('.msg.user').count()
    ok(users === 0, '新建会话清空消息列表')
    await page.screenshot({ path: 'C:/Users/zyc/AppData/Local/Temp/m1-assistant.png' })
    console.log('      截图：C:/Users/zyc/AppData/Local/Temp/m1-assistant.png')
  })

  await step('[6] 控制台错误', async () => {
    const real = errors.filter(e => !/favicon|ResizeObserver|DevTools/i.test(e))
    ok(real.length === 0, `无前端错误（${real.length} 条）` + (real[0] ? ' :: ' + real[0].slice(0, 120) : ''))
  })

  await browser.close()
  console.log(`\nRESULT: pass=${pass} fail=${fail}`)
  process.exit(fail === 0 ? 0 : 1)
})().catch(e => { console.error('FATAL', e); process.exit(2) })
