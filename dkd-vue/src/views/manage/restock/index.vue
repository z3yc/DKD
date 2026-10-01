<template>
  <div class="app-container restock-page">
    <div class="page-head">
      <div class="page-head__title">智能补货工作台</div>
      <div class="page-head__sub">
        Agent 每日 06:00 自动分析生成建议清单 —— 你只需处理异常、确认建单
      </div>
    </div>

    <!-- 原型 V2 的三步引导条 -->
    <div class="steps">
      <span class="on"><b>1</b>查看建议清单</span>
      <span><b>2</b>调整或跳过异常项</span>
      <span><b>3</b>确认创建工单</span>
    </div>

    <el-row :gutter="12" class="stats">
      <el-col :span="6">
        <el-card shadow="never" class="stat">
          <div class="label">待处理建议</div>
          <div class="value">{{ stats.pending }}</div>
          <div class="delta" :class="{ warn: stats.urgent > 0 }">
            {{ stats.urgent > 0 ? `其中紧急 ${stats.urgent} 条` : '无紧急项，可放心批量确认' }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat">
          <div class="label">待处理建议总量</div>
          <div class="value">{{ stats.totalQuantity }}<small> 件</small></div>
          <div class="delta">当前清单合计补货数量</div>
        </el-card>
      </el-col>
      <!--
        ⚠️ 下面两张卡是原型 V2 的「预测 7 日缺货率 / 上期建议准确率」。
        后端 `/agent/restock/plans` 目前**没有**这两项数据源（统计只返回
        pending / urgent / totalQuantity），因此这里明确标注「待接入」而不是填演示数字：
        验收项里要能看出哪些数字是真的，编一个 8.2% 会让 M2 验收失去意义。
        数据来源：1-14 规则版双跑对比、3-6 补货复盘自动化。
      -->
      <el-col :span="6">
        <el-card shadow="never" class="stat stat--todo">
          <div class="label">预测 7 日缺货率</div>
          <div class="value">—</div>
          <div class="delta">待接入（1-14 双跑对比）</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat stat--todo">
          <div class="label">上期建议准确率</div>
          <div class="value">—</div>
          <div class="delta">待接入（3-6 复盘自动化）</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="main-card">
      <div class="plan-meta">
        <span>计划日期 <b>{{ planDate || '今日' }}</b></span>
        <span class="dim">共 {{ plans.length }} 台设备 · 生成于当日 06:00 定时分析</span>
        <div class="plan-actions">
          <el-date-picker
            v-model="queryDate"
            type="date"
            size="small"
            value-format="YYYY-MM-DD"
            placeholder="查看其他日期"
            :clearable="false"
            style="width: 150px"
            @change="getList"
          />
          <el-button size="small" :icon="Refresh" :loading="loading" @click="getList">刷新</el-button>
          <el-button
            v-if="!pause.paused"
            size="small"
            type="warning"
            plain
            @click="openPauseDialog"
          >暂停自动分析</el-button>
          <el-button size="small" type="primary" :loading="confirmAllLoading" :disabled="!pendingPlans.length" @click="handleConfirmAll">
            全部确认建单（{{ pendingPlans.length }}）
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="pause.paused"
        class="pause-banner"
        type="warning"
        :closable="false"
        show-icon
      >
        <template #title>
          <span>已暂停自动补货分析：明日 06:00 不再生成新建议，当前清单仍可继续处理。</span>
          <el-button size="small" text type="primary" style="margin-left: 12px" @click="openResumeDialog">恢复自动分析</el-button>
        </template>
        <template v-if="pause.reason" #default>
          <span class="dim">暂停原因：{{ pause.reason }}（操作人 {{ pause.paused_by || '-' }}）</span>
        </template>
      </el-alert>

      <div class="fchips">
        <span class="fchips__label">优先级</span>
        <el-button
          v-for="chip in PRIORITY_CHIPS"
          :key="chip.value"
          size="small"
          :type="priorityFilter === chip.value ? 'primary' : 'default'"
          :plain="priorityFilter !== chip.value"
          @click="priorityFilter = chip.value"
        >{{ chip.label }}</el-button>
        <span class="fchips__label" style="margin-left: 12px">状态</span>
        <el-button
          v-for="chip in STATUS_CHIPS"
          :key="chip.value"
          size="small"
          :type="statusFilter === chip.value ? 'primary' : 'default'"
          :plain="statusFilter !== chip.value"
          @click="statusFilter = chip.value"
        >{{ chip.label }}</el-button>
      </div>

      <el-table
        v-loading="loading"
        :data="filteredPlans"
        row-key="id"
        border
        size="small"
        empty-text="当日暂无补货计划（若已暂停自动分析，明日 06:00 不会生成新建议）"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="detail-wrap">
              <el-table :data="row.items" size="small" border>
                <el-table-column label="商品" prop="skuName" min-width="140">
                  <template #default="{ row: item }">{{ item.skuName || '（未命名商品）' }}</template>
                </el-table-column>
                <el-table-column label="货道" prop="channelCode" width="80" />
                <el-table-column label="库存水位" width="160">
                  <template #default="{ row: item }">
                    {{ item.currentQuantity }}/{{ item.maxCapacity }}
                    <el-progress
                      :percentage="waterLevel(item)"
                      :stroke-width="6"
                      :show-text="false"
                      :status="waterLevel(item) <= 20 ? 'exception' : undefined"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="建议补货" prop="suggestedQuantity" width="90" />
                <el-table-column label="预计可撑" width="90">
                  <template #default="{ row: item }">{{ item.estimatedDays }} 天</template>
                </el-table-column>
                <el-table-column label="优先级" width="80">
                  <template #default="{ row: item }">
                    <el-tag :type="priorityTagType(item.priority)" size="small">{{ priorityLabel(item.priority) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="Agent 依据" prop="reason" min-width="260">
                  <template #default="{ row: item }">
                    <div class="reason-text">{{ item.reason || '（无依据说明，建议先人工核实再建单）' }}</div>
                  </template>
                </el-table-column>
              </el-table>
              <div v-if="row.assigneeName" class="dim" style="margin-top: 6px">
                接单人：{{ row.assigneeName }}{{ row.assigneeId ? '（ID ' + row.assigneeId + '）' : '' }}
              </div>
              <div v-else class="dim" style="margin-top: 6px">
                接单人：待指派（未匹配到同区域可接单的运维人员）
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="设备 / 点位" min-width="140">
          <template #default="{ row }">
            <div>{{ row.innerCode }}</div>
            <!--
              ⚠️ 契约未提供点位名/地址（`summarize_plan` 只有 innerCode/vmId），
              因此这里只显示设备编号 + vmId，**不拼任何伪造的点位名**。
              若要显示点位，需后端扩字段（涉及契约变更，须走兼容性评审）。
            -->
            <div class="dim">设备 ID {{ row.vmId }}</div>
          </template>
        </el-table-column>

        <el-table-column label="建议明细" min-width="180">
          <template #default="{ row }">
            <span>{{ row.skuCount }} 个货道</span>
            <span class="dim">· 合计 +{{ row.totalQuantity }}</span>
            <div class="dim">展开可见「Agent 依据」</div>
          </template>
        </el-table-column>

        <el-table-column label="最低水位" width="150">
          <template #default="{ row }">
            <span>{{ minWaterLevel(row) }}%</span>
            <el-progress
              :percentage="minWaterLevel(row)"
              :stroke-width="6"
              :show-text="false"
              :status="minWaterLevel(row) <= 20 ? 'exception' : undefined"
            />
          </template>
        </el-table-column>

        <el-table-column label="建议补货" width="100">
          <template #default="{ row }">+{{ row.totalQuantity }}</template>
        </el-table-column>

        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
            <div v-if="row.taskCode" class="dim">工单 {{ row.taskCode }}</div>
            <div v-if="row.adjustReason" class="dim" :title="row.adjustReason">原因：{{ row.adjustReason }}</div>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="230" align="right" fixed="right">
          <template #default="{ row }">
            <template v-if="isPending(row.status)">
              <el-button size="small" type="primary" @click="handleConfirm(row)">确认建单</el-button>
              <el-button size="small" @click="openAdjustDialog(row)">调整</el-button>
              <el-button size="small" type="danger" plain @click="openSkipDialog(row)">跳过</el-button>
            </template>
            <template v-else-if="row.status === STATUS.SKIPPED">
              <el-button size="small" type="primary" plain @click="openRestoreDialog(row)">恢复建议</el-button>
            </template>
            <template v-else-if="row.status === STATUS.ORDERING">
              <span class="dim">建单处理中，请稍候刷新</span>
            </template>
            <template v-else>
              <span class="dim">{{ row.status === STATUS.ORDERED ? '已建单' : '已复盘' }}</span>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <div class="page-tip">
        点击行左侧箭头可展开各货道的「Agent 依据」；所有人工调整、跳过操作均会写入决策留痕并反馈复盘模型。
      </div>
    </el-card>

    <RestockAdjustDialog
      v-model="adjust.visible"
      :plan="adjust.plan"
      :loading="adjust.loading"
      @confirm="submitAdjust"
    />
    <RestockReasonDialog
      v-model="reasonDialog.visible"
      :title="reasonDialog.title"
      :description="reasonDialog.description"
      :reason-label="reasonDialog.reasonLabel"
      :presets="reasonDialog.presets"
      :confirm-text="reasonDialog.confirmText"
      :loading="reasonDialog.loading"
      @confirm="submitReason"
    />
  </div>
</template>

<script setup name="Restock">
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  adjustRestockPlan,
  confirmRestockPlan,
  getRestockPauseState,
  listRestockPlans,
  restoreRestockPlan,
  setRestockPause,
  skipRestockPlan
} from '@/api/manage/restock'
import RestockAdjustDialog from './components/RestockAdjustDialog.vue'
import RestockReasonDialog from './components/RestockReasonDialog.vue'

/**
 * 智能补货工作台（排期 1-10 / 1-11；后端 1-7）。
 *
 * 数据来源：Java 网关 `/agent/restock/**` → Python dkd-agent。页面本身不做任何计算，
 * 建议量/状态/依据全部来自后端（避免前端算一份、后端算一份的“两个真相”）。
 *
 * 两条明确的「不造假」纪律（M2 验收要求数字可核对）：
 *   1. 统计卡里没有数据源的指标留「—」并标注来源，不填演示数字；
 *   2. 契约里没有的字段（点位名）不拼假值，只显示 innerCode。
 *
 * 后端状态码语义（决定这里怎么提示，不能一律说“操作失败”）：
 *   409 = 状态机拒绝/并发冲突（如「已建单的计划不能重复确认」）
 *   400 = 业务规则拒绝（如「调整必须填写原因」「服务水平越界」）
 *   422 = 字段校验    502 = 回调 Java 建单失败    503 = 补货服务未初始化
 * 这些文案全部来自后端 `msg`，由 `api/manage/restock.js` 原样带出（见该文件头注释）。
 */

/** 与后端 `restock_state.py::PLAN_STATUS_*` 逐字对齐（三端只在这里映射一次，避免标签漂移） */
const STATUS = {
  SUGGESTED: 1,
  ADJUSTED: 2,
  SKIPPED: 3,
  ORDERED: 4,
  REVIEWED: 5,
  UNASSIGNED: 6,
  ORDERING: 7
}

const PRIORITY_CHIPS = [
  { label: '全部', value: 'all' },
  { label: '紧急', value: 4 },
  { label: '高', value: 3 },
  { label: '中', value: 2 },
  { label: '低', value: 1 }
]

const STATUS_CHIPS = [
  { label: '全部', value: 'all' },
  { label: '待处理', value: 'pending' },
  { label: '已跳过', value: STATUS.SKIPPED },
  { label: '已建单', value: STATUS.ORDERED },
  { label: '已复盘', value: STATUS.REVIEWED }
]

const loading = ref(false)
const confirmAllLoading = ref(false)
const planDate = ref('')
const queryDate = ref('')
const plans = ref([])
const stats = reactive({ pending: 0, urgent: 0, totalQuantity: 0 })
const pause = reactive({ paused: false, reason: '', paused_by: '', paused_time: null })

const priorityFilter = ref('all')
const statusFilter = ref('all')

const adjust = reactive({ visible: false, loading: false, plan: null })
const reasonDialog = reactive({
  visible: false,
  loading: false,
  title: '',
  description: '',
  reasonLabel: '原因',
  presets: [],
  confirmText: '确认',
  onConfirm: null
})

/** 在途列表请求的中止句柄（AGENTS §2.2：长请求必须可取消，避免切页面后回写状态） */
let abortController = null

const pendingPlans = computed(() => plans.value.filter((p) => isPending(p.status)))

const filteredPlans = computed(() =>
  plans.value.filter((plan) => {
    if (statusFilter.value === 'pending') {
      if (!isPending(plan.status)) return false
    } else if (statusFilter.value !== 'all' && plan.status !== statusFilter.value) {
      return false
    }
    if (priorityFilter.value === 'all') return true
    // 计划是「一台设备多条货道」，优先级按该计划内的**最高优先级**过滤
    // （按单条货道过滤会让“设备行”含义变得说不清）
    return maxPriority(plan) === priorityFilter.value
  })
)

onMounted(() => {
  getList()
  loadPauseState()
})

onBeforeUnmount(() => {
  if (abortController) abortController.abort()
})

function isPending(status) {
  return status === STATUS.SUGGESTED || status === STATUS.ADJUSTED || status === STATUS.UNASSIGNED
}

function maxPriority(plan) {
  return (plan.items || []).reduce((max, item) => Math.max(max, item.priority || 1), 0)
}

function priorityLabel(priority) {
  return { 4: '紧急', 3: '高', 2: '中', 1: '低' }[priority] || '低'
}

function priorityTagType(priority) {
  return { 4: 'danger', 3: 'warning', 2: 'primary', 1: 'info' }[priority] || 'info'
}

function statusLabel(status) {
  return (
    {
      [STATUS.SUGGESTED]: '建议',
      [STATUS.ADJUSTED]: '已调整',
      [STATUS.SKIPPED]: '已跳过',
      [STATUS.ORDERED]: '已建单',
      [STATUS.REVIEWED]: '已复盘',
      [STATUS.UNASSIGNED]: '待指派',
      [STATUS.ORDERING]: '建单中'
    }[status] || String(status)
  )
}

function statusTagType(status) {
  return (
    {
      [STATUS.SUGGESTED]: 'primary',
      [STATUS.ADJUSTED]: 'warning',
      [STATUS.SKIPPED]: 'info',
      [STATUS.ORDERED]: 'success',
      [STATUS.REVIEWED]: 'success',
      [STATUS.UNASSIGNED]: 'warning',
      [STATUS.ORDERING]: 'warning'
    }[status] || 'info'
  )
}

function waterLevel(item) {
  if (!item.maxCapacity) return 0
  return Math.min(100, Math.round((item.currentQuantity / item.maxCapacity) * 100))
}

function minWaterLevel(plan) {
  const items = plan.items || []
  if (!items.length) return 0
  return items.reduce((min, item) => Math.min(min, waterLevel(item)), 100)
}

/** 取清单（可取消；loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  if (abortController) abortController.abort()
  abortController = new AbortController()

  loading.value = true
  listRestockPlans(queryDate.value || undefined, abortController.signal)
    .then((res) => {
      if (res.aborted) return
      if (!res.ok) {
        // 503 = 补货服务未初始化（通常是 DB 未连上或 lifespan 装配失败），
        // 这类问题靠“操作失败”四个字是查不出来的，所以单独给一句人话
        ElMessage.error(
          res.code === 503 ? '补货服务未初始化，请确认智能体服务与数据库连接正常' : res.msg
        )
        plans.value = []
        Object.assign(stats, { pending: 0, urgent: 0, totalQuantity: 0 })
        return
      }
      const data = res.data || {}
      planDate.value = data.planDate || ''
      plans.value = data.plans || []
      Object.assign(stats, data.stats || { pending: 0, urgent: 0, totalQuantity: 0 })
    })
    .finally(() => {
      loading.value = false
    })
}

function loadPauseState() {
  getRestockPauseState().then((res) => {
    if (!res.ok) return
    const data = res.data || {}
    // 后端 `model_dump(mode="json")` 未走 alias → 键是 snake_case
    pause.paused = !!data.paused
    pause.reason = data.reason || ''
    pause.paused_by = data.paused_by || ''
    pause.paused_time = data.paused_time || null
  })
}

function handleConfirm(plan) {
  ElMessageBox.confirm(
    `确认按建议量创建补货工单？设备 ${plan.innerCode} · 合计 ${plan.totalQuantity} 件。`,
    '确认建单',
    { type: 'info', confirmButtonText: '确认建单', cancelButtonText: '取消' }
  )
    .then(() => confirmOne(plan))
    .catch(() => {})
}

/**
 * 单条确认（幂等）。
 *
 * ⚠️ 失败时**不能提示“操作失败”**：后端把不同原因分得很清，且已经写了留痕，
 * 原样透传 `msg` 运营才知道下一步该做什么（如“已建单”说明无需重试）。
 * 建单失败（502）时后端**不回退计划状态**，因此这里明确告诉运营“可再次点击确认重试”
 * ——对应排期 FIX-15（重试路径原本只有隐式实现，没有文案说明）。
 */
function confirmOne(plan) {
  return confirmRestockPlan(plan.id, queryDate.value || undefined).then((res) => {
    if (res.ok) {
      ElMessage.success(`已创建工单（设备 ${plan.innerCode}）`)
      getList()
    } else if (res.code === 502) {
      ElMessage.error(`建单失败：${res.msg}。计划状态未变更，可修正后再次点击「确认建单」重试。`)
      getList()
    } else {
      ElMessage.error(res.msg)
      getList()
    }
    return res
  })
}

/**
 * 全部确认建单。
 *
 * 后端**故意不提供批量端点**（`api/restock.py` 模块 docstring：批量端点会把
 * “一台失败是否整批回滚”变成没人说得清的问题），因此这里逐条串行调用并汇总结果——
 * 关键在于**如实报告部分成功**：失败的具体哪台设备要说清，且不清空已成功的状态。
 */
function handleConfirmAll() {
  const targets = pendingPlans.value.slice()
  if (!targets.length) {
    ElMessage.info('当前没有待处理的建议')
    return
  }
  ElMessageBox.confirm(
    `将逐条确认 ${targets.length} 台设备的补货建议并创建工单；某台失败不影响其余，结果会逐条汇总。`,
    '全部确认建单',
    { type: 'warning', confirmButtonText: '开始建单', cancelButtonText: '取消' }
  )
    .then(async () => {
      confirmAllLoading.value = true
      const failed = []
      try {
        // 串行而非并发：避免同一时刻对多个设备发起回调，把失败原因混成一团
        for (const plan of targets) {
          const res = await confirmOne(plan)
          if (!res.ok) failed.push(`${plan.innerCode}：${res.msg}`)
        }
      } finally {
        confirmAllLoading.value = false
        getList()
      }
      if (failed.length) {
        ElMessageBox.alert(failed.join('\n'), `批量建单未全部成功（成功 ${targets.length - failed.length} / 共 ${targets.length}）`, {
          type: 'warning'
        })
      } else {
        ElMessage.success(`已全部创建工单（${targets.length} 台设备）`)
      }
    })
    .catch(() => {})
}

function openAdjustDialog(plan) {
  adjust.plan = plan
  adjust.visible = true
}

function submitAdjust(payload) {
  const plan = adjust.plan
  if (!plan) return
  adjust.loading = true
  adjustRestockPlan(plan.id, payload, queryDate.value || undefined)
    .then((res) => {
      adjust.loading = false
      if (res.ok) {
        adjust.visible = false
        ElMessage.success('调整已保存（原因已写入决策留痕）')
        getList()
      } else {
        // 400 是业务规则拒绝（原因留空 / 服务水平越界 / 两种模式同时给），原样展示
        ElMessage.error(res.msg)
      }
    })
    .finally(() => {
      // loading 兜底复位：中间异常也不能让按钮永久转圈
      adjust.loading = false
    })
}

function openSkipDialog(plan) {
  openReasonDialog({
    title: '跳过该条补货建议',
    description: `确定跳过：${plan.innerCode}（合计 +${plan.totalQuantity} 件）？跳过为终态，当日可通过「恢复建议」撤销。`,
    reasonLabel: '跳过原因',
    presets: ['现场库存实际充足', '销量预估偏高', '设备即将撤机', '促销计划取消', '其他'],
    confirmText: '确认跳过',
    onConfirm: (reason) => skipRestockPlan(plan.id, reason, queryDate.value || undefined)
  })
}

function openRestoreDialog(plan) {
  openReasonDialog({
    title: '恢复该条补货建议',
    description: `把 ${plan.innerCode} 从「已跳过」恢复为待处理建议。注意：仅限当天，跨日的计划需等次日分析重新生成。`,
    reasonLabel: '恢复原因',
    presets: ['误点跳过', '现场情况变化', '盘点后需重新补货', '其他'],
    confirmText: '恢复建议',
    onConfirm: (reason) => restoreRestockPlan(plan.id, reason, queryDate.value || undefined)
  })
}

function openPauseDialog() {
  openReasonDialog({
    title: '暂停自动补货分析',
    description:
      '暂停后：明日 06:00 不再自动生成新补货建议；当前清单仍可正常调整、确认建单；复盘与策略迭代同步暂停。适合场景：盘点期、大促前人工统一排单、数据异常排查。',
    reasonLabel: '暂停原因',
    presets: ['盘点期', '大促前人工统一排单', '数据异常排查', '其他'],
    confirmText: '确认暂停',
    onConfirm: (reason) => setRestockPause(true, reason)
  })
}

function openResumeDialog() {
  openReasonDialog({
    title: '恢复自动补货分析',
    description: '恢复后，次日 06:00 将重新生成补货建议。',
    reasonLabel: '恢复原因',
    presets: ['盘点结束', '大促排单完成', '数据已核实', '其他'],
    confirmText: '确认恢复',
    onConfirm: (reason) => setRestockPause(false, reason)
  })
}

function openReasonDialog(config) {
  reasonDialog.title = config.title
  reasonDialog.description = config.description || ''
  reasonDialog.reasonLabel = config.reasonLabel || '原因'
  reasonDialog.presets = config.presets || []
  reasonDialog.confirmText = config.confirmText || '确认'
  reasonDialog.onConfirm = config.onConfirm
  reasonDialog.visible = true
}

function submitReason(reason) {
  const handler = reasonDialog.onConfirm
  if (!handler) return
  reasonDialog.loading = true
  handler(reason)
    .then((res) => {
      if (res && res.ok) {
        reasonDialog.visible = false
        ElMessage.success('操作成功')
        getList()
        loadPauseState()
      } else {
        ElMessage.error((res && res.msg) || '操作失败')
      }
    })
    .finally(() => {
      reasonDialog.loading = false
    })
}
</script>

<style lang="scss" scoped>
.restock-page {
  .page-head {
    margin-bottom: 14px;

    &__title {
      font-size: 20px;
      font-weight: 600;
      color: var(--el-text-color-primary);
    }

    &__sub {
      color: var(--el-text-color-secondary);
      font-size: 13px;
      margin-top: 4px;
    }
  }

  .steps {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
    flex-wrap: wrap;

    span {
      font-size: 12px;
      color: var(--el-text-color-secondary);
      background: var(--el-fill-color-light);
      border-radius: 14px;
      padding: 4px 12px;

      b {
        display: inline-block;
        width: 16px;
        height: 16px;
        line-height: 16px;
        text-align: center;
        border-radius: 50%;
        background: var(--el-border-color);
        color: #fff;
        font-size: 11px;
        margin-right: 4px;
      }

      &.on {
        color: var(--el-color-primary);
        background: var(--el-color-primary-light-9);

        b {
          background: var(--el-color-primary);
        }
      }
    }
  }

  .stats {
    margin-bottom: 14px;

    .stat {
      .label {
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }

      .value {
        font-size: 26px;
        font-weight: 600;
        line-height: 1.4;

        small {
          font-size: 13px;
          font-weight: 400;
        }
      }

      .delta {
        font-size: 12px;
        color: var(--el-text-color-secondary);

        &.warn {
          color: var(--el-color-danger);
        }
      }

      // 无数据源的指标：视觉上要能被一眼认出「这不是真实数字」
      &--todo {
        .value {
          color: var(--el-text-color-placeholder);
        }
      }
    }
  }

  .plan-meta {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 10px;

    .plan-actions {
      margin-left: auto;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
  }

  .pause-banner {
    margin-bottom: 12px;
  }

  .fchips {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 10px;

    &__label {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }

  .page-tip {
    margin-top: 10px;
    font-size: 12px;
    line-height: 1.8;
    color: var(--el-text-color-secondary);
  }

  .reason-text {
    font-size: 12px;
    line-height: 1.7;
    color: var(--el-text-color-regular);
    white-space: pre-wrap;
  }

  .dim {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }

  .detail-wrap {
    padding: 10px 16px 12px 48px;
  }
}
</style>
