<template>
  <div class="app-container agent-dashboard" v-hasPermi="['manage:agentDashboard:list']">
    <!-- 页头：标题 + 时间窗 + 刷新（口径版本写在副标题，页面任何数字都能回溯到字典） -->
    <div class="page-head">
      <div>
        <div class="page-head__title">智能体运营驾驶舱</div>
        <div class="page-head__sub">
          近 {{ days }} 天 · 口径《智能体效果指标字典 V1》（n&lt;100 不作对外口径）
        </div>
      </div>
      <div class="page-head__actions">
        <el-radio-group v-model="days" size="small" @change="handleWindowChange">
          <el-radio-button :label="7">近 7 天</el-radio-button>
          <el-radio-button :label="14">近 14 天</el-radio-button>
          <el-radio-button :label="30">近 30 天</el-radio-button>
        </el-radio-group>
        <el-button size="small" icon="Refresh" :loading="loading" @click="getList">刷新</el-button>
      </div>
    </div>

    <!-- 三态①：加载骨架（整页聚合是一个请求，骨架比逐卡转圈更不容易让人误判某卡没数据） -->
    <el-skeleton v-if="loading" :rows="10" animated class="page-skeleton" />

    <!-- 三态②：加载失败（原因 + 重试；silent 请求没有拦截器 toast，这里必须持久展示） -->
    <el-empty v-else-if="loadError" :description="'驾驶舱数据加载失败：' + loadError">
      <el-button type="primary" @click="getList">重试</el-button>
    </el-empty>

    <!-- 三态③：窗口内确实没有数据（说明“没有数据”而不是“页面坏了”，并给出下一步动作） -->
    <el-empty
      v-else-if="isEmpty"
      description="当前时间窗内没有智能体运行数据：可切换时间窗，或确认定时分析任务是否在跑（06:00 生成补货建议）"
    >
      <el-button @click="getList">重新加载</el-button>
    </el-empty>

    <template v-else-if="overview">
      <!-- 告警条：只在后端返回告警时占位；level 决定配色（critical=danger / warning=warning） -->
      <div v-if="alerts.length > 0" class="alerts">
        <el-alert
          v-for="(alert, index) in alerts"
          :key="alert.code || index"
          :type="alertType(alert.level)"
          :closable="false"
          show-icon
          class="alert-item"
          :title="alert.msg || '（告警内容缺失）'"
        >
          <template v-if="alert.code" #default>
            <span class="dim">告警编码：{{ alert.code }}<span v-if="alert.level">（{{ alert.level }}）</span></span>
          </template>
        </el-alert>
      </div>

      <!-- 四卡：采纳 / 降级 / 成本 / 失败。每个数字都带 n 与口径 tooltip，不允许孤零零一个百分比 -->
      <el-row :gutter="12" class="cards">
        <el-col :xs="24" :sm="12" :lg="6">
          <el-card shadow="never" class="metric-card">
            <div class="metric-label">
              建议采纳率
              <el-tooltip :content="TIPS.adoption" placement="top">
                <span class="metric-help">口径</span>
              </el-tooltip>
            </div>
            <div class="metric-value">{{ percent(overview.adoption && overview.adoption.adoptionRate) }}</div>
            <div class="metric-meta">
              n={{ num(overview.adoption && overview.adoption.totalPlans) }} 单 ·
              已建单 {{ num(overview.adoption && overview.adoption.orderedPlans) }} ·
              调整 {{ num(overview.adoption && overview.adoption.adjustedPlans) }}
            </div>
            <div class="metric-meta">
              调整率 {{ percent(overview.adoption && overview.adoption.adjustRate) }}
            </div>
            <el-tag v-if="sampleTag(overview.adoption && overview.adoption.totalPlans)" size="small" :type="sampleTag(overview.adoption && overview.adoption.totalPlans).type" class="sample-tag">
              {{ sampleTag(overview.adoption && overview.adoption.totalPlans).text }}
            </el-tag>
          </el-card>
        </el-col>

        <el-col :xs="24" :sm="12" :lg="6">
          <el-card shadow="never" class="metric-card">
            <div class="metric-label">
              未校准率（降级）
              <el-tooltip :content="TIPS.degradation" placement="top">
                <span class="metric-help">口径</span>
              </el-tooltip>
            </div>
            <div class="metric-value metric-value--warn">
              {{ percent(overview.degradation && overview.degradation.uncalibratedRate) }}
            </div>
            <div class="metric-meta">
              未校准 {{ num(overview.degradation && overview.degradation.uncalibratedPlans) }} 单 ·
              已知标记 {{ num(degradationKnown) }} 单（分母）
            </div>
            <!-- unknown>0 时单独一行：它既不在分子也不在分母，混在一行里会被误读成“未知=未校准” -->
            <div v-if="degradationUnknown > 0" class="metric-meta metric-meta--warn">
              未知 {{ degradationUnknown }} 行（历史/未接入，不计入分母）
            </div>
            <!-- 数据源未就绪时按字典要求显示「—」并明说待哪个任务，不填演示数字 -->
            <div v-if="degradationMissing" class="metric-meta metric-meta--warn">数据源待 5-6：当前无法计算校准/降级率</div>
            <div class="metric-meta">
              纯统计基线可安全采用，但未经模型校准（5-6 徽标同口径）
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :sm="12" :lg="6">
          <el-card shadow="never" class="metric-card">
            <div class="metric-label">
              成本（token）
              <el-tooltip :content="TIPS.cost" placement="top">
                <span class="metric-help">口径</span>
              </el-tooltip>
            </div>
            <div class="metric-value">{{ num(overview.cost && overview.cost.tokensTotal) }}</div>
            <div class="metric-meta">
              调用 {{ num(overview.cost && overview.cost.calls) }} 次 ·
              平均 {{ num(overview.cost && overview.cost.avgTokensPerCall) }} token/次
            </div>
            <div class="metric-meta">
              入 {{ num(overview.cost && overview.cost.tokensIn) }} / 出 {{ num(overview.cost && overview.cost.tokensOut) }}
              · 平均延迟 {{ formatMs(overview.cost && overview.cost.avgLatencyMs) }}
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :sm="12" :lg="6">
          <el-card shadow="never" class="metric-card">
            <div class="metric-label">
              失败运行率
              <el-tooltip :content="TIPS.failure" placement="top">
                <span class="metric-help">口径</span>
              </el-tooltip>
            </div>
            <div class="metric-value metric-value--warn">{{ percent(overview.failure && overview.failure.failureRate) }}</div>
            <div class="metric-meta">
              失败 {{ num(overview.failure && overview.failure.failedRuns) }} / 终态 {{ num(overview.failure && overview.failure.runs) }} 次
            </div>
            <div class="metric-meta">
              ERROR 日志 {{ num(overview.failure && overview.failure.errorLogs) }} 条
            </div>
            <el-tag v-if="sampleTag(overview.failure && overview.failure.runs)" size="small" :type="sampleTag(overview.failure && overview.failure.runs).type" class="sample-tag">
              {{ sampleTag(overview.failure && overview.failure.runs).text }}
            </el-tag>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="cards">
        <!-- 待我处理：数量/超阈值/最久等待；有权限时点数量直达补货工作台 -->
        <el-col :xs="24" :lg="8">
          <el-card shadow="never" class="panel-card">
            <template #header>
              <div class="panel-head">
                <span>待我处理</span>
                <el-tooltip :content="TIPS.pending" placement="top">
                  <span class="metric-help">口径</span>
                </el-tooltip>
              </div>
            </template>
            <div class="pending-grid">
              <div class="pending-item">
                <div class="pending-label">待指派</div>
                <div class="pending-value">
                  {{ num(overview.pending && overview.pending.count) }}
                  <el-button
                    v-if="canGoRestock && (overview.pending && overview.pending.count > 0)"
                    link
                    type="primary"
                    size="small"
                    @click="goRestock"
                  >去处理</el-button>
                </div>
              </div>
              <div class="pending-item">
                <div class="pending-label">超时未处理</div>
                <div class="pending-value">
                  {{ num(overview.pending && overview.pending.overThreshold) }}
                  <span class="pending-unit">（阈值 {{ num(overview.pending && overview.pending.thresholdHours) }} 小时）</span>
                </div>
              </div>
              <div class="pending-item">
                <div class="pending-label">最久等待</div>
                <div class="pending-value">{{ formatMinutes(overview.pending && overview.pending.oldestWaitMinutes) }}</div>
              </div>
            </div>
            <p class="panel-note">
              待指派 = 无匹配接单人的补货计划；超过阈值未处理会进入告警。数据来自补货计划状态机。
            </p>
          </el-card>
        </el-col>

        <!-- Agent vs 规则对比：n=0 必须明确“不足以判断”，不能显示 0% 偏差冒充结论 -->
        <el-col :xs="24" :lg="16">
          <el-card shadow="never" class="panel-card">
            <template #header>
              <div class="panel-head">
                <span>Agent vs 规则版建议（5-4 双跑对比）</span>
                <el-tooltip :content="TIPS.compare" placement="top">
                  <span class="metric-help">口径</span>
                </el-tooltip>
              </div>
            </template>

            <template v-if="compareAvailable">
              <div class="compare-grid">
                <div class="compare-item">
                  <div class="compare-label">平均偏差</div>
                  <div class="compare-value">{{ pctValue(overview.compare.meanDeltaPct) }}</div>
                </div>
                <div class="compare-item">
                  <div class="compare-label">中位偏差</div>
                  <div class="compare-value">{{ pctValue(overview.compare.medianDeltaPct) }}</div>
                </div>
                <div class="compare-item">
                  <div class="compare-label">样本量 n</div>
                  <div class="compare-value">{{ num(overview.compare.n) }}</div>
                </div>
                <div class="compare-item">
                  <div class="compare-label">对比计划日</div>
                  <div class="compare-value compare-value--sm">{{ overview.compare.planDate || '—' }}</div>
                </div>
              </div>
              <p v-if="overview.compare.note" class="panel-note">{{ overview.compare.note }}</p>
              <el-tag v-if="sampleTag(overview.compare.n)" size="small" :type="sampleTag(overview.compare.n).type" class="sample-tag">
                {{ sampleTag(overview.compare.n).text }}
              </el-tag>
            </template>

            <el-alert
              v-else
              type="info"
              :closable="false"
              show-icon
              title="样本 n=0，不足以判断"
              :description="compareEmptyReason"
            />
          </el-card>
        </el-col>
      </el-row>

      <!-- 成本趋势：daily 简单表格 + 内联条形（不引入图表库，避免为一张图加依赖） -->
      <el-card shadow="never" class="panel-card">
        <template #header>
          <div class="panel-head">
            <span>成本趋势（token / 日）</span>
            <span class="dim">数据来源 agent_message；金额不可算（仓库无模型单价表）</span>
          </div>
        </template>

        <el-empty v-if="daily.length === 0" :image-size="60" description="该窗口内没有成本记录（agent_message 无数据）" />
        <el-table v-else :data="daily" size="small" border max-height="320">
          <el-table-column label="日期" prop="date" width="130" />
          <el-table-column label="调用次数" width="110" align="center">
            <template #default="{ row }">{{ num(row.calls) }}</template>
          </el-table-column>
          <el-table-column label="token 合计" width="140" align="center">
            <template #default="{ row }">{{ num(row.tokensTotal) }}</template>
          </el-table-column>
          <el-table-column label="占比（相对窗口内单日峰值）" min-width="220">
            <template #default="{ row }">
              <div class="bar">
                <span class="bar__fill" :style="{ width: barWidth(row) + '%' }"></span>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 口径与样本脚注：指标字典版本必须出现在页面上（字典 §7 回写义务） -->
      <p class="page-note">
        口径版本：《智能体效果指标字典 V1》（docs/agent-metrics-dictionary.md）。
        时间窗按半开区间统计；采纳/调整按决策时点归属；n&lt;30 仅内部调试查看，30≤n&lt;100 不作对外口径；
        本机/开发库样本属“开发库示例”，不得作为 KPI 承诺。
        <span v-if="overview.generatedAt" class="dim">数据生成时间：{{ formatTime(overview.generatedAt) }}</span>
      </p>
    </template>
  </div>
