<template>
  <div class="app-container agent-run">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="76px">
      <el-form-item label="场景" prop="sceneCode">
        <el-select v-model="queryParams.sceneCode" placeholder="全部场景" clearable style="width: 130px">
          <el-option v-for="s in SCENES" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态" prop="status">
        <el-select v-model="queryParams.status" placeholder="全部状态" clearable style="width: 140px">
          <el-option v-for="s in RUN_STATUSES" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="对象ID" prop="subjectId">
        <el-input
          v-model="queryParams.subjectId"
          placeholder="设备编号（inner_code）"
          clearable
          style="width: 180px"
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
      <el-form-item label="时间范围">
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

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="primary"
          plain
          icon="Plus"
          v-hasPermi="['manage:vm:query']"
          @click="openStartDialog"
        >发起故障诊断</el-button>
      </el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList" />
    </el-row>

    <el-table
      v-loading="loading"
      :data="runList"
      border
      size="small"
      row-key="id"
      empty-text="没有符合条件的运行记录"
    >
      <el-table-column label="运行ID" min-width="130">
        <template #default="{ row }">
          <!-- 列表只显示后 8 位（运行ID 形如 fault-7f3c... 很长），全量值放 tooltip 供对账 -->
          <el-tooltip :content="row.runId || '—'" placement="top">
            <span class="mono">{{ shortRunId(row.runId) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="场景" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="sceneTag(row.sceneCode)">{{ sceneLabel(row.sceneCode) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="对象" min-width="150">
        <template #default="{ row }">
          <span>{{ row.subjectId || '—' }}</span>
          <span class="dim">{{ subjectTypeLabel(row.subjectType) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="runStatusTag(row.status)">{{ runStatusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="触发方式" width="90">
        <template #default="{ row }">{{ triggerLabel(row.triggerType) }}</template>
      </el-table-column>
      <el-table-column label="开始时间" width="160">
        <template #default="{ row }">{{ row.startedAt || '—' }}</template>
      </el-table-column>
      <el-table-column label="结束时间" width="160">
        <template #default="{ row }">{{ row.finishedAt || '—' }}</template>
      </el-table-column>
      <el-table-column label="耗时" width="90">
        <template #default="{ row }">{{ runDurationText(row) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" align="center" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="handleView(row)">查看</el-button>
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

    <!--
      时间线抽屉：按 run → step → toolCall 三级回放。
      两个场景（补货 restock / 故障 fault）共用本页，靠 sceneCode 区分，不各做一套。
    -->
    <el-drawer v-model="drawer.open" title="运行时间线" size="72%" append-to-body>
      <div v-loading="drawer.loading" element-loading-text="正在加载运行时间线…" class="timeline-wrap">
        <el-empty v-if="!drawer.loading && drawer.failed" description="运行详情加载失败，请关闭后重试" />
        <template v-else-if="drawer.run">
          <el-descriptions title="运行概要" :column="2" border size="small">
            <el-descriptions-item label="运行ID" :span="2">
              <span class="mono">{{ drawer.run.runId || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="场景">{{ sceneLabel(drawer.run.sceneCode) }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag size="small" :type="runStatusTag(drawer.run.status)">{{ runStatusLabel(drawer.run.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="对象">
              {{ subjectTypeLabel(drawer.run.subjectType) }} · {{ drawer.run.subjectId || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="触发方式">{{ triggerLabel(drawer.run.triggerType) }}</el-descriptions-item>
            <el-descriptions-item label="开始时间">{{ drawer.run.startedAt || '—' }}</el-descriptions-item>
            <el-descriptions-item label="结束时间">{{ drawer.run.finishedAt || '—' }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ runDurationText(drawer.run) }}</el-descriptions-item>
            <el-descriptions-item label="配置版本">
              {{ drawer.run.configVersion === null || drawer.run.configVersion === undefined ? '—' : drawer.run.configVersion }}
            </el-descriptions-item>
            <el-descriptions-item label="追踪ID" :span="2">
              <span class="mono">{{ drawer.run.requestId || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="终止原因" :span="2">{{ drawer.run.stopReason || '—' }}</el-descriptions-item>
          </el-descriptions>

          <!-- 故障场景：派单提案单独摆出来。运营点「确认建单」前必须能看见“派给谁、凭什么”，
               而不是只看到一个按钮（提案来自 step2 的 evidenceJson，解析失败则整块不显示）。 -->
          <template v-if="drawer.run.sceneCode === 'fault' && faultProposal">
            <div class="drawer-section-title">派单提案</div>
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="接单人">{{ faultProposal.assigneeName || '待指派' }}</el-descriptions-item>
              <el-descriptions-item label="优先级">{{ priorityLabel(faultProposal.priority) }}</el-descriptions-item>
              <el-descriptions-item label="可执行">{{ faultProposal.dispatchable ? '是' : '否' }}</el-descriptions-item>
              <el-descriptions-item label="依据证据">
                {{ (faultProposal.basedOnEvidence || []).length }} 条
              </el-descriptions-item>
              <el-descriptions-item label="选人依据" :span="2">
                区域 {{ faultProposal.assigneeBasis?.regionId ?? '—' }} ·
                候选 {{ faultProposal.assigneeBasis?.candidateCount ?? 0 }} 人 ·
                在途工单 {{ faultProposal.assigneeBasis?.deviceInflightCount ?? 0 }} 张
              </el-descriptions-item>
              <el-descriptions-item label="说明" :span="2">{{ faultProposal.desc || '—' }}</el-descriptions-item>
              <el-descriptions-item v-if="faultProposal.reason" label="未派原因" :span="2">
                {{ faultProposal.reason }}
              </el-descriptions-item>
            </el-descriptions>
          </template>

          <div class="drawer-section-title">轮次（按 stepNo 升序）</div>
          <el-empty
            v-if="sortedSteps.length === 0"
            description="暂无轮次记录：运行可能刚启动，或智能体侧记录写入失败（写入失败只 WARN、不阻断主流程）"
          />
          <el-timeline v-else>
            <el-timeline-item
              v-for="step in sortedSteps"
              :key="step.stepNo"
              :timestamp="step.createTime || ''"
              placement="top"
              :type="stepTimelineType(step.status)"
            >
              <div class="step-card">
                <div class="step-head">
                  <span class="step-title">#{{ step.stepNo }} · {{ roleLabel(step.roleCode) }} · {{ actionLabel(step.action) }}</span>
                  <el-tag size="small" :type="stepStatusTag(step.status)">{{ stepStatusLabel(step.status) }}</el-tag>
                </div>
                <div class="step-summary">{{ step.summary || '（本轮无结论摘要）' }}</div>
                <div class="step-meta">
                  <span>token 入 {{ step.inputTokens ?? 0 }} / 出 {{ step.outputTokens ?? 0 }}</span>
                  <span>耗时 {{ formatMs(step.durationMs) }}</span>
                </div>
                <div v-if="step.errorMsg" class="err-text">{{ step.errorMsg }}</div>

                <el-table :data="step.toolCalls || []" size="small" border class="tool-table" empty-text="本轮无工具调用">
                  <el-table-column label="工具编码" prop="toolCode" min-width="150">
                    <template #default="{ row }">
                      <span class="mono">{{ row.toolCode || '—' }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="参数摘要" prop="paramsDigest" min-width="200" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.paramsDigest || '—' }}</template>
                  </el-table-column>
                  <el-table-column label="行数" width="70" align="center">
                    <template #default="{ row }">{{ row.rowCount ?? 0 }}</template>
                  </el-table-column>
                  <el-table-column label="耗时" width="90">
                    <template #default="{ row }">{{ formatMs(row.durationMs) }}</template>
                  </el-table-column>
                  <el-table-column label="结果状态" width="100">
                    <template #default="{ row }">
                      <!-- 失败原因不占列（可能很长），挂在状态标签的 tooltip 上 -->
                      <el-tooltip v-if="row.errorMsg" :content="row.errorMsg" placement="top">
                        <el-tag size="small" :type="toolStatusTag(row.resultStatus)">{{ toolStatusLabel(row.resultStatus) }}</el-tag>
                      </el-tooltip>
                      <el-tag v-else size="small" :type="toolStatusTag(row.resultStatus)">
                        {{ toolStatusLabel(row.resultStatus) }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </el-timeline-item>
          </el-timeline>
        </template>
        <el-empty v-else-if="!drawer.loading" description="未取到运行数据" />
      </div>
      <template #footer>
        <!-- 人工确认入口（F-09c）：只有「故障 + 待确认 + 提案可执行」才出现。
             权限点与后端网关一致（manage:task:add）；没有权限的人连按钮都不显示。 -->
        <template v-if="canConfirmFault">
          <el-button
            type="primary"
            :loading="faultAction.confirming"
            v-hasPermi="['manage:task:add']"
            @click="handleFaultConfirm"
          >确认建单</el-button>
          <el-button
            type="danger"
            plain
            :loading="faultAction.confirming"
            v-hasPermi="['manage:task:add']"
            @click="openRejectDialog"
          >驳回</el-button>
        </template>
        <el-button @click="drawer.open = false">关闭</el-button>
      </template>
    </el-drawer>

    <!-- 驳回必须写原因：原因会进 agent_step 与决策留痕，是下一次诊断的输入 -->
    <el-dialog v-model="rejectDialog.open" title="驳回派单提案" width="520px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="驳回原因" required>
          <el-input
            v-model="rejectDialog.remark"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            placeholder="必填：写给下一次诊断/派单看（例如证据不足、已在现场处理）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rejectDialog.open = false">取消</el-button>
        <el-button type="danger" :loading="rejectDialog.submitting" @click="submitReject">
          确认驳回（不建单）
        </el-button>
      </template>
    </el-dialog>

    <!-- 发起诊断：M3 的演示入口。设备页的「智能诊断」按钮接入留在后续（F-15 附近统一）。 -->
    <el-dialog v-model="startDialog.open" title="发起故障诊断" width="560px" append-to-body>
      <el-form label-width="96px">
        <el-form-item label="设备编号" required>
          <el-input v-model="startDialog.innerCode" placeholder="如 A1000001" clearable />
        </el-form-item>
        <el-form-item label="时间窗口">
          <el-input-number v-model="startDialog.windowHours" :min="1" :max="87600" />
          <span class="dim">小时；默认 24。排查历史积压时调大（本机数据最早 2023 年）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="startDialog.open = false">取消</el-button>
        <el-button type="primary" :loading="startDialog.submitting" @click="submitStartRun">
          开始诊断
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="AgentRun">
import { ElMessage } from 'element-plus'
import { confirmFaultRun, getAgentRun, listAgentRuns, startFaultRun } from '@/api/manage/agentRun'

/**
 * 智能体运行时间线页（F-04，M1）。
 *
 * 数据来源：agent_run / agent_step / agent_tool_call 三表（docs/ddl/agent_run_step.sql），
 * 写入方只有 dkd-agent，本页只读。补货与故障两个场景共用，靠 sceneCode 区分。
 *
 * 权限：本页菜单复用 manage:agentAudit:list；详情接口（/{runId}）后端要求已有的
 * manage:agentAudit:query（即决策审计的「留痕详情」9009），给角色授权时需一并勾选。
 *
 * 页面把“一次运行”拆成三层：运行（列表）→ 轮次（timeline）→ 工具调用（内嵌表格），
 * 对应运营最常问的三个问题：这次跑完没有、每轮派给谁、调了什么拿到几行花了多久。
 */

const SCENES = [
  { value: 'restock', label: '补货', tag: 'success' },
  { value: 'fault', label: '故障', tag: 'warning' },
  { value: 'chat', label: '对话', tag: 'info' }
]

/**
 * run.status 颜色。
 *
 * running 用 ''：element-plus 2.4.3 的 ElTag.type 白名单是
 * ["success", "info", "warning", "danger", ""]，'' 即默认主色（视觉上就是 primary）。
 * 直接写 'primary' 会在每次渲染触发 Vue prop 校验告警（决策审计页踩过同一个坑）。
 */
const RUN_STATUSES = [
  { value: 'running', label: '运行中', tag: '' },
  { value: 'waiting_confirm', label: '待确认', tag: 'warning' },
  { value: 'done', label: '已完成', tag: 'success' },
  { value: 'failed', label: '失败', tag: 'danger' },
  { value: 'interrupted', label: '已中断', tag: 'info' }
]

/** 与 agent_run.trigger_type 列注释对齐：1-用户 2-定时任务 3-人工干预 */
const TRIGGERS = {
  1: '用户',
  2: '定时',
  3: '人工'
}

/** 与 agent_run.subject_type 列注释对齐 */
const SUBJECT_TYPES = {
  vm: '设备',
  plan: '补货计划'
}

/**
 * 角色/动作映射只登记 DDL 与故障场景方案里已有的值。
 * 未登记的值**原样显示**（不兜底成“未知”）：新动作上线时运营能直接看见编码，
 * 这比悄悄显示“未知”更容易发现漏登记。
 */
const ROLES = {
  diagnostician: '诊断官',
  dispatcher: '派单官',
  reviewer: '审核官',
  supervisor: '主管'
}
const ACTIONS = {
  diagnose: '诊断',
  dispatch_propose: '派单提案',
  dispatch_confirm: '派单确认',
  review: '审核',
  finish: '收尾'
}

const STEP_STATUSES = [
  { value: 'ok', label: '正常', tag: 'success', timeline: 'success' },
  { value: 'error', label: '失败', tag: 'danger', timeline: 'danger' },
  { value: 'timeout', label: '超时', tag: 'warning', timeline: 'warning' },
  { value: 'skipped', label: '跳过', tag: 'info', timeline: 'info' }
]
const TOOL_STATUSES = [
  { value: 'ok', label: '成功', tag: 'success' },
  { value: 'error', label: '失败', tag: 'danger' },
  { value: 'timeout', label: '超时', tag: 'warning' },
  { value: 'truncated', label: '截断', tag: 'info' }
]

const loading = ref(true)
const showSearch = ref(true)
const runList = ref([])
const total = ref(0)
const dateRange = ref([])
const queryRef = ref(null)
/** 抽屉状态：failed 单独标记，用来区分“加载失败”和“真的没有数据”（两者不能都显示空状态） */
const drawer = reactive({ open: false, loading: false, failed: false, run: null, steps: [] })

/**
 * 故障场景交互状态（F-09c）。
 *
 * 与 drawer 分开：确认/驳回的 loading 只影响按钮，不应让整个抽屉转圈
 * （否则运营会以为“详情在重载”，而实际是在等 Java 建单）。
 */
const faultAction = reactive({ confirming: false })
const rejectDialog = reactive({ open: false, remark: '', submitting: false })
const startDialog = reactive({ open: false, innerCode: '', windowHours: 24, submitting: false })

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    sceneCode: null,
    status: null,
    subjectId: null,
    requestId: null,
    params: {}
  }
})
const { queryParams } = toRefs(data)

/** 按 stepNo 升序：后端 SQL 目前已排好序，页面再排一次，防止将来接口调整把 #1→#N 的时间顺序悄悄打乱（顺序错了会直接误导排障） */
const sortedSteps = computed(() => {
  return (drawer.steps || []).slice().sort((a, b) => (a.stepNo || 0) - (b.stepNo || 0))
})

/** 优先级（与后端 DispatchProposal 注释一致：1-低 2-中 3-高 4-紧急） */
const PRIORITIES = { 1: '低', 2: '中', 3: '高', 4: '紧急' }

/**
 * 从 step2（dispatch_propose）的 evidenceJson 里取出派单提案。
 *
 * 解析失败返回 null（整块不显示）而不是抛错：坏一条证据不该让整页打不开。
 * `evidenceJson` 可能是字符串（MySQL JSON 列的当前返回）或对象（将来换驱动），两种都兼容。
 */
const faultProposal = computed(() => {
  const step = (drawer.steps || []).filter((item) => item.action === 'dispatch_propose')[0]
  if (!step || !step.evidenceJson) return null
  try {
    const payload =
      typeof step.evidenceJson === 'string' ? JSON.parse(step.evidenceJson) : step.evidenceJson
    return (payload && payload.proposal) || null
  } catch (error) {
    return null
  }
})

/** 只有「故障 + 待确认 + 提案可执行」才允许人工确认（与后端确认接口的前置一致） */
const canConfirmFault = computed(() => {
  return (
    drawer.run?.sceneCode === 'fault' &&
    drawer.run?.status === 'waiting_confirm' &&
    faultProposal.value?.dispatchable === true
  )
})

/** 空值显示「—」，其余原样转字符串（映射不到时不吞掉原始编码） */
function rawLabel(value) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

/** 字典命中返回中文，未登记的值原样返回；用 hasOwnProperty 避免 'constructor' 之类撞上原型链 */
function dictLabel(dict, value) {
  const raw = rawLabel(value)
  if (raw === '—') return raw
  return Object.prototype.hasOwnProperty.call(dict, value) ? dict[value] : raw
}

function findIn(list, value) {
  return list.filter((item) => item.value === value)[0]
}

function sceneLabel(value) {
  const hit = findIn(SCENES, value)
  return hit ? hit.label : rawLabel(value)
}

function sceneTag(value) {
  const hit = findIn(SCENES, value)
  return hit ? hit.tag : 'info'
}

function runStatusLabel(value) {
  const hit = findIn(RUN_STATUSES, value)
  return hit ? hit.label : rawLabel(value)
}

function runStatusTag(value) {
  const hit = findIn(RUN_STATUSES, value)
  return hit ? hit.tag : 'info'
}

function stepStatusLabel(value) {
  const hit = findIn(STEP_STATUSES, value)
  return hit ? hit.label : rawLabel(value)
}

function stepStatusTag(value) {
  const hit = findIn(STEP_STATUSES, value)
  return hit ? hit.tag : 'info'
}

function stepTimelineType(value) {
  const hit = findIn(STEP_STATUSES, value)
  // 未知状态用主色（timeline 的 type 白名单包含 primary），不要静默当成成功
  return hit ? hit.timeline : 'primary'
}

function toolStatusLabel(value) {
  const hit = findIn(TOOL_STATUSES, value)
  return hit ? hit.label : rawLabel(value)
}

function toolStatusTag(value) {
  const hit = findIn(TOOL_STATUSES, value)
  return hit ? hit.tag : 'info'
}

function triggerLabel(value) {
  return dictLabel(TRIGGERS, value)
}

function priorityLabel(value) {
  return dictLabel(PRIORITIES, value)
}

function subjectTypeLabel(value) {
  return dictLabel(SUBJECT_TYPES, value)
}

function roleLabel(value) {
  return dictLabel(ROLES, value)
}

function actionLabel(value) {
  return dictLabel(ACTIONS, value)
}

/** 运行ID只显示后 8 位；短 ID 原样显示，避免被截成空串 */
function shortRunId(value) {
  if (!value) return '—'
  const text = String(value)
  return text.length > 8 ? text.slice(-8) : text
}

/**
 * MySQL DATETIME（"2026-10-26 14:02:03"）转毫秒。
 * Safari 对 `new Date("yyyy-MM-dd HH:mm:ss")` 解析不可靠，统一改写成
 * `yyyy/MM/dd HH:mm:ss`（各浏览器都认）；同时兼容带 T 的 ISO 串。
 */
function toMillis(value) {
  if (!value) return NaN
  return new Date(String(value).replace(/-/g, '/').replace('T', ' ')).getTime()
}

/**
 * 列表/概要的耗时（秒）。
 *
 * 没有 finishedAt 时返回「进行中」而不是 0 秒：运行中的 run 只有 startedAt，
 * 算成 0 秒会让运营误判“跑完只花了 0 秒”；时钟异常导致的负值也显示「—」而不是负数。
 */
function runDurationText(run) {
  if (!run || !run.startedAt) return '—'
  if (!run.finishedAt) return '进行中'
  const start = toMillis(run.startedAt)
  const end = toMillis(run.finishedAt)
  if (Number.isNaN(start) || Number.isNaN(end)) return '—'
  const seconds = (end - start) / 1000
  return seconds >= 0 ? seconds.toFixed(1) + ' s' : '—'
}

/** 毫秒耗时：不足 1 秒按 ms 显示（一轮工具调用多是几百毫秒，换算成 0.3s 反而难读） */
function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'
}

/** 查询运行列表（loading 必须在 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  // 每次重建 params：日期清空后不能残留上一次的 beginTime/endTime
  queryParams.value.params = {}
  if (dateRange.value && dateRange.value.length === 2) {
    queryParams.value.params.beginTime = dateRange.value[0]
    queryParams.value.params.endTime = dateRange.value[1]
  }
  listAgentRuns(queryParams.value)
    .then((response) => {
      runList.value = response.rows || []
      total.value = response.total || 0
    })
    .catch(() => {
      // 传输层错误拦截器已提示过；这里补一条页面语境，并清空旧数据，避免把上一次结果当成这一次的
      ElMessage.error('运行列表加载失败，请稍后重试')
      runList.value = []
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
  dateRange.value = []
  if (queryRef.value) queryRef.value.resetFields()
  handleQuery()
}

/**
 * 打开运行时间线。
 *
 * 一次请求拿 run + steps（后端详情接口就是这么返回的）；加载完成前不渲染概要，
 * 避免把“还在加载”显示成“没有数据”（决策审计页踩过同类问题）。
 */
function handleView(row) {
  drawer.open = true
  loadDrawer(row.runId)
}

/**
 * 加载/刷新抽屉数据（确认、驳回、发起诊断后都走这里）。
 *
 * 失败时标记 failed 而不是清成空：让页面能区分“加载失败”和“真的没有轮次”
 * （后者是业务事实，前者是故障，两者不能都显示空状态）。
 */
function loadDrawer(runId) {
  drawer.loading = true
  drawer.failed = false
  drawer.run = null
  drawer.steps = []
  getAgentRun(runId)
    .then((response) => {
      const detail = response.data || {}
      drawer.run = detail.run || null
      drawer.steps = detail.steps || []
      // 详情成功但 run 为空属于契约异常，明确告诉用户而不是给一张空白抽屉
      if (!drawer.run) ElMessage.warning('详情返回为空，无法回放本次运行')
    })
    .catch(() => {
      drawer.failed = true
      ElMessage.error('运行详情加载失败，请稍后重试')
    })
    .finally(() => {
      drawer.loading = false
    })
}

/**
 * 确认建单（F-09c）。
 *
 * 结果的三种业务形态都按“正常返回”处理，用不同提示区分，而不是把 duplicate 当失败：
 * confirmed=新建、duplicate=之前已建（返回原工单）、其它=未建单（含 409 状态冲突，由拦截器提示）。
 */
function handleFaultConfirm() {
  if (!drawer.run) return
  const runId = drawer.run.runId
  faultAction.confirming = true
  confirmFaultRun(runId, { action: 'confirm' })
    .then((response) => {
      const result = response.data || {}
      if (result.status === 'confirmed') {
        ElMessage.success('已建维修工单 ' + (result.taskCode || result.taskId))
      } else if (result.status === 'duplicate') {
        ElMessage.info('该运行已建单，返回原工单 ' + (result.taskCode || result.taskId))
      } else {
        ElMessage.warning(result.message || '未建单，请刷新后重试')
      }
      loadDrawer(runId)
      getList()
    })
    .catch(() => {
      // 拦截器已提示后端 message（409 正在建单中 / 403 无权限）；这里刷新一次状态，避免按钮卡死
      loadDrawer(runId)
    })
    .finally(() => {
      faultAction.confirming = false
    })
}

function openRejectDialog() {
  rejectDialog.remark = ''
  rejectDialog.open = true
}

/** 驳回：原因必填（原因会进 step 与决策留痕，是下一次诊断的输入） */
function submitReject() {
  const remark = (rejectDialog.remark || '').trim()
  if (!remark) {
    ElMessage.warning('驳回必须填写原因')
    return
  }
  if (!drawer.run) return
  const runId = drawer.run.runId
  rejectDialog.submitting = true
  confirmFaultRun(runId, { action: 'reject', remark })
    .then((response) => {
      const result = response.data || {}
      ElMessage.success(result.message || '已驳回，未建单')
      rejectDialog.open = false
      loadDrawer(runId)
      getList()
    })
    .catch(() => {
      // 同样交给拦截器提示；保持弹窗打开，运营可改原因后重试
    })
    .finally(() => {
      rejectDialog.submitting = false
    })
}

function openStartDialog() {
  startDialog.innerCode = ''
  startDialog.windowHours = 24
  startDialog.open = true
}

/**
 * 发起诊断（M3 演示入口）。
 *
 * 为什么同步等待：单台诊断 1~2 秒，运营能接受；变批量巡检（F-15）时再改异步。
 * 成功后直接打开新运行的抽屉——否则用户点完只看到列表多一行，不知道发生了什么。
 */
function submitStartRun() {
  const innerCode = (startDialog.innerCode || '').trim()
  if (!innerCode) {
    ElMessage.warning('请填写设备编号')
    return
  }
  startDialog.submitting = true
  startFaultRun({ innerCode, windowHours: startDialog.windowHours || 24 })
    .then((response) => {
      const result = response.data || {}
      startDialog.open = false
      ElMessage.success(
        result.status === 'waiting_confirm' ? '诊断完成：已生成派单提案，待人工确认' : '诊断完成：未生成派单提案'
      )
      getList()
      if (result.runId) {
        drawer.open = true
        loadDrawer(result.runId)
      }
    })
    .catch(() => {
      // 502/参数错误由拦截器提示；保持弹窗，允许改设备号重试
    })
    .finally(() => {
      startDialog.submitting = false
    })
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
/* 抽屉带 append-to-body，内容会被 teleport 到 body，因此样式规则一律写在顶层，不要裹在页面根类下 */
.timeline-wrap {
  min-height: 200px;
}

.drawer-section-title {
  margin: 18px 0 12px;
  font-size: 14px;
  font-weight: 600;
}

.step-card {
  padding: 10px 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
}

.step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.step-title {
  font-size: 13px;
  font-weight: 600;
}

.step-summary {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
  white-space: pre-wrap;
}

.step-meta {
  display: flex;
  gap: 16px;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.tool-table {
  margin-top: 8px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.dim {
  margin-left: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.err-text {
  margin-top: 6px;
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}
</style>
