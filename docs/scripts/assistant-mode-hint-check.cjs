/**
 * AI 助手「模式提示」浏览器验收（2026-10-01 修报障产出）。
 *
 * ## 这个脚本要防的是什么
 *
 * 报障原文：「AI 智能体对话有 bug，对话不返回数据」。实际现象是回复变成 `[echo] <你的问题>`。
 * 根因不是代码坏了，而是服务端跑在 `DKD_AGENT_USE_LLM=0`（Phase 0 的链路自检分支，
 * 见 `app/graphs/chat_graph.py::ECHO_PREFIX`）——而**前端完全没读 meta 帧里的 `mode`**，
 * 于是「配置没打开真模型」在用户眼里就是「智能体失灵」。
 *
 * 本脚本断言两件事（分别对应两种模式）：
 *   --expect echo : 必须出现**回显模式提示条**，并且条里写明「怎么启用」；
 *   --expect llm  : 不得出现回显提示，且应显示**服务端实际返回的模型名**。
 *
 * 用法：
 *   PW_CORE=<tmp>/node_modules/playwright-core node docs/scripts/assistant-mode-hint-check.cjs --expect llm
 *   PW_CORE=<tmp>/node_modules/playwright-core node docs/scripts/assistant-mode-hint-check.cjs --expect echo
 */
const PW = process.env.PW_CORE || 'C:/Users/zyc/AppData/Local/Temp/pw/node_modules/playwright-core'
const { chromium } = require(PW)

const BASE = 'http://127.0.0.1'
const EXPECT = (process.argv.find((a) => a.startsWith('--expect=')) || '--expect=llm').split('=')[1]

let pass = 0
let fail = 0
const ok = (c, m) => { console.log((c ? '  PASS  ' : '  FAIL  ') + m); c ? pass++ : fail++ }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1560, height: 980 } })
  const errors = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push(String(e)))

  console.log(`\n[期望模式] ${EXPECT}`)

  await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
  await page.fill('input[placeholder="账号"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button:has-text("登 录")')
  await page.waitForSelector('#ai-assistant-entry', { timeout: 25000 })
  await page.click('#ai-assistant-entry')
  await page.waitForSelector('.agent-panel', { timeout: 10000 })
  ok(true, '登录 → 打开 AI 助手抽屉')

  // 发一句（用报障里的原话，确保是同一个场景）
  await page.fill('.chat-input input', '先看紧急的')
  await page.locator('.chat-input button', { hasText: '发送' }).click({ timeout: 10000 })
  // 工具轮次占位（§十 任务 6）：助手调只读工具时中间那几秒**不产生 delta 帧**，
  // 若前端不显示占位，用户看到的就是一个空白气泡（无法区分“在查库”与“卡死”）。
  // 只在 llm 模式断言：echo 模式没有工具轮次，占位只一闪而过，断言会变成 flaky。
  const queryingSeen = await page
    .waitForSelector('.msg.assistant .querying', { timeout: 20000 })
    .then(() => true)
    .catch(() => false)
  // 等回复落地（流式结束：光标消失）
  await page.waitForSelector('.msg.assistant .caret', { state: 'detached', timeout: 60000 }).catch(() => {})
  await sleep(1200)

  const bubbles = await page.locator('.msg.assistant .bubble').allTextContents()
  const reply = (bubbles[bubbles.length - 1] || '').trim()
  console.log(`  → 回复：${reply.slice(0, 80)}`)

  const modeBarVisible = await page.locator('.mode-bar').isVisible().catch(() => false)
  const modelBadgeVisible = await page.locator('.model-badge').isVisible().catch(() => false)
  const modelText = modelBadgeVisible ? ((await page.textContent('.model-badge')) || '').trim() : ''

  if (EXPECT === 'echo') {
    ok(modeBarVisible, '出现「回显模式」提示条（不再让用户以为智能体坏了）')
    const bar = modeBarVisible ? (await page.textContent('.mode-bar')).replace(/\s+/g, ' ') : ''
    ok(/回显模式/.test(bar), `提示条写明是回显模式：${bar.slice(0, 60)}`)
    ok(/DKD_AGENT_USE_LLM/.test(bar), '提示条给出启用方式（直接指向配置项，而不是让人去猜）')
    ok(/\[echo\]/.test(reply), `回复确实带 [echo] 前缀（与该提示一致）：${reply.slice(0, 40)}`)
    ok(!modelBadgeVisible, 'echo 模式下不显示模型名（不虚构“用了哪个模型”）')
  } else {
    ok(!modeBarVisible, '不应出现回显模式提示（服务端已在真模型模式）')
    ok(modelBadgeVisible, '显示模型标识（证明确实调了模型）')
    ok(/模型：\S+/.test(modelText), `模型名按**服务端实际返回**显示：${modelText}`)
    ok(!/\[echo\]/.test(reply), `回复不再带 [echo] 前缀：${reply.slice(0, 50)}`)
    ok(reply.length > 0, '回复非空')
    ok(queryingSeen, '工具轮次期间出现「正在查询数据…」占位（气泡不再空白几秒）')
  }

  await page.screenshot({ path: `f:/tmp/assistant-mode-${EXPECT}.png`, fullPage: false })
  const real = errors.filter((e) => !/favicon|ResizeObserver/i.test(e))
  ok(real.length === 0, real.length ? `控制台错误：${real.slice(0, 2).join(' | ')}` : '控制台无错误')

  await browser.close()
  console.log(`\n结果：PASS=${pass} FAIL=${fail}`)
  process.exit(fail ? 1 : 0)
})()