</template>

<script setup name="AgentDashboard">
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import auth from '@/plugins/auth'
import { getDashboardOverview } from '@/api/manage/agentDashboard'

/**
 * 智能体运营驾驶舱（S7，排期任务 6-1）。
 *
 * 口径唯一来源：`docs/agent-metrics-dictionary.md`（V1）。页面纪律：
 *  1. **每个数字必须带 n / 分母 / 窗口**——只给百分比是“看起来专业但不可对账”；
 *  2. **数据源未就绪就显示「—」**，不填演示数字（字典 §0 R-6）；
 *  3. **n=0 的对比卡必须说“不足以判断”**，不能显示 0% 偏差冒充“无差异”；
 *  4. 整页一次聚合请求：多接口拼口径必然漂移（同一指标两处算法不同是最难排查的错）。
 */

const TIPS = {
  adoption:
    '采纳率 = 已建单计划 / 已终结决策计划（同一 cohort，按决策时点归属窗口）；调整率 = 调整后建单 / 同分母。totalPlans 是“已终结决策计划数”，不是“生成计划数”。来源：agent_decision_log + agent_restock_plan。',
  degradation:
    '未校准率 = llm_calibrated=0 的计划 / 已知校准标记的计划；分母不含历史 NULL（未知行单列，避免把“不知道”算进分母）。',
  cost:
    '成本 token 来自 agent_message（tokens_in + tokens_out）；仓库没有模型单价表，因此只报 token、不换算金额。平均延迟为该表 latency_ms 均值；avg 为 null 表示窗口内无样本。',
  failure:
    '失败率 = 失败运行 / 终态运行（done/failed/interrupted）；其中 failedRuns = failed + interrupted（服务重启/人工中断也单列为失败）；running 与 waiting_confirm 不计入分母（还没结果不能算错）；chat 不写 agent_run，其失败率需日志口径单列。',
  pending:
    '待指派 = 补货计划 status=6；超阈值 = 等待时长超过配置阈值（默认见接口 thresholdHours）；最久等待按“最近变更（无则创建）至今”计算。',
  compare:
    'Agent vs 规则按同一计划日双跑（5-4）：偏差 = |Agent 建议量 − 规则建议量| / 规则建议量。必须同时给出 n；n=0 或未跑双跑时不做结论（后端 note 已说明）。'
}

