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
let blocked = 0
const ok = (c, m) => { console.log((c ? '  PASS  ' : '  FAIL  ') + m); c ? pass++ : fail++ }
/**
 * 「未验到」：与失败分开打印（AGENTS §10）——本脚本默认只验“当天前 10 条留痕”，
 * 若这 10 条里没有 chat.respond，那“长回复换行”这条就是“没东西可验”，不是缺陷。
 */
const skipAs = (m) => { console.log('  BLOCKED  ' + m); blocked++ }

/**
 * 当前**可见**弹窗的快照（文本 + 每个 JSON 块的文本/计算样式/几何量）。
 *
 * 为什么所有取值都必须经过它：el-dialog 关闭后 DOM 仍在（destroy-on-close=false），
 * 而本页同时挂着「详情」「链路」两个弹窗。用全局选择器
 * （`page.textContent('.el-dialog')`、`$$eval('.json-block__body')`）会取到隐藏或正在淡出的
 * 那一份——它的 innerText 是空串，于是断言以“块行数 [1,1]”这类看不懂的形式**随机**失败。
 * 写这一版时反复踩到，最终确定“单一数据源 = 可见弹窗”。
 */
;(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })

  async function dialogSnapshot() {
    return page.evaluate(() => {
      const dlg = [...document.querySelectorAll('.el-dialog')].find((d) => d.offsetParent !== null)
      if (!dlg) return { found: false, text: '', blocks: [] }
      const blocks = [...dlg.querySelectorAll('.json-block__body')].map((el) => {
        const c = getComputedStyle(el)
        const text = el.innerText
        return {
          text,
          lines: text ? text.split('\n').length : 0,
          whiteSpace: c.whiteSpace,
          maxHeight: c.maxHeight,
          overflow: el.scrollWidth - el.clientWidth
        }
      })
      return { found: true, text: dlg.innerText, blocks }
    })
  }

  /** 等“可见弹窗已经有内容”（可选：再等它里面出现某个关键词）——避免量到淡出中的旧弹窗 */
  async function waitVisibleDialog(contains) {
    await page.waitForFunction(
      (needle) => {
        const dlg = [...document.querySelectorAll('.el-dialog')].find((d) => d.offsetParent !== null)
        if (!dlg || !dlg.querySelector('.json-block')) return false
        return !needle || dlg.innerText.includes(needle)
      },
      contains || null,
      { timeout: 10000 }
    )
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

  async function step(name, fn) {
    console.log(name)
    try { await fn() } catch (e) { fail++; console.log('  ERROR  ' + name + ' :: ' + String(e).split('\n')[0]) }
  }

  const errors = []
  // warning 单独收集（2026-10-02 加）：Vue 的 prop 校验失败是 warning 不是 error，
  // 只查 error 会漏掉（实测 `el-tag type="primary"` 就是这么漏过去的）。
  const warnings = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
    else if (m.type() === 'warning') warnings.push(m.text())
  })
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
    await waitVisibleDialog()
    const first = await dialogSnapshot()
    ok(/输入上下文/.test(first.text), '详情包含「输入上下文」区块')
    ok(/llm_output/.test(first.text), '详情包含 LLM 输出区块')
    const jsonText = first.blocks[0] ? first.blocks[0].text : ''
    ok(jsonText.trim().length > 5, `JSON 上下文已渲染：${jsonText.replace(/\s+/g, ' ').trim().slice(0, 60)}`)
    // 美化过的 JSON 应带缩进（说明走的是 JSON.parse + stringify，而不是裸文本）
    ok(/\n\s{2,}/.test(jsonText), 'JSON 已格式化缩进（便于人读，不是一坨）')

    // ---- 弹窗里的样式真的生效了吗（2026-10-03 回来的回归，两类根因都能抓到）----
    // 背景：详情弹窗带 `append-to-body`，el-dialog 会把内容 **teleport 到 <body>**。
    // 当时本页把 `.json-block*` 的样式裹在页面根类 `.agent-audit` 下，编译出的选择器是
    // `.agent-audit .json-block__body[data-v-*]` → 祖先不在路径上 → 一律不匹配：
    // `white-space: pre-wrap` / `max-height` / `overflow` 全部失效，长文本变成一条
    // 6447px 的宽行（实测），既看不到换行也读不下去。
    // 这类“样式静默失效”在纯文本断言里**看不见**（文字内容一样），所以必须查计算样式
    // 与几何量：只断言“有文字”是抓不到它的。
    // ⚠️ 必须**限定到可见弹窗**：el-dialog 关闭后 DOM 仍在（destroy-on-close=false），
    // 直接 `document.querySelectorAll('.json-block__body')` 会把已关闭弹窗里那些
    // `display:none` 的块也量进来——它们的 `innerText` 是空串（行数恒为 1），
    // 断言就会在“上一个弹窗还挂着”时莫名其妙地失败（写这条时真的踩到了）。
    const styles = first.blocks
    ok(
      styles.every((s) => s.whiteSpace === 'pre-wrap'),
      `弹窗内 <pre> 的 white-space 生效（teleport 不得切断 scoped 样式）：${JSON.stringify(styles.map((s) => s.whiteSpace))}`
    )
    ok(
      styles.every((s) => s.maxHeight !== 'none'),
      `max-height 生效（长文本有独立滚动区）：${JSON.stringify(styles.map((s) => s.maxHeight))}`
    )
    ok(
      styles.every((s) => s.overflow <= 1),
      `无横向溢出（长文本会换行）：scrollWidth-clientWidth = ${JSON.stringify(styles.map((s) => s.overflow))}`
    )

    // ---- 长回复的换行还原（llm_output.reply 是 JSON 字符串，`\\n` 是转义）----
    // 只在真存在多行回复时才要求：详情弹窗打开的是**第一行**，可能是补货/分析类留痕（llm_output 为空）。
    const chatRow = page.locator('tr', { hasText: 'chat.respond' }).first()
    if ((await chatRow.count()) > 0) {
      await page.locator('.el-dialog__footer button:has-text("关闭")').click({ timeout: 10000 })
      // ⚠️ 必须等**真的关掉**再开下一个：el-dialog 关闭后 DOM 仍在，而 `waitForSelector('.json-block')`
      // 会被旧 DOM 立刻满足——于是下面量到的是"正在淡出"的那个弹窗（innerText 已为空，
      // 行数恒为 1），断言会以“块行数 [1,1]”这种看不懂的形式失败（写这条时真的踩到了）。
      await page.waitForFunction(
        () => ![...document.querySelectorAll('.el-dialog')].some((d) => d.offsetParent !== null),
        null,
        { timeout: 10000 }
      )
      await chatRow.locator('button:has-text("详情")').click({ timeout: 10000 })
      await waitVisibleDialog('chat.respond')
      const chat = await dialogSnapshot()
      // 真换行还原后：回复正文块的行数应明显 >1，且开头不是 `{`（那是 JSON 原文块的特征）
      ok(
        chat.blocks.some((b) => b.lines >= 3 && !b.text.trim().startsWith('{')),
        // 失败信息带上每块的首行片段：否则“块行数 [1,1]”这种报错根本看不出是哪个环节
        `多行回复已还原换行（否则整段回复就是一行）：块行数 ${JSON.stringify(chat.blocks.map((b) => b.lines))}` +
          `；内容首行 ${JSON.stringify(chat.blocks.map((b) => b.text.trim().slice(0, 24)))}` +
          `；弹窗标题片段 ${JSON.stringify(chat.text.replace(/\s+/g, ' ').trim().slice(0, 60))}`
      )
    } else {
      skipAs('当前页没有 chat.respond 留痕，长回复换行未验到')
    }

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
    const realWarn = warnings.filter((w) => !/favicon|ResizeObserver/i.test(w))
    ok(realWarn.length === 0, realWarn.length ? `控制台警告 ${realWarn.length} 条：${realWarn.slice(0, 2).join(' | ')}` : '控制台无警告')
  })

  await browser.close()
  console.log(`\n结果：PASS=${pass} FAIL=${fail} BLOCKED=${blocked}`)
  // 退出码约定（AGENTS §10）：0=全绿 / 1=有失败 / 2=有未验到；有 FAIL 时优先报 1
  process.exit(fail ? 1 : blocked ? 2 : 0)
})()
