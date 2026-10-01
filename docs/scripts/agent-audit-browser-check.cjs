/**
 * 决策审计页浏览器验收脚本（排期任务 3-7）。
 *
 * 覆盖：登录 → 「AI 智能体 / 决策审计」菜单 → 只读提示条 → 列表渲染 →
 *       过滤（场景/结果）→ 分页 → 详情回放（JSON 上下文）→ 无写操作按钮 → 控制台无错误
 *
 * 前置：Java :8080（含 AgentDecisionLogController）+ 前端 dev server :80；
 *       菜单已执行（docs/ddl/agent_audit_menu.sql）。
 *       依赖装在项目外：npm i --prefix <临时目录> playwright-core
 *
 * 用法：
 *   PW_CORE=<临时目录>/node_modules/playwright-core node docs/scripts/agent-audit-browser-check.cjs
 *
 * 为什么必须有一层浏览器验证：审计页的价值全在“看得见、查得到”上——
 * 接口返回 200 只能说明后端通了，不能说明**筛选真的生效、JSON 真的能读、
 * 有没有误加写操作入口**。后者尤其重要：AGENTS §6.3 的审计证据一旦能在后台被改，
 * 它就不再是证据了（所以本脚本专门断言“没有写操作按钮”）。
 */
const PW = process.env.PW_CORE || 'C:/Users/zyc/AppData/Local/Temp/pw/node_modules/playwright-core'
const path = require('path')
const os = require('os')
const { chromium } = require(PW)

const BASE = 'http://127.0.0.1'
const SHOT_DIR = process.env.SHOT_DIR || os.tmpdir()
let pass = 0
let fail = 0
const ok = (c, m) => { console.log((c ? '  PASS  ' : '  FAIL  ') + m); c ? pass++ : fail++ }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function step(name, fn) {
  console.log(name)
  try { await fn() } catch (e) { fail++; console.log('  ERROR  ' + name + ' :: ' + String(e).split('\n')[0]) }
}

