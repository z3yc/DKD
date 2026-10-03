<template>
  <div class="app-container agent-audit">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="76px">
      <el-form-item label="场景" prop="scene">
        <el-select v-model="queryParams.scene" placeholder="全部场景" clearable style="width: 150px">
          <el-option v-for="s in SCENES" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="触发方式" prop="triggerType">
        <el-select v-model="queryParams.triggerType" placeholder="全部" clearable style="width: 140px">
          <el-option v-for="t in TRIGGERS" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="结果" prop="result">
        <el-select v-model="queryParams.result" placeholder="全部结果" clearable style="width: 140px">
          <el-option v-for="r in RESULTS" :key="r.value" :label="r.label" :value="r.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="动作" prop="action">
        <el-input
          v-model="queryParams.action"
          placeholder="如 restock.confirm"
          clearable
          style="width: 180px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="目标ID" prop="targetId">
        <el-input
          v-model="queryParams.targetId"
          placeholder="计划ID / 设备编号"
          clearable
          style="width: 160px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="追踪ID" prop="requestId">
        <el-input
          v-model="queryParams.requestId"
          placeholder="X-Request-Id"
          clearable
          style="width: 200px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="操作时间">
        <el-date-picker
          v-model="dateRange"
          value-format="YYYY-MM-DD"
          type="daterange"
          range-separator="-"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 240px"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>

    <!--
      ⚠️ 这里**没有**「新增/修改/删除」按钮，是有意的：
      留痕是审计证据（AGENTS §6.3），写入方只有智能体服务。
      后台一旦能改或能删，它就无法再回答“谁在什么时候点了什么、最后怎么样”。
    -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="审计留痕只读"
      description="本页展示智能体的完整决策留痕：业务写操作（含成功 / 幂等 / 被拒绝 / 失败四条路径）与会话问答（chat.respond，用于链路追踪与 token 计量）。留痕由智能体服务写入，后台不提供修改与删除。"
    />

    <el-table v-loading="loading" :data="logList" border size="small" row-key="id">
      <el-table-column label="时间" prop="createTime" width="160" />
      <el-table-column label="场景" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="sceneTagType(row.scene)">{{ sceneLabel(row.scene) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="触发方式" width="92">
        <template #default="{ row }">{{ triggerLabel(row.triggerType) }}</template>
      </el-table-column>
      <el-table-column label="用户" width="70">
        <template #default="{ row }">{{ row.userId === null ? '—' : row.userId }}</template>
      </el-table-column>
      <el-table-column label="动作" prop="action" min-width="150" />
      <el-table-column label="目标" min-width="130">
        <template #default="{ row }">
          <span>{{ row.targetType || '—' }}</span>
          <span v-if="row.targetId" class="dim">#{{ row.targetId }}</span>
        </template>
      </el-table-column>
      <el-table-column label="结果" width="92">
        <template #default="{ row }">
          <el-tag size="small" :type="resultTagType(row.result)">{{ resultLabel(row.result) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="失败/拒绝原因" prop="errorMsg" min-width="220">
        <template #default="{ row }">
          <span v-if="row.errorMsg" class="err-text">{{ row.errorMsg }}</span>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="90">
        <template #default="{ row }">
          <span v-if="row.confidence !== null && row.confidence !== undefined">{{ row.confidence }}</span>
          <span v-else class="dim" title="人工干预不产生置信度，不是缺失">—</span>
        </template>
      </el-table-column>
      <el-table-column label="token" prop="costTokens" width="80" />
      <el-table-column label="追踪ID" width="150">
        <template #default="{ row }">
          <span class="mono">{{ row.requestId || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" align="center" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="handleDetail(row)">详情/回放</el-button>
          <!--
            链路入口：只有带 requestId 的行才有链路可串（留痕行理论上都可能为空，故显式判空）。
            与「详情」的分工：详情看**这一条**的完整上下文；链路看**同一次请求**的先后顺序。
          -->
          <el-button
            v-if="row.requestId"
            size="small"
            type="primary"
            link
            @click="handleTrace(row)"
          >链路</el-button>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
    </el-table>

    <pagination
      v-show="total > 0"
      :total="total"
      v-model:page="queryParams.pageNum"
      v-model:limit="queryParams.pageSize"
      @pagination="getList"
    />

    <!-- 详情：JSON 上下文用 <pre> 文本插值渲染（不用 v-html，天然不会有注入面） -->
    <el-dialog v-model="detail.open" title="决策详情（审计回放）" width="760px" append-to-body>
      <div v-if="detail.row" class="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="留痕ID">{{ detail.row.id }}</el-descriptions-item>
          <el-descriptions-item label="时间">{{ detail.row.createTime }}</el-descriptions-item>
          <el-descriptions-item label="场景">{{ sceneLabel(detail.row.scene) }}</el-descriptions-item>
          <el-descriptions-item label="触发方式">{{ triggerLabel(detail.row.triggerType) }}</el-descriptions-item>
          <el-descriptions-item label="用户">{{ detail.row.userId === null ? '—（定时任务）' : detail.row.userId }}</el-descriptions-item>
          <el-descriptions-item label="动作">{{ detail.row.action }}</el-descriptions-item>
          <el-descriptions-item label="目标">
            {{ detail.row.targetType }} <span v-if="detail.row.targetId">#{{ detail.row.targetId }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="结果">
            <el-tag size="small" :type="resultTagType(detail.row.result)">{{ resultLabel(detail.row.result) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="追踪ID" :span="2">
            <span class="mono">{{ detail.row.requestId || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="失败/拒绝原因" :span="2">
            {{ detail.row.errorMsg || '—' }}
          </el-descriptions-item>
        </el-descriptions>

        <!--
          ⚠️ 未取到完整留痕前**不得渲染这两块**（否则会显示「（无）」）。

          为什么（2026-10-03 实测）：`handleDetail` 先把列表行给 `detail.row`（列表 VO
          **不含** `input_context`/`llm_output` 这两个大字段），再异步取单条详情回填。
          中间那个窗口里 `pretty(undefined)` 输出的是「（无）」——在审计页上这等于
          **把“还在加载”断言成了“这条留痕没有数据”**，而两者结论完全相反。
          （该窗口是靠浏览器验收脚本的“块行数 [1,1] / 内容首行（无）”反复报状才定位到的。）
          现改为：加载中不给块，取回后再渲染。
        -->
        <div v-loading="detail.loading" element-loading-text="正在加载完整留痕…">
          <div v-if="detail.loading" class="json-loading-tip">
            正在加载完整留痕（含 JSON 上下文）…
          </div>
          <template v-else>
            <div class="json-block">
              <div class="json-block__title">输入上下文（input_context，脱敏后）</div>
              <pre class="json-block__body">{{ pretty(detail.row.inputContext) }}</pre>
            </div>
            <div class="json-block">
              <div class="json-block__title">
                LLM 结构化输出（llm_output）
                <span class="dim">人工干预恒为空——不把人工操作伪装成模型建议</span>
              </div>
              <pre class="json-block__body">{{ pretty(detail.row.llmOutput) }}</pre>
              <!--
                可读版正文。

                为什么要多这一块（2026-10-03 报障）：`llm_output` 是 JSON，而模型的回答是一个
                很长的 markdown 字符串——它的换行在 JSON 里必须写成 `\n` 转义，所以直接看上面那块
                原文时**整段回复是一行**（实测 6447px）。审计既要「原样可查」（上面保留）也要
                「人读得下去」（这里还原换行），两者不能互相替代：
                · 只留原文 → 运营面对一行带 `\n` 的长文本，等于没给可读信息；
                · 只留还原版 → 丢掉"上游到底写了什么"的凭证（审计场景不能这么做）。
                本块**不做任何改写**（不脱敏/不裁剪/不重排），只把 `\n` 还原成真换行。
              -->
              <div v-if="replyText" class="json-block__sub">
                <div class="json-block__title">
                  回复正文
                  <span class="dim">换行已还原便于阅读；内容未改写，以原始值为准</span>
                </div>
                <pre class="json-block__body">{{ replyText }}</pre>
              </div>
            </div>
          </template>
        </div>
      </div>
      <template #footer>
        <el-button @click="detail.open = false">关闭</el-button>
      </template>
    </el-dialog>

    <!--
      本次请求链路（按 X-Request-Id 串联，升序）。

      为什么需要它：审计最常问的不是“某一条留痕写了什么”，而是
      「这一次操作到底发生了什么」——状态机放行 → 回调建单 → 回写状态，
      可能落在 1~3 条留痕上。列表是倒序分页，看不到这个顺序。
    -->
    <el-dialog
      v-model="trace.open"
      :title="'本次请求链路：' + (trace.requestId || '—')"
      width="820px"
      append-to-body
    >
      <div v-loading="trace.loading" class="trace">
        <el-empty v-if="!trace.loading && trace.rows.length === 0" description="该请求号在留痕表里没有记录" />
        <el-timeline v-else>
          <el-timeline-item
            v-for="row in trace.rows"
            :key="row.id"
            :timestamp="row.createTime"
            placement="top"
            :type="resultTagType(row.result)"
          >
            <div class="trace-item">
              <div class="trace-head">
                <b>{{ row.action }}</b>
                <el-tag size="small" :type="resultTagType(row.result)">{{ resultLabel(row.result) }}</el-tag>
                <span class="dim">
                  #{{ row.id }} · {{ triggerLabel(row.triggerType) }} ·
                  {{ row.targetType }}<span v-if="row.targetId">#{{ row.targetId }}</span>
                </span>
              </div>
              <div v-if="row.errorMsg" class="err-text">{{ row.errorMsg }}</div>
              <pre class="json-block__body">{{ pretty(row.inputContext) }}</pre>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
      <template #footer>
        <span class="dim trace-foot">只读：按 X-Request-Id 串联同一次请求的全部留痕（升序，最多 50 条）</span>
        <el-button @click="trace.open = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="AgentAudit">
import { ElMessage } from 'element-plus'
import { getDecisionLog, listDecisionLogs, traceDecisionLog } from '@/api/manage/agentAudit'

/**
 * 智能体决策审计页（排期任务 3-7）。
 *
 * AGENTS §10 的 CR 清单里有一条「Agent 相关改动：决策留痕已接」——
 * 留痕数据自 1-7b 起已经在正常写入 `agent_decision_log`，本页把它变成**可查、可回放**。
 *
 * 三处刻意的界面取舍：
 *  1. **没有写操作按钮**：留痕是审计证据，后台不能改也不能删（理由见模板里的提示条）。
 *  2. **列表不带 JSON 上下文**：该表随每次对话增长，列表塞大 JSON 会让分页越来越慢；
 *     点「详情/回放」才按 id 取那一条（对应后端 list/detail 两个 SQL）。
 *  3. **置信度为空显示「—」并说明原因**：人工干预本来就不产生置信度，
 *     留空不等于缺数据；用 title 写清楚，避免审计时误判为“数据缺失”。
 */

const { proxy } = getCurrentInstance()

const SCENES = [
  { value: 1, label: '通用问答' },
  { value: 2, label: '补货' },
  { value: 3, label: '诊断' },
  { value: 4, label: '运营分析' }
]
const TRIGGERS = [
  { value: 1, label: '用户会话' },
  { value: 2, label: '定时任务' },
  { value: 3, label: '人工干预' }
]
/** 与 docs/ddl/agent_tables.sql 的列注释逐字对齐（0-待定 1-成功 2-失败 3-被拒绝） */
const RESULTS = [
  { value: 1, label: '成功' },
  { value: 2, label: '失败' },
  { value: 3, label: '被拒绝' },
  { value: 0, label: '待定' }
]

const loading = ref(true)
const showSearch = ref(true)
const logList = ref([])
const total = ref(0)
const dateRange = ref([])
const detail = reactive({ open: false, row: null, loading: false })

/**
 * 详情弹窗里的「回复正文」：把 `llm_output.reply` 的 `\n` 还原成真换行。
 *
 * 只在**确实含换行**时才给：单行回复另开一块是噪声（审计页信息密度本来就高）。
 * 解析失败一律返回空串——审计页面不能因为一条留痕格式异常就整页报错，原始值那块照常显示。
 */
const replyText = computed(() => {
  const raw = detail.row && detail.row.llmOutput
  if (!raw) return ''
  let obj
  try {
    obj = typeof raw === 'string' ? JSON.parse(raw) : raw
  } catch (e) {
    return ''
  }
  const reply = obj && typeof obj.reply === 'string' ? obj.reply : ''
  return reply.indexOf('\n') >= 0 ? reply : ''
})
/** 链路弹窗状态（requestId + 升序留痕行）；loading 必须在 finally 复位（AGENTS §2.2） */
const trace = reactive({ open: false, loading: false, requestId: '', rows: [] })

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    scene: null,
    triggerType: null,
    result: null,
    action: null,
    targetId: null,
    requestId: null
  }
})
const { queryParams } = toRefs(data)

function sceneLabel(value) {
  const hit = SCENES.filter((s) => s.value === value)[0]
  return hit ? hit.label : String(value)
}

function triggerLabel(value) {
  const hit = TRIGGERS.filter((t) => t.value === value)[0]
  return hit ? hit.label : String(value)
}

function resultLabel(value) {
  const hit = RESULTS.filter((r) => r.value === value)[0]
  return hit ? hit.label : String(value)
}

/**
 * 场景 → el-tag 配色。
 *
 * ⚠️ 运营分析（4）返回 `''` 而非 `'primary'`：element-plus 2.4.3 的 `ElTag.type`
 * 只接受 ["success","info","warning","danger",""]，传 `'primary'` 会每次渲染触发
 * Vue prop 校验告警。空字符串即默认主色，与 `system/dict/data.vue` 的写法一致。
 */
function sceneTagType(value) {
  // `??` 而非 `||`：`''` 是 el-tag 的默认主色（合法取值）但 falsy，
  // 用 `||` 会被兜底成 'info'（灰色）——配色会被静默改掉。
  return { 1: 'info', 2: 'success', 3: 'warning', 4: '' }[value] ?? 'info'
}

function resultTagType(value) {
  // 1-成功 green / 2-失败 red / 3-被拒绝 orange / 0-待定 gray
  return { 1: 'success', 2: 'danger', 3: 'warning', 0: 'info' }[value] || 'info'
}

/** JSON 文本美化：解析失败就原样显示（不吞掉内容，审计要看到真实写入值） */
function pretty(text) {
  if (!text) return '（无）'
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch (e) {
    return text
  }
}

/** 查询列表（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  queryParams.value.params = {}
  if (dateRange.value && dateRange.value.length === 2) {
    queryParams.value.params['beginTime'] = dateRange.value[0]
    queryParams.value.params['endTime'] = dateRange.value[1]
  }
  listDecisionLogs(queryParams.value)
    .then((response) => {
      logList.value = response.rows
      total.value = response.total
    })
    .finally(() => {
      loading.value = false
    })
}

function handleQuery() {
  queryParams.value.pageNum = 1
  getList()
}

function resetQuery() {
  dateRange.value = []
  const form = proxy.$refs['queryRef']
  if (form) form.resetFields()
  handleQuery()
}

/**
 * 打开「本次请求链路」：按 requestId 取升序留痕。
 *
 * 错误处理与列表一致：失败不静默（AGENTS §6.2），给用户可读提示并保持弹窗可关。
 */
function handleTrace(row) {
  trace.open = true
  trace.loading = true
  trace.requestId = row.requestId
  trace.rows = []
  traceDecisionLog(row.requestId)
    .then((response) => {
      trace.rows = response.data || []
    })
    .catch(() => {
      ElMessage.error('链路查询失败，请稍后重试')
    })
    .finally(() => {
      trace.loading = false
    })
}

/**
 * 打开详情/回放。
 *
 * 两段式：先把**列表行**给 `detail.row`（弹窗立刻有摘要可看），再异步取**单条详情**回填
 * （列表 VO 不含 `input_context`/`llm_output` 这两个大字段）。
 *
 * ⚠️ `loading` 不是锦上添花（2026-10-03 实测）：没有它时，取数返回前那一个窗口里模板把
 * `pretty(undefined)` 渲染成「（无）」——在审计页上等于**把“还在加载”断言成“这条留痕没有数据”**，
 * 两者结论完全相反。所以加载期间不给 JSON 块，并在 `finally` 里复位（AGENTS §2.2）。
 */
function handleDetail(row) {
  detail.row = row
  detail.open = true
  detail.loading = true
  getDecisionLog(row.id)
    .then((response) => {
      if (response.data) detail.row = response.data
    })
    .catch(() => {
      // 详情失败不影响已展示的列表摘要，明确告知而不是静默
      ElMessage.warning('详情加载失败，当前展示的是列表摘要（不含 JSON 上下文）')
    })
    .finally(() => {
      detail.loading = false
    })
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
/* 链路弹窗：把每条留痕的上下文压成可扫读的块（与详情弹窗的 json-block 同一视觉语言） */
.trace {
  max-height: 60vh;
  overflow-y: auto;

  .trace-item {
    .trace-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }

    .json-block__body {
      max-height: 160px;
    }
  }

  .trace-foot {
    float: left;
    line-height: 32px;
    font-size: 12px;
  }
}

/*
 * ⚠️ 下面这些**不能**裹在页面根类（原写法 `.agent-audit { ... }`）里。
 *
 * 为什么（2026-10-03 实测的显示 bug，有选择器级证据）：页面根是
 * `<div class="app-container agent-audit">`，而两个弹窗都带 `append-to-body`——
 * el-dialog 会把内容 **teleport 到 `<body>`**，于是 `<pre>` 虽然仍带 `data-v-*`，
 * 但**祖先 `.agent-audit` 已经不在它的路径上**。编译出来的选择器是
 * `.agent-audit .json-block__body[data-v-a90add8f]` → 一律不匹配 →
 * `white-space: pre-wrap` / `max-height` / `overflow` / 背景 / 内边距全部没生效。
 *
 * 现象：详情回放的 `llm_output.reply` 是一整段 markdown（换行是 JSON 字符串里的
 * `\n` 转义），落到没样式的 `<pre>` 上就是**一条 6447px 宽的长行**（实测
 * scrollWidth 6447 vs clientWidth 720），既看不到换行也读不下去。
 * 页面上不被察觉是因为 `input_context` 恰好短；一旦内容是长文本就暴露。
 *
 * 去掉包裹层是安全的：`<style scoped>` 已经给规则加上 `[data-v-*]`，隔离不依赖祖先。
 * 对照：同文件的 `.trace` 一直是顶层写的，所以链路弹窗的样式没问题——
 * 两个弹窗、两种写法，差异只在这里。
 */
.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 4px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.err-text {
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.6;
}

/* 加载中的占位：与两块 JSON 同高量级，避免弹窗先矮后跳 */
.json-loading-tip {
  margin-top: 12px;
  padding: 24px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.json-block {
  margin-top: 12px;

  /* 可读版正文与原始值同属一块，靠上边距与标题区分层级 */
  &__sub {
    margin-top: 10px;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 6px;

    .dim {
      font-weight: 400;
    }
  }

  &__body {
    max-height: 260px;
    overflow: auto;
    background: var(--el-fill-color-light);
    border-radius: 4px;
    padding: 10px;
    font-size: 12px;
    line-height: 1.6;
    /* 换行由 JSON 的结构缩进提供；字符串里的 `\\n` 是转义，不在这里还原（见下方说明） */
    white-space: pre-wrap;
    word-break: break-all;
    margin: 0;
  }
}
</style>