const router = useRouter()
const days = ref(7)
const loading = ref(false)
const loadError = ref('')
const overview = ref(null)

const canGoRestock = computed(() => auth.hasPermi('manage:task:list'))

const alerts = computed(() => {
  const list = overview.value && Array.isArray(overview.value.alerts) ? overview.value.alerts : []
  return list.filter((item) => item && (item.level || item.msg))
})

const daily = computed(() => {
  const list = overview.value && overview.value.cost && Array.isArray(overview.value.cost.daily)
    ? overview.value.cost.daily
    : []
  return list
})

/** 窗口内确实没有数据：四张卡的分母/计数全为 0（区别于“加载失败”） */
const isEmpty = computed(() => {
  const data = overview.value
  if (!data) return true
  const totalPlans = Number((data.adoption && data.adoption.totalPlans) || 0)
  const calls = Number((data.cost && data.cost.calls) || 0)
  const runs = Number((data.failure && data.failure.runs) || 0)
  const pending = Number((data.pending && data.pending.count) || 0)
  const uncalibrated = Number((data.degradation && data.degradation.totalPlans) || 0)
  return totalPlans === 0 && calls === 0 && runs === 0 && pending === 0 && uncalibrated === 0
})

/** 对比卡是否有可展示样本：available=false 或 n=0 一律按“不足以判断”处理 */
const compareAvailable = computed(() => {
  const compare = overview.value && overview.value.compare
  if (!compare || compare.available !== true) return false
  return Number(compare.n || 0) > 0
})

