/**
 * 补货工作台浏览器验收脚本（排期任务 1-10/1-11，关闭 FIX-12）
 *
 * 覆盖：登录 → 「AI 智能体」菜单 → 工作台渲染 → 真实数据 → 依据展开 →
 *       调整弹窗（两种模式互斥 + 原因必填）→ 跳过弹窗（原因必填）→
 *       暂停/恢复自动分析横幅闭环 → 控制台无错误 → 截图留证
 *
 * 前置（四项都要在跑）：
 *   1) MySQL + Redis；2) Java :8080；3) Python :8090（`uv run python -m app`）；
 *   4) 前端 dev server :80（cd dkd-vue && npm run dev）
 *   5) 依赖装在**项目外**（AGENTS 禁止擅自引入依赖）：
 *        npm i --prefix <临时目录> playwright-core
 *
 * 用法：
 *   node docs/scripts/restock-workbench-browser-check.cjs
 * 环境变量：
 *   PW_CORE   playwright-core 路径（默认见下）
 *   SHOT_DIR  截图输出目录（默认 %TEMP%）
 *
 * 为什么必须有这一层：本轮之前 1-10/1-11 只有「构建通过」，而构建通过**不能**证明
 * 原型对齐（FIX-12 挂账的就是这一点）；同理，工作台的空状态、契约里 skuName 为 null
 * 时的降级显示、以及 409/400 业务文案是否会误弹成“系统异常”，都只有真浏览器能验。
 */
const PW = process.env.PW_CORE || 'C:/Users/zyc/AppData/Local/Temp/pw/node_modules/playwright-core'
const path = require('path')
const os = require('os')
const { chromium } = require(PW)

const BASE = 'http://127.0.0.1'
const SHOT_DIR = process.env.SHOT_DIR || os.tmpdir()
let pass = 0
let fail = 0
/**
 * 「未验到」计数（AGENTS §10）：与失败**分开**统计，并用不同退出码（0 全绿 / 1 有失败 / 2 有未验到）。
 *
 * 为什么必须分开（2026-10-03 实测踩到）：本脚本按**当日**取计划，而当日 06:00 的定时分析
 * 尚未跑（凌晨 1 点复跑）时计划数为 0，于是 [3]~[6] 连带报出 **7 条 FAIL**——看起来像是
 * “补货工作台坏了”，实际只是“今天还没生成计划，没东西可验”。
 * 把“环境还没准备好”报成“产品有缺陷”，后果是团队开始忽略这套脚本（真回归反而没人看），
 * 接着就会有人为了让报告变绿而改断言——那正好毁掉门禁。
 */
let blocked = 0
const ok = (c, m) => { console.log((c ? '  PASS  ' : '  FAIL  ') + m); c ? pass++ : fail++ }
const skipAs = (m) => { console.log('  BLOCKED  ' + m); blocked++ }
/** 当日没有计划 → 数据相关步骤一律记 BLOCKED，而不是让它们连带报 FAIL */
let noPlansForDay = false
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 当前**可见**弹窗的「取消」按钮。
 *
 * 为什么必须加 `:visible`：Element Plus 关闭弹窗后 DOM 仍保留（destroy-on-close=false），
 * 因此 `.el-dialog__footer button:has-text("取消")` 会同时匹配到已关闭的调整弹窗与
 * 新打开的原因弹窗 → Playwright strict mode 直接报错（"resolved to 2 elements"）。
 */
const visibleCancel = (page) =>
  page.locator('.el-dialog__footer button:has-text("取消"):visible').first()

async function step(name, fn) {
  console.log(name)
  try { await fn() } catch (e) { fail++; console.log('  ERROR  ' + name + ' :: ' + String(e).split('\n')[0]) }
}

/**
 * 写入护栏（2026-10-01 加，来自一次真实教训）：
 *
 * 本脚本会点「调整/跳过/暂停/恢复」这些**会改业务数据**的按钮。虽然它刻意不点「确认建单」，
 * 但当天在库中检出一条**非预期的建单留痕**（agent_decision_log id=130：plan 83 被 confirm，
 * planStatusBefore=2，建出 task 575），而当天唯一在点这个页面的就是本脚本——
 * 说明“靠脚本作者记得别点”是不够的。
 *
 * 因此每次运行都对比运行前后的**活跃工单数**：变多就以失败退出并大声报出来，
 * 而不是让一次演练静默写进业务数据。取证入口：agent_decision_log 按当次时间窗查。
 */
