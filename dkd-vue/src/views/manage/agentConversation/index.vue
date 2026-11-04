<template>
  <div class="app-container agent-conversation">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="76px">
      <el-form-item label="场景" prop="scene">
        <el-select v-model="queryParams.scene" placeholder="全部场景" clearable style="width: 150px">
          <el-option v-for="item in SCENES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态" prop="status">
        <el-select v-model="queryParams.status" placeholder="全部状态" clearable style="width: 130px">
          <el-option v-for="item in STATUSES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="用户ID" prop="userId">
        <el-input
          v-model="queryParams.userId"
          placeholder="精确匹配，如 2"
          clearable
          style="width: 140px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="时间范围">
        <!--
          会话按创建时间过滤，默认近 7 天：历史会话不是排障用的秒级数据，7 天是“最近在用”的自然粒度。
          前端同时限制最大 30 天——后端对会话列表的护栏与日志页一致（单主库，AGENTS §1 约束 5）。
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
      页面自解释：说明数据来源与只读纪律。
      会话/消息表由 Python 侧写入（agent_conversation / agent_message），后台只查不改——
      “用户与智能体说过什么”是事实记录，能改就不再是记录（与审计页同一原则）。
    -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="历史会话只读：会话与消息由智能体服务写入（复用 agent_conversation / agent_message）"
      description="列表的消息数、工具调用数、token 合计由后端聚合，列表不做逐行补查。打开详情可看完整消息时间线；每条消息的追踪 ID 可跳到执行日志页继续排障（第 4-1 步「刷新/重进可恢复」的检索入口）。"
    />

    <div class="list-wrap">
      <!-- 三态①：加载骨架（首屏/翻页/搜索期间都显示，避免把“加载中”误读成“没有会话”） -->
      <el-skeleton v-if="loading" :rows="8" animated class="list-skeleton" />

      <!-- 三态②：加载失败（给出原因 + 重试，不静默变成空列表） -->
      <el-empty v-else-if="loadError" :description="'会话列表加载失败：' + loadError">
        <el-button type="primary" @click="getList">重试</el-button>
      </el-empty>

      <!-- 三态③：空数据（说明是筛选结果为空，而不是“系统里没有会话”） -->
      <el-empty
        v-else-if="conversationList.length === 0"
        description="当前筛选条件下没有会话：可放宽时间范围或清空场景/状态后再试"
      >
        <el-button @click="resetQuery">调整筛选条件</el-button>
      </el-empty>

      <el-table v-else :data="conversationList" border size="small" row-key="conversationId">
        <el-table-column label="会话ID" min-width="150">
          <template #default="{ row }">
            <!-- 列表只显示后 12 位（会话ID 是 UUID，很长），全量值放 tooltip 供对账 -->
            <el-tooltip :content="row.conversationId || '—'" placement="top">
              <span class="mono">{{ shortId(row.conversationId) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="标题" prop="title" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.title">{{ row.title }}</span>
            <span v-else class="dim">（无标题）</span>
          </template>
        </el-table-column>
        <el-table-column label="用户" width="140">
          <template #default="{ row }">
            <span>{{ row.userName || '—' }}</span>
            <span class="dim">#{{ row.userId === null || row.userId === undefined ? '—' : row.userId }}</span>
          </template>
        </el-table-column>
        <el-table-column label="场景" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="sceneTag(row.scene)">{{ sceneLabel(row.scene) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="消息数" width="100" align="center">
          <template #default="{ row }">
            <!--
              messageCount 是表内冗余计数，actualMessageCount 是后端对 agent_message 的实测条数。
              两者不一致说明计数漂移（冗余列没跟上写入），用感叹号 + tooltip 标出来：
              把漂移当正常数字展示，会让人按错误的消息条数判断“对话是否完整”。
            -->
            <el-tooltip
              :disabled="!countDrift(row)"
              placement="top"
              :content="'表内冗余计数 ' + (row.messageCount ?? 0) + ' 条，消息表实测 ' + (row.actualMessageCount ?? '—') + ' 条（计数漂移）'"
            >
              <span>
                {{ row.messageCount ?? 0 }}
                <span v-if="countDrift(row)" class="drift">!</span>
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="工具调用" width="88" align="center">
          <!-- 工具调用数 = tool_calls 非空的消息条数；0 是真值（没调工具），不是缺失 -->
          <template #default="{ row }">{{ row.toolCallsCount ?? row.toolCallCount ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="tokens（入/出）" width="130" align="center">
          <template #default="{ row }">{{ (row.tokensIn ?? 0) + ' / ' + (row.tokensOut ?? 0) }}</template>
        </el-table-column>
        <el-table-column label="最近消息" width="170">
          <template #default="{ row }">{{ formatTime(row.lastMessageTime) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatTime(row.createTime) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <!-- 详情接口权限是 manage:agentConversation:query（菜单 9019），只授列表权限的人不该看到这个按钮 -->
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentConversation:query']"
              @click="openDetail(row)"
            >查看详情</el-button>
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

    <!--
      S13 会话详情抽屉：消息时间线 + 工具调用折叠 + requestId 跳日志页。
      append-to-body 会把内容 teleport 到 body，所以相关样式一律写在 style 顶层（见文件末尾说明）。
    -->
    <el-drawer v-model="detail.open" title="会话详情" size="72%" append-to-body>
      <div v-loading="detail.loading" element-loading-text="正在加载会话与消息…">
        <!-- 抽屉三态①：加载中（骨架，不用空白 + 转圈的“还没开始”假象） -->
        <el-skeleton v-if="detail.loading" :rows="8" animated class="drawer-skeleton" />

        <!--
          抽屉三态②：加载失败（原因 + 重试；区分“加载失败”与“真的没有消息”）。
          失败原因**持久展示**在空态里，而不是只靠一条会消失的 toast——排障时用户需要回看
          “到底是连不上还是这个会话不存在”。对“不存在”这类确定性业务错误不给重试按钮：
          重试不会改变事实，只会让人反复点一个必然失败的动作。
        -->
        <el-empty v-else-if="detail.failed" :description="'会话详情加载失败：' + detail.failMsg">
          <el-button
            v-if="!conversationMissing"
            type="primary"
            @click="loadDetail(detail.conversationId)"
          >重试</el-button>
          <el-button @click="detail.open = false">关闭</el-button>
        </el-empty>

        <template v-else-if="detail.conversation">
          <el-descriptions title="会话概要" :column="3" border size="small">
            <el-descriptions-item label="会话ID" :span="2">
              <span class="mono">{{ detail.conversation.conversationId || detail.conversationId }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag size="small" :type="statusTag(detail.conversation.status)">
                {{ statusLabel(detail.conversation.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="标题" :span="3">
              {{ detail.conversation.title || '（无标题）' }}
            </el-descriptions-item>
            <el-descriptions-item label="场景">{{ sceneLabel(detail.conversation.scene) }}</el-descriptions-item>
            <el-descriptions-item label="用户">
              {{ detail.conversation.userName || '—' }}
              <span class="dim">#{{ detail.conversation.userId ?? '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="区域">
              {{ detail.conversation.regionId === null || detail.conversation.regionId === undefined ? '—' : detail.conversation.regionId }}
            </el-descriptions-item>
            <el-descriptions-item label="消息数">
              {{ detail.conversation.messageCount ?? detail.messages.length }}
              <span
                v-if="detail.conversation.actualMessageCount !== null && detail.conversation.actualMessageCount !== undefined"
                class="dim"
              >（实测 {{ detail.conversation.actualMessageCount }}）</span>
            </el-descriptions-item>
            <el-descriptions-item label="工具调用">{{ toolCallTotal }}</el-descriptions-item>
            <el-descriptions-item label="tokens">
              入 {{ detail.conversation.tokensIn ?? messageTokenIn }} / 出 {{ detail.conversation.tokensOut ?? messageTokenOut }}
            </el-descriptions-item>
            <el-descriptions-item label="最近消息">{{ formatTime(detail.conversation.lastMessageTime) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatTime(detail.conversation.createTime) }}</el-descriptions-item>
            <el-descriptions-item label="会话主键">
              {{ detail.conversation.id === null || detail.conversation.id === undefined ? '—' : detail.conversation.id }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="drawer-section-title">消息时间线（按 seq 升序）</div>
          <!--
            后端单次最多返回 messageLimit 条消息（默认 500）。若 messagesTruncated=true，
            说明这个会话的消息数超过上限、这里看到的是**不完整**的时间线——
            不提示就会让人以为“对话只有这些”，把截断误读成会话事实。
          -->
          <el-alert
            v-if="detail.messagesTruncated"
            class="mb8"
            type="info"
            :closable="false"
            show-icon
            title="消息时间线已截断"
            :description="'该会话消息超过单次上限（' + (detail.messageLimit || 500) + ' 条），此处按 seq 升序只展示前 ' + sortedMessages.length + ' 条；继续排查请用消息里的追踪 ID 去执行日志页。'"
          />
          <el-empty
            v-if="sortedMessages.length === 0"
            :image-size="70"
            description="该会话没有消息记录：可能刚建立会话还没发言，或消息写入失败（写入失败不阻断对话，但会缺这段记录）"
          />
          <el-timeline v-else>
            <el-timeline-item
              v-for="msg in sortedMessages"
              :key="msg.seq"
              :timestamp="formatTime(msg.createTime)"
              placement="top"
              :type="roleTimelineType(msg.msgRole)"
            >
              <div class="msg-card">
                <div class="msg-head">
                  <el-tag size="small" :type="roleTag(msg.msgRole)">
                    #{{ msg.seq }} · {{ roleLabel(msg.msgRole) }}
                  </el-tag>
                  <span class="dim">{{ msg.model || '未记录模型' }}</span>
                  <span class="dim">token 入 {{ msg.tokensIn ?? 0 }} / 出 {{ msg.tokensOut ?? 0 }}</span>
                  <span class="dim">耗时 {{ formatMs(msg.latencyMs) }}</span>
                  <!--
                    互跳入口：每条消息的 request_id 都能跳到执行日志页。
                    用 hasPermi 判断而不是 v-hasPermi：这里控制的是 v-if，交给 computed 更直观。
                  -->
                  <el-button
                    v-if="msg.requestId && canViewLog"
                    size="small"
                    type="primary"
                    link
                    @click="goLog(msg)"
                  >查日志</el-button>
                </div>
                <div v-if="msg.requestId" class="msg-req">
                  追踪ID <span class="mono">{{ msg.requestId }}</span>
                </div>

                <!--
                  截断提示必须显式：后端把 content 截断到 2000 字并置 truncated=true，
                  若页面不提示，运维会把“被截断的半句话”当成模型原始输出，排障结论直接跑偏。
                -->
                <el-alert
                  v-if="msg.truncated"
                  class="msg-truncated"
                  type="warning"
                  :closable="false"
                  show-icon
                  title="内容已截断（仅展示前 2000 字）"
                  :description="truncateDesc(msg)"
                />

                <!-- 用 <pre> 文本插值渲染正文：不渲染 markdown/HTML，既是审计口径也是注入面控制 -->
                <pre class="msg-content">{{ msg.content || '（空）' }}</pre>

                <el-collapse v-if="hasToolCalls(msg)" class="msg-tools">
                  <el-collapse-item :title="'工具调用（' + toolCallsCount(msg) + '）'" :name="String(msg.seq)">
                    <pre class="msg-json">{{ prettyToolCalls(msg) }}</pre>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </el-timeline-item>
          </el-timeline>
        </template>

        <el-empty v-else-if="!detail.loading" description="未取到会话数据" />
      </div>
      <template #footer>
        <span class="dim drawer-foot">只读：消息由智能体服务写入，后台不提供修改/删除</span>
        <el-button @click="detail.open = false">关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup name="AgentConversation">
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import auth from '@/plugins/auth'
import { getAgentConversation, listAgentConversation } from '@/api/manage/agentConversation'

/**
 * S13 历史会话列表页（排期任务 2-5 / 4-1）。
 *
 * 数据来源：`agent_conversation` + `agent_message`（复用现有两表，**不新建**
 * agent_chat_session/agent_chat_message——计划表 ⚠1 已裁定复用，新增会引入双写与迁移成本）。
 * 查询经 Java `GET /manage/agent/conversation/list` 与 `/{conversationId}`，不经 Python。
 *
 * 三个界面取舍：
 *  1. **列表只做聚合展示**：消息数/工具调用数/token 由后端聚合返回，页面不在行内再查消息
 *     （否则一页 10 行就是 10 次额外查询，AGENTS §10 明确禁止 N+1）。
 *  2. **详情抽屉三态**：加载骨架 / 失败可重试 / 空消息说明——把“加载失败”画成“没有消息”
 *     会让运维把链路故障误判成“用户没说话”。
 *  3. **消息正文不渲染 markdown**：用 <pre> 文本插值，保留原始换行也不引入注入面；
 *     会话详情是排障视图，渲染会改写原文（审计口径不接受）。
 */

const route = useRoute()
const router = useRouter()

/** 会话场景：1~4 与 agent_conversation.scene 列注释一致；≥101 是自定义 chat 智能体（配置化方案 3-2） */
const SCENES = [
  { value: 1, label: '通用问答' },
  { value: 2, label: '补货' },
  { value: 3, label: '诊断' },
  { value: 4, label: '运营分析' }
]
/** 会话状态与 agent_conversation.status 列注释一致：1-进行中 2-已结束 3-已归档 */
const STATUSES = [
  { value: 1, label: '进行中' },
  { value: 2, label: '已结束' },
  { value: 3, label: '已归档' }
]
/** 消息角色与 agent_message.msg_role 列注释一致：1-用户 2-助手 3-工具结果 4-系统 */
const ROLES = {
  1: { label: '用户', tag: 'info', timeline: 'primary' },
  2: { label: '助手', tag: '', timeline: 'success' },
  3: { label: '工具结果', tag: 'warning', timeline: 'warning' },
  4: { label: '系统', tag: 'info', timeline: 'info' }
}

const loading = ref(true)
const loadError = ref('')
const showSearch = ref(true)
const conversationList = ref([])
const total = ref(0)
const queryRef = ref(null)

/** 会话详情抽屉；failed 单独标记，用来区分“加载失败”与“真的没有消息” */
const detail = reactive({
  open: false,
  loading: false,
  failed: false,
  conversationId: null,
  conversation: null,
  messages: [],
  /** 失败原因（后端 msg 原样保留）：持久展示，避免只有一闪而过的 toast */
  failMsg: '',
  /** 后端单次消息上限（默认 500）；messagesTruncated=true 表示时间线不完整，页面必须提示 */
  messageLimit: 500,
  messagesTruncated: false
})

/** 详情页「查日志」按钮是否可见：目标页（执行日志 S6）的权限点 */
const canViewLog = computed(() => auth.hasPermi('manage:agentLog:list'))

/**
 * “会话不存在”是确定性业务错误（后端 code500 + 明确 msg）：这里不给重试按钮。
 * 其余失败（网络/超时/5xx）才保留重试——是否可重试是两类完全不同的故障。
 */
const conversationMissing = computed(() => detail.failMsg.indexOf('不存在') >= 0)

/** 后端强制的时间窗上限（30 天，作用于 agent_conversation.create_time，与日志页同一套护栏） */
const MAX_RANGE_MS = 30 * 24 * 60 * 60 * 1000

/** 默认时间范围 = 近 7 天（会话是“最近在用”的检索场景，不是秒级排障数据） */
function defaultRange() {
  const end = dayjs()
  return [end.subtract(7, 'day').format('YYYY-MM-DD HH:mm:ss'), end.format('YYYY-MM-DD HH:mm:ss')]
}

const dateRange = ref(defaultRange())

const rangeShortcuts = [
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
    pageSize: 10,
    scene: null,
    status: null,
    userId: null
  }
})
const { queryParams } = toRefs(data)

/** 消息按 seq 升序：后端已排序，这里再排一次防止接口调整把顺序悄悄打乱（顺序错了会误读对话） */
const sortedMessages = computed(() => {
  return (detail.messages || []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0))
})

/** 详情里消息自带的 token 合计（conversation 聚合值与消息合计不一致时，页面上两个都保留可对账） */
const messageTokenIn = computed(() => sumBy(sortedMessages.value, 'tokensIn'))
const messageTokenOut = computed(() => sumBy(sortedMessages.value, 'tokensOut'))
const toolCallTotal = computed(() => {
  const head = detail.conversation
  if (head && head.toolCallsCount !== null && head.toolCallsCount !== undefined) return head.toolCallsCount
  if (head && head.toolCallCount !== null && head.toolCallCount !== undefined) return head.toolCallCount
  return sortedMessages.value.reduce((acc, msg) => acc + toolCallsCount(msg), 0)
})

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------
function sumBy(list, key) {
  return list.reduce((acc, item) => acc + (Number(item[key]) || 0), 0)
}

function formatTime(value) {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : String(value)
}

function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'
}

/** 会话ID只显示后 12 位（UUID 很长）；短 ID 原样显示，避免被截成空串 */
function shortId(value) {
  if (!value) return '—'
  const text = String(value)
  return text.length > 12 ? '…' + text.slice(-12) : text
}

function sceneLabel(value) {
  if (value === null || value === undefined || value === '') return '—'
  const hit = SCENES.filter((item) => item.value === value)[0]
  if (hit) return hit.label
  // 自定义智能体的场景码 ≥101（契约 §4.4），列表里原样带编号展示，便于对上 agent_config
  return Number(value) >= 101 ? '自定义 #' + value : String(value)
}

function sceneTag(value) {
  // 1-通用问答 用默认主色（'' 是 el-tag 合法取值，写 'primary' 会触发 prop 校验告警）
  return { 1: '', 2: 'success', 3: 'warning', 4: 'info' }[value] ?? 'info'
}

function statusLabel(value) {
  if (value === null || value === undefined || value === '') return '—'
  const hit = STATUSES.filter((item) => item.value === value)[0]
  return hit ? hit.label : String(value)
}

function statusTag(value) {
  // 1-进行中 用默认主色（''），2-已结束 success，3-已归档 info；未登记给灰色
  return { 1: '', 2: 'success', 3: 'info' }[value] ?? 'info'
}

function roleLabel(value) {
  const hit = ROLES[value]
  return hit ? hit.label : '角色 #' + value
}

function roleTag(value) {
  const hit = ROLES[value]
  return hit ? hit.tag : 'info'
}

function roleTimelineType(value) {
  const hit = ROLES[value]
  return hit ? hit.timeline : 'primary'
}

/**
 * 工具调用三种形态都兼容（java-log 最终契约）：
 *  - toolCalls：后端解析后的数组/对象，解析失败时为 null；
 *  - toolCallsRaw / toolCallsParseError：仅解析失败时给原文与错误标记；
 *  - toolCallCount：后端算好的条数，优先用它（与列表聚合口径一致）。
 * 原则：不清楚的结构原样展示，不吞内容、不替后端“猜”成空数组。
 */
function normalizeToolCalls(value) {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'string') {
    try {
      return JSON.parse(value)
    } catch (error) {
      return value
    }
  }
  return value
}

/** 优先用后端解析结果，其次用原始文本（JSON 解析失败场景），都没有才算“无工具调用” */
function resolveToolCalls(msg) {
  const parsed = normalizeToolCalls(msg.toolCalls)
  if (parsed !== null) return parsed
  return normalizeToolCalls(msg.toolCallsRaw)
}

function toolCallsCount(msg) {
  if (msg.toolCallCount !== null && msg.toolCallCount !== undefined) return Number(msg.toolCallCount) || 0
  const resolved = resolveToolCalls(msg)
  if (resolved === null) return 0
  if (Array.isArray(resolved)) return resolved.length
  if (typeof resolved === 'object') return Object.keys(resolved).length > 0 ? 1 : 0
  return 1
}

function hasToolCalls(msg) {
  return toolCallsCount(msg) > 0
}

/** 工具调用 JSON 美化：解析失败时明确标注“以下是原始文本”，不替后端做结构修正 */
function prettyToolCalls(msg) {
  if (msg.toolCallsParseError) {
    return '（tool_calls JSON 解析失败，以下为原始文本）\n' + (msg.toolCallsRaw || '（原始内容为空）')
  }
  const resolved = resolveToolCalls(msg)
  if (resolved === null) return '（无）'
  if (typeof resolved === 'string') return resolved
  try {
    return JSON.stringify(resolved, null, 2)
  } catch (error) {
    return String(resolved)
  }
}

/** 截断说明：带上原始长度与已展示长度，避免读者把截断内容当成全文 */
function truncateDesc(msg) {
  const total = msg.contentLength
  const shown = (msg.content || '').length
  const scope = total === null || total === undefined
    ? '原始消息更长，本页展示的是截断后的内容'
    : '原始消息共 ' + total + ' 字，此处展示前 ' + shown + ' 字'
  return scope + '；需要完整上下文请结合同一 requestId 的执行日志与运行时间线判断。'
}

// ---------------------------------------------------------------------------
// 列表
// ---------------------------------------------------------------------------

/**
 * 冗余计数与实测条数不一致 → 标出“计数漂移”。
 *
 * 为什么值得单独标：messageCount 是表内冗余列，actualMessageCount 是后端对消息表的实测条数；
 * 两者对不上说明冗余列没跟上写入，此时按 messageCount 判断“对话是否完整”会得出错误结论。
 * 两端任一缺失时不报（缺失 ≠ 漂移）。
 */
function countDrift(row) {
  if (row.messageCount === null || row.messageCount === undefined) return false
  if (row.actualMessageCount === null || row.actualMessageCount === undefined) return false
  return Number(row.actualMessageCount) !== Number(row.messageCount)
}

function validateRange() {
  const range = dateRange.value
  if (!range || range.length !== 2 || !range[0] || !range[1]) {
    ElMessage.warning('请选择时间范围（默认近 7 天，最长 30 天）')
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
  // 后端对会话列表同样强制 30 天窗口（超限返回 code=500）：前端先拦一次，
  // 否则用户只会看到“列表加载失败”，无法区分“真的没会话”和“时间窗超限被拒”。
  if (end - start > MAX_RANGE_MS) {
    ElMessage.warning(
      '时间范围最大 30 天（当前约 ' + Math.ceil((end - start) / (24 * 60 * 60 * 1000)) + ' 天），请缩小后重试'
    )
    return false
  }
  return true
}

/**
 * 组装查询参数：空串不传（传 `scene=` 这类空参数会让后端把它当成“已填写但为空”）。
 * 用户ID 只允许数字：后端按 Long 解析，非数字会变成 400，页面先拦一次给出可读提示。
 */
function buildQuery() {
  const query = {
    pageNum: queryParams.value.pageNum,
    pageSize: queryParams.value.pageSize,
    beginTime: dateRange.value[0],
    endTime: dateRange.value[1]
  }
  const userId = queryParams.value.userId === null || queryParams.value.userId === undefined
    ? ''
    : String(queryParams.value.userId).trim()
  if (userId !== '' && !/^\d+$/.test(userId)) {
    ElMessage.warning('用户ID只能是数字')
    return null
  }
  if (userId !== '') query.userId = userId
  if (queryParams.value.scene !== null && queryParams.value.scene !== undefined) query.scene = queryParams.value.scene
  if (queryParams.value.status !== null && queryParams.value.status !== undefined) query.status = queryParams.value.status
  return query
}

/** 查询列表（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  if (!validateRange()) return
  const query = buildQuery()
  if (!query) return
  loading.value = true
  loadError.value = ''
  listAgentConversation(query)
    .then((response) => {
      conversationList.value = response.rows || []
      total.value = response.total || 0
    })
    .catch((error) => {
      // 拦截器已提示传输/业务错误；这里保留原因给失败态，并清空旧数据（否则会把上一次结果当这一次）
      loadError.value = (error && error.message) || '网络异常'
      conversationList.value = []
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
  handleQuery()
}

// ---------------------------------------------------------------------------
// 详情抽屉
// ---------------------------------------------------------------------------
function openDetail(row) {
  detail.conversationId = row.conversationId
  detail.open = true
  loadDetail(row.conversationId)
}

/**
 * 加载会话详情。
 *
 * 失败时置 failed（而不是给空消息）：让抽屉能区分“加载失败”和“真的没有消息”，
 * 两者对排障的含义完全相反。loading 在 finally 复位（AGENTS §2.2）。
 */
function loadDetail(conversationId) {
  if (!conversationId) return
  detail.conversationId = conversationId
  detail.loading = true
  detail.failed = false
  detail.failMsg = ''
  detail.conversation = null
  detail.messages = []
  // 重置截断标记：上一个会话的“已截断”不能带到这一个（否则会误报时间线不完整）
  detail.messagesTruncated = false
  detail.messageLimit = 500
  getAgentConversation(conversationId)
    .then((response) => {
      const payload = response.data || {}
      detail.conversation = payload.conversation || null
      detail.messages = payload.messages || []
      detail.messagesTruncated = payload.messagesTruncated === true
      detail.messageLimit = payload.messageLimit || 500
      // 契约异常（详情成功但没有会话头）要明确告知，而不是给一张只剩“未取到会话数据”的抽屉
      if (!detail.conversation) ElMessage.warning('详情返回缺少会话头，消息时间线可能不完整')
    })
    .catch((error) => {
      // 只置失败态、不自弹 toast：拦截器已经展示过后端 msg（如「会话不存在：xxx」），
      // 再弹一条只会变成“拦截器一条 + 页面一条”。失败原因持久展示在抽屉空态里。
      detail.failed = true
      detail.failMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      detail.loading = false
    })
}

// ---------------------------------------------------------------------------
// 跨页跳转（不硬编码路由）
// ---------------------------------------------------------------------------

/**
 * 在当前已注册路由里按菜单 path 后缀找执行日志页。
 *
 * 为什么不在前端写死 `/ai/agentLog`：菜单由 sys_menu 动态下发（AGENTS §2.2），
 * 写死 URL 在菜单 path 调整后会 404；按后缀查找对“挂在哪个父目录下”不敏感。
 * 找不到说明该环境没执行 9017 菜单脚本，明确提示而不是给死链。
 */
function findMenuRoute(suffix) {
  const records = router.getRoutes()
  return records.filter((item) => typeof item.path === 'string' && item.path.endsWith('/' + suffix))[0] || null
}

/** 跳到执行日志页并按 requestId 过滤（S6 会读 route.query.requestId 预填筛选） */
function goLog(msg) {
  const hit = findMenuRoute('agentLog')
  if (!hit) {
    ElMessage.warning('未找到「执行日志」页面：请确认菜单 9017 已执行（docs/ddl/agent_log_menu.sql）并重新登录')
    return
  }
  router.push({ path: hit.path, query: { requestId: msg.requestId } })
}

// ---------------------------------------------------------------------------
// 路由参数（互跳入口）
// ---------------------------------------------------------------------------

/** 已应用的会话ID：避免 keep-alive 复用时对同一个会话重复拉详情 */
let appliedConversationId = null

/**
 * 读取 route.query.conversationId 并直接打开详情。
 *
 * 用途：执行日志页的「会话」按钮跳到本页时，用户期望直接看到那个会话，
 * 而不是先看到一张默认筛选的列表再自己找。
 *
 * URL 是用户可改的：后端对 conversationId 做 1~64 长度校验（不合法直接 code500、不查库），
 * 所以这里先拦一次——否则抽屉会弹一个必然失败的请求，用户只能看到“加载失败”。
 */
function applyRouteQuery() {
  const raw = route.query.conversationId
  if (raw === undefined || raw === null || raw === '') return false
  const conversationId = String(raw).trim()
  if (conversationId.length < 1 || conversationId.length > 64) {
    if (conversationId !== appliedConversationId) {
      appliedConversationId = conversationId
      ElMessage.warning('URL 中的会话ID不合法（长度 1~64），已忽略')
    }
    return false
  }
  if (conversationId === appliedConversationId) return false
  appliedConversationId = conversationId
  detail.open = true
  loadDetail(conversationId)
  return true
}

onMounted(() => {
  applyRouteQuery()
  getList()
})

/**
 * keep-alive 复用时 onMounted 不再触发：从日志页带新的 conversationId 跳回来时，
 * 这里补读一次并直接打开新会话；同一会话不重复加载（避免无谓请求与抽屉闪烁）。
 */
onActivated(() => {
  applyRouteQuery()
})
</script>

<style lang="scss" scoped>
/*
 * ⚠️ 抽屉带 append-to-body，内容会被 teleport 到 <body>，祖先 `.agent-conversation` 不在
 * 其 DOM 路径上——所以弹窗内的样式规则一律写在顶层（决策审计页踩过：裹在页面根类下时
 * 编译成 `.agent-conversation .xxx[data-v-*]`，一律不匹配，pre 的换行/滚动全部失效）。
 */

.list-wrap {
  min-height: 200px;
}

.list-skeleton,
.drawer-skeleton {
  padding: 8px 0;
}

.drawer-section-title {
  margin: 18px 0 12px;
  font-size: 14px;
  font-weight: 600;
}

.msg-card {
  padding: 10px 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
}

.msg-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.msg-req {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.msg-truncated {
  margin-top: 8px;
}

.msg-content {
  max-height: 320px;
  overflow: auto;
  margin: 8px 0 0;
  padding: 10px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.7;
  /* 保留换行并强制换行，避免长文本渲染成一条横向长行 */
  white-space: pre-wrap;
  word-break: break-all;
}

.msg-tools {
  margin-top: 8px;
}

.msg-json {
  max-height: 260px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.drawer-foot {
  float: left;
  line-height: 32px;
  font-size: 12px;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 4px;
}

/* 计数漂移标记：视觉上必须比普通数字“扎眼”，否则会被当成装饰忽略 */
.drift {
  margin-left: 2px;
  color: var(--el-color-warning);
  font-weight: 700;
  cursor: help;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}
</style>