const compareEmptyReason = computed(() => {
  const compare = overview.value && overview.value.compare
  if (compare && compare.note) return compare.note
  return '尚未采集到双跑对比数据（5-4 未跑或当日无样本）。没有样本时不做结论，也不显示演示数字。'
})

/** 降级率数据源是否缺失（5-6 未上线/explicit null）：缺失时显示「—」并标注待哪个任务 */
const degradationMissing = computed(() => {
  const degradation = overview.value && overview.value.degradation
  return !degradation || degradation.uncalibratedRate === null || degradation.uncalibratedRate === undefined
})

/** 未知（NULL）行数：历史/未接入，**不计入未校准率分母**（后端已按此口径返回） */
const degradationUnknown = computed(() => {
  const degradation = overview.value && overview.value.degradation
  return Number((degradation && degradation.unknownPlans) || 0)
})

/** 已知标记行数（= totalPlans − unknownPlans）：未校准率的真实分母，单独展示便于对账 */
const degradationKnown = computed(() => {
  const degradation = overview.value && overview.value.degradation
  const total = Number((degradation && degradation.totalPlans) || 0)
  return Math.max(total - degradationUnknown.value, 0)
})

const maxDailyTokens = computed(() => {
  return daily.value.reduce((max, item) => Math.max(max, Number(item.tokensTotal) || 0), 0)
})