;(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
  const errors = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push(String(e)))

  await step('[1] 登录 → AI 智能体 → 决策审计（菜单与动态路由）', async () => {
    await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
    await page.fill('input[placeholder="账号"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登 录")')
    await page.waitForSelector('.app-wrapper', { timeout: 25000 })

    await page.click('text=AI 智能体', { timeout: 15000 })
    await page.click('text=决策审计', { timeout: 15000 })
    await page.waitForSelector('.agent-audit', { timeout: 20000 })
    ok(true, '菜单「AI 智能体 / 决策审计」可进入，页面挂载成功')
  })

  await step('[2] 只读提示 + 无写操作入口（AGENTS §6.3 的硬约束）', async () => {
    const alert = await page.textContent('.agent-audit .el-alert')
    ok(/只读/.test(alert), `页面明示只读：${alert.replace(/\s+/g, ' ').trim().slice(0, 50)}`)

    // 关键断言：审计页不得出现任何写操作入口。
    // 不用正则去猜 HTML，直接枚举页面上的按钮文字——这是人眼看得到的那个集合。
    const buttons = await page.locator('.agent-audit button').allTextContents()
    const writeWords = ['新增', '修改', '删除', '导入', '导出']
    const offenders = buttons
      .map((t) => t.replace(/\s+/g, ''))
      .filter((t) => writeWords.some((w) => t.includes(w)))
    ok(offenders.length === 0, `没有写操作按钮（实际按钮：${JSON.stringify(buttons.map((t) => t.replace(/\s+/g, '')).filter(Boolean).slice(0, 8))}）`)
  })

  await step('[3] 列表渲染真实留痕', async () => {
    const rows = await page.locator('.el-table__body-wrapper tbody tr.el-table__row').count()
    ok(rows === 10, `默认分页 10 行，实际 ${rows}`)
    const total = await page.textContent('.el-pagination__total').catch(() => '')
    ok(/共\s*\d+\s*条/.test(total), `分页总数显示：${(total || '').trim()}`)

    const text = await page.textContent('.agent-audit')
    ok(/补货|人工干预|用户会话/.test(text), '场景/触发方式渲染为中文标签（不是裸数字）')
    ok(/成功|失败|被拒绝/.test(text), '结果渲染为中文标签')
  })

  await step('[4] 过滤真的生效（场景=补货）', async () => {
    const before = await page.textContent('.el-pagination__total')
    // 打开「场景」下拉选「补货」
    await page.locator('.agent-audit .el-form-item', { hasText: '场景' }).locator('input').first().click()
    await sleep(500)
    await page.locator('.el-select-dropdown__item', { hasText: '补货' }).first().click()
    await sleep(300)
    await page.click('button:has-text("搜索")')
    await sleep(1500)
    const after = await page.textContent('.el-pagination__total')
    ok(before !== after, `筛选后总数变化：${before.replace(/\s+/g, '')} → ${after.replace(/\s+/g, '')}`)
    const text = await page.textContent('.agent-audit')
    ok(!/通用问答|运营分析/.test(text), '筛选后列表内不再出现其它场景的行')
  })

  await step('[5] 详情/回放：JSON 上下文可读', async () => {
    await page.locator('button:has-text("详情")').first().click({ timeout: 10000 })
    await page.waitForSelector('.json-block', { timeout: 10000 })
    const body = await page.textContent('.el-dialog')
    ok(/输入上下文/.test(body), '详情包含「输入上下文」区块')
    ok(/llm_output/.test(body), '详情包含 LLM 输出区块')
    const jsonText = await page.textContent('.json-block__body')
    ok(jsonText.trim().length > 5, `JSON 上下文已渲染：${jsonText.replace(/\s+/g, ' ').trim().slice(0, 60)}`)
    // 美化过的 JSON 应带缩进（说明走的是 JSON.parse + stringify，而不是裸文本）
    ok(/\n\s{2,}/.test(jsonText), 'JSON 已格式化缩进（便于人读，不是一坨）')

    await page.screenshot({ path: path.join(SHOT_DIR, 'agent-audit-detail.png'), fullPage: false })
    await page.locator('.el-dialog__footer button:has-text("关闭")').click({ timeout: 10000 })
    await sleep(400)
  })

  await step('[6] 链路：按 X-Request-Id 串联同一次请求的全部留痕（升序）', async () => {
    // 先回收列表：第 4 步筛成了「补货」，这里重置回全量，保证第一行有 requestId 可点
    await page.click('button:has-text("重置")')
    await sleep(1500)

    // 取第一行的追踪ID（列在表格里以 `.mono` 渲染）与它的「链路」按钮
    const firstRow = page.locator('.el-table__body-wrapper tbody tr.el-table__row').first()
    const rid = ((await firstRow.locator('.mono').first().textContent()) || '').trim()
    ok(rid.length > 0 && rid !== '—', `第一行有可追溯的追踪ID：${rid}`)

    await firstRow.locator('button:has-text("链路")').click({ timeout: 10000 })
    await page.waitForSelector('.trace', { timeout: 10000 })
    // ⚠️ 必须限定 `:visible`：Element Plus 关闭弹窗后 **DOM 仍保留**
    // （排期 §十 10.6 坑 4），`.el-dialog__header` 会先命中已关闭的「详情」弹窗，
    // 于是断言拿到的是“决策详情（审计回放）”这个标题（本脚本第一版就是这么红的）。
    const title = (
      (await page.locator('.el-dialog:visible .el-dialog__header').last().textContent()) || ''
    ).trim()
    ok(title.includes(rid), `链路弹窗标题带上本次请求号：${title.slice(0, 60)}`)

    const items = await page.locator('.trace .el-timeline-item').count()
    ok(items >= 1, `链路至少渲染 1 条留痕（实际 ${items} 条）`)
    const traceText = ((await page.textContent('.trace')) || '').replace(/\s+/g, ' ')
    ok(/chat\.respond|restock\./.test(traceText), `链路里能看到动作名：${traceText.slice(0, 80)}`)
    ok(/输入上下文|\{|\[/.test(traceText), '链路条目带可读的输入上下文')

    await page.screenshot({ path: path.join(SHOT_DIR, 'agent-audit-trace.png'), fullPage: false })
    console.log('      截图：' + path.join(SHOT_DIR, 'agent-audit-trace.png'))
    await page.locator('.el-dialog__footer button:has-text("关闭")').last().click({ timeout: 10000 })
    await sleep(400)
  })

  await step('[7] 控制台无错误 + 截图留证', async () => {
    await page.screenshot({ path: path.join(SHOT_DIR, 'agent-audit.png'), fullPage: true })
    console.log('      截图：' + path.join(SHOT_DIR, 'agent-audit.png'))
    const real = errors.filter((e) => !/favicon|ResizeObserver/i.test(e))
    ok(real.length === 0, real.length ? `控制台错误 ${real.length} 条：${real.slice(0, 2).join(' | ')}` : '控制台无错误')
  })

  await browser.close()
  console.log(`\n结果：PASS=${pass} FAIL=${fail}`)
  process.exit(fail ? 1 : 0)
})()
