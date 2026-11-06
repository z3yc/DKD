<template>
  <div class="eval-panel">
    <div class="eval-panel__head">
      <span class="eval-panel__title">{{ panelTitle }}</span>
      <el-button link type="primary" :loading="loading" @click="load">刷新</el-button>
    </div>

    <!-- 三态：加载 / 失败可重试 / 无报告（“无报告”必须单独说明，不能画成“通过”） -->
    <el-skeleton v-if="loading" :rows="3" animated />

    <el-empty v-else-if="failed" :image-size="60" :description="'评测报告加载失败：' + failMsg">
      <el-button type="primary" @click="load">重试</el-button>
    </el-empty>

    <template v-else-if="reportRows.length === 0">
      <el-empty :image-size="60" :description="emptyText" />
      <p class="eval-note">
        未运行评测 ≠ 通过：保存护栏开启时，没有可用报告会被拦截；关闭护栏时可以保存，但没有质量证据。
        「被护栏拦截（blocked）」也不等于通过。
      </p>
    </template>

    <template v-else>
      <el-table :data="reportRows" border size="small" max-height="300">
        <el-table-column label="版本" width="88" align="center">
          <template #default="{ row }">v{{ row.configVersion ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="模式" width="100" align="center">
          <template #default="{ row }">{{ row.mode || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130" align="center">
          <template #default="{ row }">
            <!-- blocked=被护栏拦截：绝不能显示成“通过”，否则会把“被拦下”读成“质量达标” -->
            <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="准确率" width="100" align="center">
          <template #default="{ row }">{{ accuracyText(row.accuracy) }}</template>
        </el-table-column>
        <el-table-column label="通过/总数" width="110" align="center">
          <template #default="{ row }">{{ passText(row) }}</template>
        </el-table-column>
        <el-table-column label="基线" min-width="150">
          <template #default="{ row }">{{ baselineText(row) }}</template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ row.createTime || '—' }}</template>
        </el-table-column>
        <el-table-column label="执行人" width="110">
          <template #default="{ row }">{{ row.runBy || '—' }}</template>
        </el-table-column>
      </el-table>
      <p class="eval-note">
        报告来自 5-1 评测流水线：scored=已评分、blocked=被护栏拦截（不得视为通过）、failed=评测失败；
        未设基线表示尚未建立对照，准确率没有“相对基线”的口径。
      </p>
    </template>
  </div>
</template>

<script setup name="EvalReportPanel">
import { listEvalReports } from '@/api/manage/agentConfig'

/**
 * 评测报告面板（5-1），编辑抽屉与版本历史抽屉共用。
 *
 * 为什么独立成组件：两处抽屉都要看同一份数据（按 configCode 拉、按 version 过滤），
 * 复制两份表格必然漂移——尤其状态文案（blocked 不能显示成通过）这种口径，只能有一处实现。
 *
 * 展示口径（5-1/5-3）：
 *  - status=blocked（被护栏拦截）**不得**显示为通过，用 danger 色；
 *  - 无报告显示「本版本暂无评测报告」并写明“未运行≠通过”；
 *  - baselineAccuracy 为空显示「未设基线」，不要拿 accuracy 顶上（那是编造对照）。
 */

const props = defineProps({
  /** 配置编码（唯一键）；为空时不发请求（新增态还没有 code） */
  configCode: {
    type: String,
    default: ''
  },
  /** 只看某个版本；null/undefined = 展示该配置全部版本（版本历史抽屉用） */
  configVersion: {
    type: [Number, String],
    default: null
  },
  /** 取最近多少条报告（后端 limit） */
  limit: {
    type: Number,
    default: 20
  },
  /** 无报告时的文案（编辑抽屉按版本过滤，版本历史看全量，两者语义不同） */
  emptyText: {
    type: String,
    default: '本版本暂无评测报告'
  }
})

const loading = ref(false)
const failed = ref(false)
const failMsg = ref('')
const rows = ref([])

const panelTitle = computed(() => {
  return props.configVersion === null || props.configVersion === undefined
    ? '评测报告（按版本）'
    : '评测报告（v' + props.configVersion + '）'
})

/** 按版本过滤：后端列表接口只支持 configCode/limit，版本过滤在前端做（limit 取够大即可） */
const reportRows = computed(() => {
  if (props.configVersion === null || props.configVersion === undefined) return rows.value
  const target = Number(props.configVersion)
  return rows.value.filter((item) => Number(item.configVersion) === target)
})

const STATUS_MAP = {
  scored: { label: '已评分', tag: 'success' },
  blocked: { label: '被护栏拦截', tag: 'danger' },
  failed: { label: '评测失败', tag: 'warning' }
}

function statusLabel(value) {
  if (value === null || value === undefined || value === '') return '—'
  const hit = STATUS_MAP[value]
  return hit ? hit.label : String(value)
}

function statusTag(value) {
  const hit = STATUS_MAP[value]
  return hit ? hit.tag : 'info'
}

/** 准确率 0~1 → 两位小数百分比；缺失「—」（不要显示成 0%，那等于宣称准确率 0） */
function accuracyText(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isNaN(num) ? String(value) : (num * 100).toFixed(2) + '%'
}

/** 通过/总数：任一侧缺失就用「—」，不拿 0 补位（0 通过是事实，缺失不是） */
function passText(row) {
  const passed = row.passed === null || row.passed === undefined || row.passed === '' ? '—' : row.passed
  const total = row.total === null || row.total === undefined || row.total === '' ? '—' : row.total
  return passed + ' / ' + total
}

/** 基线：未设基线就如实写，不用 accuracy 冒充（否则会让“没有对照”看起来像“达到基线”） */
function baselineText(row) {
  if (row.baselineAccuracy === null || row.baselineAccuracy === undefined || row.baselineAccuracy === '') {
    return '未设基线'
  }
  const base = accuracyText(row.baselineAccuracy)
  const n = row.baselineN
  return n === null || n === undefined || n === '' ? base : base + '（n=' + n + '）'
}

function normalizeRows(response) {
  if (Array.isArray(response)) return response
  if (response && Array.isArray(response.data)) return response.data
  if (response && Array.isArray(response.rows)) return response.rows
  return []
}

function timeMs(value) {
  if (!value) return 0
  const parsed = new Date(String(value).replace(/-/g, '/').replace('T', ' ')).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

/** 拉报告（loading 必须 finally 复位，AGENTS §2.2） */
function load() {
  if (!props.configCode) {
    rows.value = []
    return
  }
  loading.value = true
  failed.value = false
  failMsg.value = ''
  listEvalReports({ configCode: props.configCode, limit: props.limit })
    .then((response) => {
      // 后端已按时间倒序；这里再排一次，防止接口调整把“最新报告”埋到后面
      rows.value = normalizeRows(response).slice().sort((a, b) => timeMs(b.createTime) - timeMs(a.createTime))
    })
    .catch((error) => {
      rows.value = []
      failed.value = true
      failMsg.value = (error && error.message) || '网络异常'
    })
    .finally(() => {
      loading.value = false
    })
}

watch(
  [() => props.configCode, () => props.configVersion],
  () => load(),
  { immediate: true }
)
</script>

<style lang="scss" scoped>
.eval-panel {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  background: var(--el-bg-color);

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
  }
}

.eval-note {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}
</style>