/**
 * 样本量标签（字典 §4）：n<30 样本不足、30≤n<100 不作对外口径、n≥100 正常。
 * 返回 null 表示不显示（避免“n 很健康”也挂一个标签造成噪声）。
 */
function sampleTag(n) {
  if (n === null || n === undefined || n === '') return null
  const value = Number(n)
  if (Number.isNaN(value)) return null
  if (value < 30) return { text: '样本不足（n<30）', type: 'danger' }
  if (value < 100) return { text: 'n<100 不作对外口径', type: 'warning' }
  return null
}

/**
 * 比率（0~1）→ 两位小数百分比；缺失/非法显示「—」（不显示 0%，0 是“确实为零”的结论）。
 *
 * 后端契约是比率；若某环境返回“已经是百分数”的值（>1），按百分数显示——
 * 比率不可能 >1，这个上界是安全判别条件（否则 42% 会被显示成 4200%）。
 */
function percent(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  if (Number.isNaN(num)) return '—'
  const pct = Math.abs(num) <= 1 ? num * 100 : num
  return pct.toFixed(2) + '%'
}

/**
 * 偏差率字段（meanDeltaPct / medianDeltaPct）**本身就是百分数**（如 212.5 表示 212.5%），
 * 依据 `docs/agent-restock-1-14-comparison-caliber.md` §5 与 `agent_compare_report.mean_delta_pct`
 * （DECIMAL(8,2)，脚本写入时已 ×100）——这里绝不能再 ×100。
 */
function pctValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  if (Number.isNaN(num)) return '—'
  return (num > 0 ? '+' : '') + num.toFixed(2) + '%'
}

function num(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (Number.isNaN(number)) return String(value)
  return number.toLocaleString('zh-CN')
}

function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'
}

/**
 * 等待分钟 → 可读时长。
 * 超过 1 天折成“X 天 Y 小时”：真机存在十天级的超时待办（9000+ 分钟），
 * 只折成“151 小时 35 分”反而让人要去换算。
 */
function formatMinutes(value) {
  if (value === null || value === undefined || value === '') return '—'
  const minutes = Number(value)
  if (Number.isNaN(minutes)) return String(value)
  if (minutes < 60) return minutes + ' 分钟'
  const hours = Math.floor(minutes / 60)
  const restMinutes = minutes % 60
  if (hours < 24) return restMinutes === 0 ? hours + ' 小时' : hours + ' 小时 ' + restMinutes + ' 分'
  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  return restHours === 0 ? days + ' 天' : days + ' 天 ' + restHours + ' 小时'
}

/** 后端时间串一般是 yyyy-MM-dd HH:mm:ss；超长（带毫秒/ISO）截到秒，读起来更整齐 */
function formatTime(value) {
  if (!value) return '—'
  const text = String(value)
  return text.length >= 19 ? text.slice(0, 19) : text
}

