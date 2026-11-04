<template>
  <div class="app-container agent-log">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="76px">
      <el-form-item label="时间范围" required>
        <!--
          时间范围是**查询的必填维度**（不是可选筛选）：
          后端对 agent_log 的查询强制时间窗（默认近 24h、最大 30 天），
          因为该表只增不减、单主库下无时间窗的分页会随日志量线性变慢（AGENTS §1 约束 5）。
          所以这里 clearable=false——清空时间范围没有意义（清空后后端仍会套 24h，页面与后端口径会不一致）。
        -->
        <el-date-picker
          v-model="dateRange"
          type="datetimerange"
          value-format="YYYY-MM-DD HH:mm:ss"
          range-separator="-"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          :clearable="false"
          :shortcuts="rangeShortcuts"
          style="width: 380px"
        />
      </el-form-item>
      <el-form-item label="级别" prop="level">
        <el-select v-model="queryParams.level" placeholder="全部级别" clearable style="width: 130px">
          <el-option v-for="item in LEVELS" :key="item" :label="item" :value="item" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态" prop="status">
        <el-select v-model="queryParams.status" placeholder="全部状态" clearable style="width: 120px">
          <el-option v-for="item in STATUSES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="关键词" prop="keyword">
        <el-input
          v-model="queryParams.keyword"
          placeholder="匹配日志正文/错误摘要"
          clearable
          style="width: 190px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="运行ID" prop="runId">
        <el-input
          v-model="queryParams.runId"
          placeholder="agent_run.run_id"
          clearable
          style="width: 190px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="追踪ID" prop="requestId">
        <el-input
          v-model="queryParams.requestId"
          placeholder="X-Request-Id"
          clearable
          style="width: 190px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button plain icon="Refresh" @click="getList">刷新</el-button>
      </el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList" />
    </el-row>

    <!--
      页面自解释：说明“为什么只读”“为什么有时间范围”“为什么展开不再请求”。
      日志是排障证据（设计口径见 docs/智能体配置化方案-2026-11-03.md §6），
      数据由 Python 侧 logging.Handler 异步写入，后台没有任何写入口。
    -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="执行日志只读，默认查询近 24 小时（单次最大 30 天）"
      description="日志由智能体服务异步写入 agent_log（级别 >= INFO），后台不提供修改与删除。行内「展开」直接使用列表已取回的数据，不会再发请求；「关联运行」跳到运行时间线页定位本次运行。"
    />

    <div class="list-wrap">
      <!-- 三态①：加载骨架（首屏/翻页/搜索期间都显示，避免把“加载中”误读成“没有日志”） -->
      <el-skeleton v-if="loading" :rows="8" animated class="list-skeleton" />

      <!-- 三态②：加载失败（给出原因 + 重试，不静默变成空列表） -->
      <el-empty v-else-if="loadError" :description="'日志加载失败：' + loadError">
        <el-button type="primary" @click="getList">重试</el-button>
      </el-empty>

      <!-- 三态③：空数据（说明是筛选结果为空，而不是“智能体没产生日志”） -->
      <el-empty
        v-else-if="logList.length === 0"
        description="当前筛选条件下没有日志：可放宽时间范围或清空级别/关键词后再试"
      >
        <el-button @click="resetQuery">调整筛选条件</el-button>
      </el-empty>

      <el-table v-else :data="logList" border size="small" row-key="id">
        <!-- 行内展开：详情全部来自列表行本身（后端 list VO 已含 message/errorMsg），不再补发详情请求 -->
        <el-table-column type="expand" width="46">
          <template #default="{ row }">
            <div class="log-detail">
              <el-descriptions :column="3" border size="small">
                <el-descriptions-item label="日志ID">{{ row.id }}</el-descriptions-item>
                <el-descriptions-item label="日志时间">{{ formatTime(row.createTime) }}</el-descriptions-item>
                <el-descriptions-item label="级别">
                  <el-tag size="small" :type="levelTag(row.level)">{{ row.level || '—' }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="场景">{{ sceneLabel(row.scene) }}</el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="节点">{{ row.node || '—' }}</el-descriptions-item>
                <el-descriptions-item label="logger">
                  <span class="mono">{{ row.logger || '—' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="耗时">{{ formatMs(row.durationMs) }}</el-descriptions-item>
                <el-descriptions-item label="写入来源">{{ row.createBy || '—' }}</el-descriptions-item>
                <el-descriptions-item label="追踪ID">
                  <span class="mono">{{ row.requestId || '—' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="运行ID">
                  <span class="mono">{{ row.runId || '—' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="会话ID">
                  <span class="mono">{{ row.conversationId || '—' }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="工单ID">{{ row.taskId || '—' }}</el-descriptions-item>
              </el-descriptions>

              <div class="detail-block">
                <div class="detail-block__title">日志正文（message）</div>
                <!-- 用 <pre> + 文本插值渲染：日志正文是不可信内容，不渲染 markdown/HTML（无 v-html，天然无注入面） -->
                <pre class="detail-block__body">{{ row.message || '（空）' }}</pre>
              </div>

              <div v-if="row.errorMsg" class="detail-block">
                <div class="detail-block__title">错误摘要（error_msg）</div>
                <pre class="detail-block__body detail-block__body--err">{{ row.errorMsg }}</pre>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.createTime) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="96" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="levelTag(row.level)">{{ row.level || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="场景" width="84" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="sceneTag(row.scene)">{{ sceneLabel(row.scene) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="84" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="正文" prop="message" min-width="300" show-overflow-tooltip>
          <template #default="{ row }">
            <span>{{ row.message || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="90">
          <template #default="{ row }">{{ formatMs(row.durationMs) }}</template>
        </el-table-column>
        <el-table-column label="关联对象" min-width="150">
          <template #default="{ row }">
            <span v-if="row.runId" class="mono">run: {{ shortId(row.runId) }}</span>
            <span v-else-if="row.conversationId" class="mono">conv: {{ shortId(row.conversationId) }}</span>
            <span v-else-if="row.taskId" class="mono">task: {{ row.taskId }}</span>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="追踪ID" width="150">
          <template #default="{ row }">
            <span class="mono">{{ row.requestId || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <!--
              权限点按**目标页**的权限显示，而不是本页权限：
              「运行时间线」页复用 manage:agentAudit:list（docs/ddl/agent_run_menu.sql），
              没有该权限的人点进去会 403，所以不给死按钮。
            -->
            <el-button
              v-if="row.runId"
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentAudit:list']"
              @click="goRun(row)"
            >关联运行</el-button>
            <el-button
              v-if="row.conversationId"
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentConversation:list']"
              @click="goConversation(row)"
            >会话</el-button>
            <span v-if="!row.runId && !row.conversationId" class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <pagination
      v-show="total > 0"
      :total="total"
      v-model:page="queryParams.pageNum"
      v-model:limit="queryParams.pageSize"
      @pagination="getList"
    />
  </div>
</template>

<script setup name="AgentLog">
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { listAgentLog } from '@/api/manage/agentLog'

/**
 * S6 智能体执行日志页（排期任务 2-4）。
 *
 * 数据来源：`agent_log`（docs/ddl/agent_config.sql 第 3 段），写入方只有 Python 侧
 * logging.Handler，本页只读；查询经 Java `GET /manage/agent/log/list`，不经 Python。
 *
 * 三个界面取舍（与审计页/S1 同一套纪律）：
 *  1. **时间窗必填、默认近 24h、最长 30 天**：无时间窗的全表分页会随日志量线性变慢，
 *     单主库下这是硬约束；前端先拦一次，是为了让用户立刻知道“为什么查不到更早的”，
 *     而不是看到后端报错才发现口径。
 *  2. **行内展开不再发请求**：list 返回的 VO 已含 message/errorMsg/requestId 等全部字段，
 *     点开再查详情等于把一次分页变成 N+1；日志排查要的是“快速扫上下文”。
 *  3. **「关联运行」「会话」不在前端写死路由**：菜单由 sys_menu 动态下发（AGENTS §2.2），
 *     写死 `/ai/agentRun` 一改菜单就 404。这里在当前路由表里按菜单 path 后缀找，
 *     找不到就明确提示（说明该环境没配菜单），而不是给死链。
 */

const route = useRoute()
const router = useRouter()

/** 级别枚举与 agent_log.level 列注释一致（VARCHAR 存标准 logging 级别名） */
const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
/** 状态枚举与 agent_log.status 列注释一致；未登记的值原样显示（不吞成“未知”） */
const STATUSES = [
  { value: 'ok', label: '正常' },
  { value: 'error', label: '失败' },
  { value: 'timeout', label: '超时' },
  { value: 'skipped', label: '跳过' }
]
/** 场景枚举与 agent_log.scene（chat/restock/fault 字符串，注意与 agent_conversation 的数字 scene 不同） */
const SCENES = {
  chat: { label: '对话', tag: 'info' },
  restock: { label: '补货', tag: 'success' },
  fault: { label: '故障', tag: 'warning' }
}

/** 后端强制的时间窗上限（30 天），与 docs/智能体配置化方案 §6「查询性能护栏」一致 */
const MAX_RANGE_MS = 30 * 24 * 60 * 60 * 1000

const loading = ref(true)
const loadError = ref('')
const showSearch = ref(true)
const logList = ref([])
const total = ref(0)
const queryRef = ref(null)

/**
 * 默认时间范围 = 近 24 小时。
 *
 * 用 dayjs 在**页面创建时**算一次，而不是写死字符串：用户打开页面时“近 24h”必须
 * 相对当前时间，写死会被之后的刷新带成“上次打开页面的那 24 小时”。
 * 重置查询时重新计算（resetQuery），保证“重置”恢复的也是当前时刻的近 24h。
 */
function defaultRange() {
  const end = dayjs()
  return [end.subtract(24, 'hour').format('YYYY-MM-DD HH:mm:ss'), end.format('YYYY-MM-DD HH:mm:ss')]
}

const dateRange = ref(defaultRange())

/** 时间快捷项：让“近 24h / 近 7 天 / 近 30 天”一按即达，也顺带说明 30 天就是上限 */
const rangeShortcuts = [
  {
    text: '近 1 小时',
    value: () => {
      const end = dayjs()
      return [end.subtract(1, 'hour').toDate(), end.toDate()]
    }
  },
  {
    text: '近 24 小时',
    value: () => {
      const end = dayjs()
      return [end.subtract(24, 'hour').toDate(), end.toDate()]
    }
  },
  {
    text: '近 7 天',
    value: () => {
      const end = dayjs()
      return [end.subtract(7, 'day').toDate(), end.toDate()]
    }
  },
  {
    text: '近 30 天',
    value: () => {
      const end = dayjs()
      return [end.subtract(30, 'day').toDate(), end.toDate()]
    }
  }
]

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 20,
    level: null,
    status: null,
    keyword: null,
    runId: null,
    requestId: null
  }
})
const { queryParams } = toRefs(data)

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------
/** 统一时间显示；无法解析时原样返回（后端 DATETIME(3) 可能带毫秒，dayjs 能直接吃） */
function formatTime(value) {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : String(value)
}

/** 毫秒耗时：不足 1 秒显示 ms（单条日志多是几百毫秒，换算成 0.3s 反而难读） */
function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'
}

/**
 * 级别 → el-tag 配色。
 * 不认识的级别给灰色 'info'（不是主色）：日志里出现未知级别本身就是要被注意的异常信号。
 */
function levelTag(value) {
  const key = String(value || '').toUpperCase()
  if (key === 'ERROR' || key === 'CRITICAL' || key === 'FATAL') return 'danger'
  if (key === 'WARNING' || key === 'WARN') return 'warning'
  if (key === 'INFO') return ''
  return 'info'
}

function sceneLabel(value) {
  if (!value) return '—'
  const hit = SCENES[value]
  return hit ? hit.label : String(value)
}

function sceneTag(value) {
  const hit = value ? SCENES[value] : null
  return hit ? hit.tag : 'info'
}

function statusLabel(value) {
  if (!value) return '—'
  const hit = STATUSES.filter((item) => item.value === value)[0]
  return hit ? hit.label : String(value)
}

function statusTag(value) {
  const map = { ok: 'success', error: 'danger', timeout: 'warning', skipped: 'info' }
  return map[value] || 'info'
}

/** 长 ID（runId/conversationId）只显示后 8 位，全量值在展开详情里可看 */
function shortId(value) {
  if (!value) return '—'
  const text = String(value)
  return text.length > 8 ? text.slice(-8) : text
}

// ---------------------------------------------------------------------------
// 列表
// ---------------------------------------------------------------------------

/**
 * 时间范围校验（前端限制最大 30 天）。
 *
 * 为什么在前端先拦：后端超窗会拒绝/夹取，运营看到的只是“查不到”，无法区分
 * “真的没有日志”和“时间窗超限被拒”。先在这里给出明确原因，排查方向就不会跑偏。
 */
function validateRange() {
  const range = dateRange.value
  if (!range || range.length !== 2 || !range[0] || !range[1]) {
    ElMessage.warning('请选择时间范围（默认近 24 小时，最长 30 天）')
    return false
  }
  const start = dayjs(range[0]).valueOf()
  const end = dayjs(range[1]).valueOf()
  if (Number.isNaN(start) || Number.isNaN(end)) {
    ElMessage.warning('时间范围格式不正确，请重新选择')
    return false
  }
  if (start > end) {
    ElMessage.warning('开始时间不能晚于结束时间')
    return false
  }
  if (end - start > MAX_RANGE_MS) {
    ElMessage.warning(
      '时间范围最大 30 天（当前约 ' + Math.ceil((end - start) / (24 * 60 * 60 * 1000)) + ' 天），请缩小后重试'
    )
    return false
  }
  return true
}

/**
 * 组装查询参数。
 *
 * 空串一律不传（而不是传 `level=`）：后端把空串当“已填写但为空”时可能拼出
 * `level = ''` 这类恒假条件，表现为“搜不到任何日志”；null/undefined 则不会被序列化。
 */
function buildQuery() {
  const query = {
    pageNum: queryParams.value.pageNum,
    pageSize: queryParams.value.pageSize,
    beginTime: dateRange.value[0],
    endTime: dateRange.value[1]
  }
  const optional = ['level', 'status', 'keyword', 'runId', 'requestId']
  optional.forEach((key) => {
    const value = queryParams.value[key]
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      query[key] = String(value).trim()
    }
  })
  return query
}

/** 查询列表（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  if (!validateRange()) return
  loading.value = true
  loadError.value = ''
  listAgentLog(buildQuery())
    .then((response) => {
      logList.value = response.rows || []
      total.value = response.total || 0
    })
    .catch((error) => {
      // 拦截器已提示传输/业务错误；这里保留原因给失败态，并清空旧数据
      //（否则会把上一次的结果当成这一次的，误导排障）
      loadError.value = (error && error.message) || '网络异常'
      logList.value = []
      total.value = 0
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
  if (queryRef.value) queryRef.value.resetFields()
  dateRange.value = defaultRange()
  // 路由带来的 requestId/runId 也要清掉，否则“重置”后仍被上一次跳转的条件限制
  queryParams.value.requestId = null
  queryParams.value.runId = null
  // 同步把跳转参数从 URL 上摘掉：否则离开再回来（keep-alive 复用）会被旧参数重新套上筛选，
  // 用户看到的“重置”就不算真重置了。用 replace 不新增历史记录。
  if (route.query.requestId || route.query.runId) {
    const nextQuery = Object.assign({}, route.query)
    delete nextQuery.requestId
    delete nextQuery.runId
    router.replace({ path: route.path, query: nextQuery })
    appliedRouteKey = '|'
  }
  handleQuery()
}

// ---------------------------------------------------------------------------
// 跨页跳转（不硬编码路由）
// ---------------------------------------------------------------------------

/**
 * 在当前已注册路由里按菜单 path 后缀找目标页。
 *
 * 为什么这么做：RuoYi 的动态路由由 sys_menu 下发（AGENTS §2.2），本页只知道自己
 * “来自哪个菜单”，不该知道兄弟菜单的完整 URL。按后缀（菜单 path）查找，
 * 菜单挂在哪个父目录下都能命中；找不到说明该环境没执行对应菜单 SQL，明确提示即可。
 */
function findMenuRoute(suffix) {
  const records = router.getRoutes()
  return records.filter((item) => typeof item.path === 'string' && item.path.endsWith('/' + suffix))[0] || null
}

/** 关联运行：跳运行时间线页并带上追踪号（方案 §6：`agentRun` 页带 requestId 参数） */
function goRun(row) {
  const hit = findMenuRoute('agentRun')
  if (!hit) {
    ElMessage.warning('未找到「运行时间线」页面：请确认菜单 9010 已执行（docs/ddl/agent_run_menu.sql）并重新登录')
    return
  }
  // 只带非空参数：URL 里出现 `requestId=` 空值会让目标页误以为“要用空条件过滤”
  const query = {}
  if (row.requestId) query.requestId = row.requestId
  if (row.runId) query.runId = row.runId
  router.push({ path: hit.path, query })
}

/** 会话：跳会话列表页并直接打开该会话详情（S13 读 route.query.conversationId） */
function goConversation(row) {
  const hit = findMenuRoute('agentConversation')
  if (!hit) {
    ElMessage.warning('未找到「会话列表」页面：请确认菜单 9018 已执行（docs/ddl/agent_log_menu.sql）并重新登录')
    return
  }
  router.push({ path: hit.path, query: { conversationId: row.conversationId } })
}

// ---------------------------------------------------------------------------
// 路由参数（互跳入口）
// ---------------------------------------------------------------------------

/**
 * 读取路由 query 预填筛选条件。
 *
 * 用途：会话详情里的「查日志」用 requestId 跳过来，或运行页用 runId 跳过来。
 * 返回是否发生变化，供 onActivated 判断要不要重新查询。
 *
 * 只有**路由确实带了跳转参数**时才改动筛选条件：否则 keep-alive 复用页面时，
 * 用户手动输入的筛选会被“空 route.query”悄悄清掉（看起来像页面自己重置了）。
 */
let appliedRouteKey = ''
function applyRouteQuery() {
  const rid = route.query.requestId
  const runId = route.query.runId
  const nextRid = rid === undefined || rid === null || rid === '' ? null : String(rid)
  const nextRunId = runId === undefined || runId === null || runId === '' ? null : String(runId)
  const key = (nextRid || '') + '|' + (nextRunId || '')
  if (key === '|') {
    appliedRouteKey = key
    return false
  }
  if (key === appliedRouteKey) return false
  appliedRouteKey = key
  queryParams.value.requestId = nextRid
  queryParams.value.runId = nextRunId
  return true
}

onMounted(() => {
  applyRouteQuery()
  getList()
})

/**
 * keep-alive 复用页面时 onMounted 不会再触发（tagsView 缓存本页）：
 * 若从会话页再跳回来，路由 query 变了但页面还是旧筛选条件，「互跳」等于没跳。
 * 这里在重新激活时补读一次路由参数，只有真的变化才重查（避免无谓请求）。
 */
onActivated(() => {
  if (applyRouteQuery()) {
    queryParams.value.pageNum = 1
    getList()
  }
})
</script>

<style lang="scss" scoped>
/* 页面内没有 append-to-body 弹窗（详情是行内展开），样式裹在页面根类下也安全；
   但 .dim/.mono 等由多个块复用，仍写成顶层，避免将来加抽屉时踩“teleport 后祖先不在路径上”的坑 */

.list-wrap {
  min-height: 200px;
}

.list-skeleton {
  padding: 8px 0;
}

.log-detail {
  padding: 4px 12px 8px;

  .detail-block {
    margin-top: 12px;

    &__title {
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 6px;
    }

    &__body {
      max-height: 240px;
      overflow: auto;
      margin: 0;
      padding: 10px;
      background: var(--el-fill-color-light);
      border-radius: 4px;
      font-size: 12px;
      line-height: 1.6;
      /* 日志正文是长文本：必须保留换行并强制换行，否则会渲染成一条横向长行 */
      white-space: pre-wrap;
      word-break: break-all;
    }

    &__body--err {
      color: var(--el-color-danger);
    }
  }
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 4px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}
</style>