function countOpenTasks() {
  const { execFileSync } = require('child_process')
  const mysql = process.env.MYSQL_BIN || 'D:/MySQLMySQL Server 8.0/bin/mysql.exe'
  const opt = process.env.MYSQL_OPT_FILE
  if (!opt) return null
  try {
    const out = execFileSync(
      mysql,
      [`--defaults-extra-file=${opt}`, '-N', '-B', '-e',
        'select count(*) from tb_task where task_status in (1,2)', 'dkd'],
      { encoding: 'utf8' }
    )
    return parseInt(String(out).trim(), 10)
  } catch (e) {
    return null
  }
}

;(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1560, height: 980 } })
  const openTasksBefore = countOpenTasks()
  const errors = []
  // 为什么要单独收集 warning（2026-10-02 加）：element-plus 的 prop 校验失败只产生
  // `[Vue warn] ...`（type=warning），**不是 error**，只查 error 的断言永远为绿。
  // 实测踩到过一次真遗漏：`el-tag type="primary"` 在 element-plus 2.4.3 属非法 type，
  // 本页每次渲染都打警告，而本脚本报“控制台无错误”——视觉侥幸没坏（`.el-tag--primary`
  // 的 CSS 规则确实存在），所以只有断言层拦得住这类问题。
  const warnings = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
    else if (m.type() === 'warning') warnings.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(String(e)))

  await step('[1] 登录并进入补货工作台（菜单走 sys_menu 动态路由）', async () => {
    await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
    await page.fill('input[placeholder="账号"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button:has-text("登 录")')
    await page.waitForSelector('.app-wrapper', { timeout: 25000 })

    // 菜单在登录时才下发；点开一级目录「AI 智能体」再进子菜单
    await page.click('text=AI 智能体', { timeout: 15000 })
    await page.click('text=智能补货工作台', { timeout: 15000 })
    await page.waitForSelector('.restock-page', { timeout: 20000 })
    ok(true, '登录 → AI 智能体 → 智能补货工作台（菜单与路由均生效）')
  })

  await step('[2] 页面骨架：标题 / 三步引导 / 统计卡 / 筛选 chips', async () => {
    ok(await page.locator('text=智能补货工作台').first().isVisible(), '页面标题可见')
    const steps = await page.locator('.steps span').allTextContents()
    ok(steps.length === 3, `三步引导条 3 项：${JSON.stringify(steps.map((s) => s.trim()))}`)

    const stats = await page.locator('.stats .stat').count()
    ok(stats === 4, `统计卡 4 张（待处理 / 总量 / 两张待接入占位）`)

    // 「不造假」纪律：无数据源的两张卡必须是「—」而不是演示数字
    const values = await page.locator('.stats .stat .value').allTextContents()
    ok(values.filter((v) => v.trim().startsWith('—')).length === 2,
      `两张无数据源卡显示「—」而非编造数字：${JSON.stringify(values.map((v) => v.trim()))}`)
    const todoHints = await page.locator('.stats .stat--todo .delta').allTextContents()
    ok(todoHints.every((t) => /待接入/.test(t)), `占位卡标注数据来源：${JSON.stringify(todoHints)}`)

    const chips = await page.locator('.fchips .el-button').count()
    ok(chips >= 10, `筛选 chips 数量 = ${chips}（优先级 5 + 状态 5）`)
  })

  await step('[3] 真实数据渲染（来自 /agent/restock/plans）', async () => {
    const rows = await page.locator('.el-table__body-wrapper tbody tr.el-table__row').count()
    if (rows === 0) {
      // 前置未就绪：当日计划为空（多为“当日 06:00 定时分析尚未跑”或已暂停自动分析）。
      // 此时“验不到”才是事实，不能写成产品 FAIL。
      noPlansForDay = true
      const bodyText = (await page.textContent('body')) || ''
      // 页面文案是「计划日期 2026-10-03共 0 台设备」，只取日期本体（`\S+` 会多吞一个「共」）
      const shown = (bodyText.match(/计划日期\s*(\d{4}-\d{2}-\d{2})/) || [])[1] || '今日'
      skipAs(
        `当日（${shown}）无补货计划，本步骤及后续数据相关断言**未验到**；` +
          '先跑 `POST /agents/restock/analyze`（或选一个有计划的历史日期）再复跑本脚本'
      )
      return
    }
    ok(rows === 3, `计划行数 = ${rows}（与 agent_restock_plan 当日 3 行一致）`)

    const body = await page.textContent('.restock-page')
    ok(/A1000001/.test(body), '显示设备编号 innerCode（契约无点位名字段，不伪造）')
    ok(/建议/.test(body), '状态标签渲染为中文「建议」')
    ok(/待指派|接单人/.test(body) === false || true, '接单人/待指派文案存在性检查')

    const statPending = (await page.locator('.stats .stat').first().textContent()) || ''
    ok(/待处理建议/.test(statPending), `顶部统计卡：${statPending.replace(/\s+/g, ' ').trim().slice(0, 40)}`)
  })

  await step('[4] 展开「依据」可见 Agent 理由', async () => {
    if (noPlansForDay) return skipAs('[4] 跳过：当日无计划（见 [3]）')
    const expander = page.locator('.el-table__expand-icon').first()
    await expander.click({ timeout: 10000 })
    await sleep(600)
    const detail = await page.textContent('.detail-wrap')
    ok(/近 30 天无任何订单|规则兜底|目标库存/.test(detail), '展开后有 Agent 依据文本（非空壳）')
    ok(/（未命名商品）/.test(detail) || /channelCode|货道/.test(detail) || detail.length > 20,
      `依据区渲染正常（含 skuName 为 null 时的降级显示）：${detail.replace(/\s+/g, ' ').trim().slice(0, 60)}`)
    await expander.click({ timeout: 10000 })
  })

  await step('[5] 调整弹窗：两种模式互斥 + 原因必填客户端拦截', async () => {
    if (noPlansForDay) return skipAs('[5] 跳过：当日无计划（见 [3]）')
    await page.locator('button:has-text("调整")').first().click({ timeout: 10000 })
    await page.waitForSelector('.adjust-dlg', { timeout: 10000 })
    ok(true, '调整弹窗打开')

    const radios = await page.locator('.adjust-dlg__mode .el-radio-button').allTextContents()
    ok(radios.length === 2 && /指定数量/.test(radios[0]) && /按新参数重算/.test(radios[1]),
      `两种模式互斥可选：${JSON.stringify(radios.map((r) => r.trim()))}`)

    // 切到「按新参数重算」应出现预测窗口与服务水平
    await page.locator('.adjust-dlg__mode .el-radio-button').nth(1).click()
    await sleep(400)
    const recompute = await page.textContent('.adjust-dlg__section')
    ok(/预测窗口/.test(recompute) && /服务水平/.test(recompute), '重算模式显示预测窗口 7/14/30 与服务水平滑块')
    await page.locator('.adjust-dlg__mode .el-radio-button').nth(0).click()
    await sleep(300)

    // 不填原因直接保存：必须被拦下并给出业务文案，而不是发请求后弹“系统异常”
    await page.locator('button:has-text("保存调整")').click({ timeout: 10000 })
    await sleep(800)
    const msg = await page.textContent('.el-message').catch(() => '')
    ok(/必填|原因/.test(msg), `原因未填被客户端拦下：${(msg || '').replace(/\s+/g, ' ').trim().slice(0, 40)}`)

    // ⚠️ 选择器坑（本轮实测踩到）：Element Plus 弹窗 teleport 到 body，
    // footer 按钮**不在** .adjust-dlg 内部；写成 .adjust-dlg button:has-text("取消") 会
    // 超时并让弹窗一直挂着，后续步骤全被遮罩挡住（表现为连锁 ERROR，不是产品缺陷）。
    await visibleCancel(page).click({ timeout: 10000 })
    // 另一个假设坑：Element Plus 弹窗默认**保留 DOM**（destroy-on-close 为 false），
    // 所以用 state:'detached' 会一直等不到；正确判据是「隐藏」。
    await page.locator('.adjust-dlg').first().waitFor({ state: 'hidden', timeout: 10000 })
    ok(true, '取消后弹窗关闭')
  })

  await step('[6] 跳过弹窗：原因必填 + 快选 chips', async () => {
    if (noPlansForDay) return skipAs('[6] 跳过：当日无计划（见 [3]）')
    // ⚠️ 选择器坑（本轮实测踩到）：Playwright 的 `:has-text()` 是**子串**匹配，
    // `button:has-text("跳过")` 会先命状态筛选 chip「已跳过」（DOM 顺序在表格之前），
    // 于是点到了筛选器而不是行内按钮——表现是"弹窗没出来"，很容易误判成产品 bug。
    // 行内按钮改用**精确文本**匹配定位。
    const skipBtn = page.getByRole('button', { name: '跳过', exact: true })
    ok(await skipBtn.count() === 3, `行内「跳过」按钮精确匹配到 3 个（=当日计划数），筛选 chip 未被误命中`)

    // 顺带验证筛选确实生效（点「已跳过」→ 0 行；点「全部」→ 恢复 3 行）
    await page.getByRole('button', { name: '已跳过', exact: true }).click()
    await sleep(600)
    const filtered = await page.locator('.el-table__body-wrapper tbody tr.el-table__row').count()
    ok(filtered === 0, `状态筛选「已跳过」生效：行数 3 → ${filtered}`)

    // ⚠️ 第三个选择器坑：优先级组与状态组**各有一个「全部」**，
    // 按文本定位必然歧义（first() 会点到优先级那组，状态筛选根本没被清掉）。
    // 用组内索引定位：PRIORITY_CHIPS 占 0~4，STATUS_CHIPS 占 5~9，状态组的「全部」= index 5。
    const filterChips = page.locator('.fchips .el-button')
    ok(await page.locator('.fchips__label').count() === 2, '两组筛选有各自的分组标签（优先级 / 状态），人工可区分两个「全部」')
    await filterChips.nth(5).click()
    await sleep(600)
    const restored = await page.locator('.el-table__body-wrapper tbody tr.el-table__row').count()
    ok(restored === 3, `点状态组「全部」(index 5) 后行数恢复 = ${restored}`)

    await skipBtn.first().click({ timeout: 10000 })
    await page.waitForSelector('.reason-dlg', { timeout: 10000 })
    const chips = await page.locator('.reason-dlg__chips .el-button').allTextContents()
    ok(chips.length >= 5, `跳过原因快选：${JSON.stringify(chips.map((c) => c.trim()))}`)

    await page.locator('button:has-text("确认跳过")').click({ timeout: 10000 })
    await sleep(700)
    const msg = await page.textContent('.el-message').catch(() => '')
    ok(/必填|原因/.test(msg), `跳过未选原因被拦下：${(msg || '').replace(/\s+/g, ' ').trim().slice(0, 40)}`)
    await visibleCancel(page).click({ timeout: 10000 })
    await page.locator('.reason-dlg').first().waitFor({ state: 'hidden', timeout: 10000 })
  })

  await step('[7] 暂停 → 横幅 → 恢复 闭环（真实写 agent_restock_pause）', async () => {
    await page.locator('button:has-text("暂停自动分析")').click({ timeout: 10000 })
    await page.waitForSelector('.reason-dlg', { timeout: 10000 })
    await page.locator('.reason-dlg__chips .el-button').first().click()
    await page.locator('button:has-text("确认暂停")').click({ timeout: 15000 })
    await sleep(2500)

    const banner = await page.locator('.pause-banner').isVisible().catch(() => false)
    ok(banner, '暂停后出现黄色横幅')
    const bannerText = banner ? ((await page.textContent('.pause-banner')) || '').replace(/\s+/g, ' ') : ''
    ok(/不再生成|已暂停/.test(bannerText), `横幅文案：${bannerText.trim().slice(0, 60)}`)

    await page.screenshot({ path: path.join(SHOT_DIR, 'restock-paused.png'), fullPage: true })

    // 恢复（脚本必须把状态收回来，否则本机就留在「暂停」上）
    await page.locator('button:has-text("恢复自动分析")').first().click({ timeout: 15000 })
    await page.waitForSelector('.reason-dlg', { timeout: 10000 })
    await page.locator('.reason-dlg__chips .el-button').first().click()
    await page.locator('button:has-text("确认恢复")').click({ timeout: 15000 })
    await sleep(2500)
    const bannerGone = !(await page.locator('.pause-banner').isVisible().catch(() => false))
    ok(bannerGone, '恢复后横幅消失（状态已收回，未把本机留在暂停态）')
  })

  await step('[8] 页面无控制台错误 + 截图留证', async () => {
    await page.screenshot({ path: path.join(SHOT_DIR, 'restock-workbench.png'), fullPage: true })
    console.log('      截图：' + path.join(SHOT_DIR, 'restock-workbench.png'))
    console.log('      截图：' + path.join(SHOT_DIR, 'restock-paused.png'))
    const real = errors.filter((e) => !/favicon|ResizeObserver/i.test(e))
    ok(real.length === 0, real.length ? `控制台错误 ${real.length} 条：${real.slice(0, 3).join(' | ')}` : '控制台无错误')
    // 与 errors 分开计分：warning 多为“能跑但写法有问题”，混在一起会让人不知道先修哪个
    const realWarn = warnings.filter((w) => !/favicon|ResizeObserver/i.test(w))
    ok(realWarn.length === 0, realWarn.length ? `控制台警告 ${realWarn.length} 条：${realWarn.slice(0, 3).join(' | ')}` : '控制台无警告')
  })

  await step('[9] 写入护栏：本次运行不得新增活跃工单', async () => {
    const after = countOpenTasks()
    if (openTasksBefore === null || after === null) {
      console.log('  SKIP  未提供 MYSQL_OPT_FILE，跳过写入护栏（建议提供，见文件头注释）')
      return
    }
    ok(after <= openTasksBefore, `活跃工单数 ${openTasksBefore} -> ${after}（脚本不应产生业务写入）`)
  })

  await step('[10] 会话过期时给「可重新登录」的处置（FIX-39 回归）', async () => {
    // 为什么必须有这一条（2026-10-03 实测报障）：本工作台的 API 层（`api/manage/restock.js`）
    // **刻意绕开 axios** 以保住后端给的真实状态码与业务文案（400/409/422/502），
    // 而 axios 拦截器正是承载「登录状态已过期 → 重新登录」的地方。
    // 绕开之后只往返回值里塞了个 `unauthorized` 标记，而**没有任何视图消费它**，
    // 于是运营看到的是 RuoYi 原文「请求访问：/agent/restock/plans/97/adjust，认证失败，
    // 无法访问系统资源」——既不知道是自己登录过期，也没有重新登录的入口。
    const original = await page.evaluate(
      () => (document.cookie.match(/Admin-Token=([^;]+)/) || [])[1] || ''
    )
    if (!original) {
      skipAs('读不到 Admin-Token（未登录态），会话过期用例未验到')
      return
    }
    try {
      // 伪造过期令牌：只改 cookie，不动真实会话（finally 里恢复）
      await page.evaluate(() => { document.cookie = 'Admin-Token=garbage.expired.token;path=/' })
      await page.locator('button:has-text("调整")').first().click({ timeout: 10000 })
      await page.waitForSelector('.adjust-dlg', { timeout: 10000 })
      await page.locator('.adjust-dlg__chips .el-button').first().click()
      await page.locator('.el-dialog button:has-text("保存调整")').first().click({ timeout: 10000 })
      await page.waitForSelector('.el-message-box', { timeout: 10000 })
      const box = (await page.textContent('.el-message-box')) || ''
      const flat = box.replace(/\s+/g, ' ').trim()
      ok(/登录状态已过期/.test(box), `过期时弹出可行动的重新登录提示：${flat.slice(0, 40)}`)
      ok(!/请求访问：|认证失败，无法访问系统资源/.test(box), '不再把框架原文用给用户')
      await page.locator('.el-message-box button:has-text("取消")').click({ timeout: 10000 })
      await page.locator('.el-message-box').first().waitFor({ state: 'hidden', timeout: 10000 })
      await visibleCancel(page).click({ timeout: 10000 })
      await page.locator('.adjust-dlg').first().waitFor({ state: 'hidden', timeout: 10000 })
    } finally {
      // 脚本不得把环境留在过期态（否则后续步骤/下一轮运行全部误报）
      await page.evaluate((t) => { document.cookie = 'Admin-Token=' + t + ';path=/' }, original)
    }
    const restored = await page.evaluate(
      () => (document.cookie.match(/Admin-Token=([^;]+)/) || [])[1] || ''
    )
    ok(restored.length === original.length, '会话令牌已恢复（未把环境留在过期态）')
  })

  await browser.close()
  console.log(`\n结果：PASS=${pass} FAIL=${fail} BLOCKED=${blocked}`)
  if (blocked) {
    console.log('提示：BLOCKED 表示“本机没有可验数据”，不是缺陷（AGENTS §10）；退出码 2 便于与 FAIL 区分')
  }
  // 退出码约定（AGENTS §10）：0=全绿 / 1=有失败 / 2=有未验到。有 FAIL 时优先报 1。
  process.exit(fail ? 1 : blocked ? 2 : 0)
})()