function alertType(level) {
  return { critical: 'error', warning: 'warning', info: 'info' }[level] || 'info'
}

function barWidth(row) {
  if (!maxDailyTokens.value) return 0
  const value = Number(row.tokensTotal) || 0
  return Math.max(value <= 0 ? 0 : 2, Math.round((value / maxDailyTokens.value) * 100))
}

/** 跳补货工作台（不硬编码路由：按菜单 path 后缀找已注册路由） */
function goRestock() {
  const records = router.getRoutes()
  const hit = records.filter((item) => typeof item.path === 'string' && item.path.endsWith('/restock'))[0] || null
  if (!hit) {
    ElMessage.warning('未找到「智能补货工作台」页面：请确认菜单 9001 已执行并重新登录')
    return
  }
  router.push({ path: hit.path })
}

/** 查询概览（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  loadError.value = ''
  getDashboardOverview(days.value)
    .then((response) => {
      overview.value = (response && response.data) || null
    })
    .catch((error) => {
      // silent 请求：失败原因只能靠页面失败态承载（toast 一闪而过没法回看）
      loadError.value = (error && error.message) || '网络异常'
      overview.value = null
    })
    .finally(() => {
      loading.value = false
    })
}

function handleWindowChange() {
  getList()
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;

  &__title {
    font-size: 18px;
    font-weight: 600;
  }

  &__sub {
    margin-top: 2px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
}

.page-skeleton {
  padding: 10px 0;
}

.alerts {
  margin-bottom: 12px;

  .alert-item + .alert-item {
    margin-top: 8px;
  }
}

.cards {
  margin-bottom: 12px;
}

.metric-card {
  height: 100%;

  .metric-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--el-text-color-secondary);
  }

  /* 口径入口用一个小号文字链接，而不是图标：图标在紧凑卡片里容易被当成装饰 */
  .metric-help {
    font-size: 11px;
    color: var(--el-color-primary);
    border: 1px solid var(--el-color-primary-light-5);
    border-radius: 8px;
    padding: 0 6px;
    line-height: 16px;
    cursor: help;
  }

  .metric-value {
    margin: 6px 0 4px;
    font-size: 26px;
    font-weight: 600;
    line-height: 1.3;
  }

  /* 降级/失败是“坏指标”，用 warning 色提示需要关注（不是 error：仍是可运行状态） */
  .metric-value--warn {
    color: var(--el-color-warning);
  }

  .metric-meta {
    font-size: 12px;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
  }

  /* 数据源未就绪的说明：用 warning 色，避免被当成普通脚注忽略 */
  .metric-meta--warn {
    color: var(--el-color-warning);
  }

  .sample-tag {
    margin-top: 6px;
  }
}

.panel-card {
  height: 100%;

  .panel-head {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
  }

  .panel-note {
    margin: 10px 0 0;
    font-size: 12px;
    line-height: 1.7;
    color: var(--el-text-color-secondary);
  }
}

.pending-grid {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;

  .pending-item {
    min-width: 120px;
  }

  .pending-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .pending-value {
    margin-top: 2px;
    font-size: 22px;
    font-weight: 600;
  }

  .pending-unit {
    font-size: 12px;
    font-weight: 400;
    color: var(--el-text-color-secondary);
  }
}

.compare-grid {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;

  .compare-item {
    min-width: 110px;
  }

  .compare-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .compare-value {
    margin-top: 2px;
    font-size: 22px;
    font-weight: 600;
  }

  .compare-value--sm {
    font-size: 14px;
    font-weight: 500;
  }
}

/* 成本趋势条形：纯 div 实现（不引入图表库），长度=相对窗口内单日峰值 */
.bar {
  position: relative;
  height: 10px;
  border-radius: 5px;
  background: var(--el-fill-color-light);
  overflow: hidden;

  &__fill {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    background: var(--el-color-primary-light-3);
    border-radius: 5px;
  }
}

.page-note {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--el-text-color-secondary);
}

.dim {
  margin-left: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
