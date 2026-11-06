<template>
  <el-drawer
    v-model="visible"
    :with-header="false"
    :append-to-body="true"
    size="400px"
    class="agent-assistant-drawer"
  >
    <div class="agent-panel">
      <!-- 头部：与原型 V2 的抽屉头一致（头像 + 标题 + 副标题 + 操作） -->
      <div class="agent-head">
        <div class="avatar">🤖</div>
        <div class="title">
          <b>DKD AI 助手</b>
          <span>Supervisor 路由 · 全局可用</span>
        </div>
        <el-tooltip content="新建会话" placement="bottom">
          <el-button link class="head-btn" @click="resetConversation">
            <el-icon><Plus /></el-icon>
          </el-button>
        </el-tooltip>
        <el-button link class="head-btn" @click="close">
          <el-icon><Close /></el-icon>
        </el-button>
      </div>

      <!--
        场景切换：scene 参数与 Python 侧契约一致（1-通用 2-补货 3-诊断 4-运营分析，自定义场景>=101）。
        列表来自 `probeStatus` 合并后的动态 scenes（默认硬编码 2/3/4 作回落，见 script 里 mergeScenes 注释）。
      -->
      <div class="agent-tabs">
        <span
          v-for="item in scenes"
          :key="item.value"
          class="tab"
          :class="{ on: scene === item.value }"
          :title="item.custom ? '自定义智能体（管理员在配置台下发的场景）' : ''"
          @click="switchScene(item.value)"
        >{{ item.label }}</span>
      </div>

      <!--
        4-1 刷新恢复提示：会话从 localStorage 恢复后显示（绿色=“找回来了”，不是故障提示）。
        用户点右上角「+」新会话会清空本场景存档，之后不再自动恢复。
      -->
      <div v-if="restoredNotice" class="restore-bar">
        <span class="txt">{{ restoredNotice }}</span>
        <el-button link type="primary" @click="restoredNotice = ''">知道了</el-button>
      </div>

      <!-- 降级提示：agent.enabled=false 或 Python 不可达（方案 §3.3 要求明确告知，不伪造会话能力） -->
      <div v-if="degraded" class="degrade-bar">
        <span class="txt">{{ degradedReason || '智能体服务暂不可用，多轮对话已暂停' }}</span>
        <el-button link type="primary" @click="probeStatus(true)">重试</el-button>
      </div>

      <!--
        回显模式提示（2026-10-01 新增）。

        为什么必须有：服务端在 `DKD_AGENT_USE_LLM=0` 时走 Phase 0 的**链路自检分支**，
        回复会被硬编码成 `[echo] <你输入的内容>`（见 `app/graphs/chat_graph.py::ECHO_PREFIX`）。
        此前前端**完全没读 meta 帧里的 `mode` 字段**，于是这个行为看起来就像“对话坏了/答非所问”。

        这正是报障里最贵的一类：用户看到的是“智能体失灵”，而真相是“本机配置没打开真模型”。
        把它显示出来，报障时会直接指向配置而不是代码。
      -->
      <div v-if="turnMode === 'echo'" class="mode-bar">
        <span class="txt">
          当前为<strong>回显模式</strong>：回答不会调用大模型（会原样回显你的问题）。
          这是链路自检状态，启用方式：服务端设 <code>DKD_AGENT_USE_LLM=1</code> 后重启。
        </span>
      </div>
      <!-- 真模型时只做一个轻量标识：模型名按**服务端实际返回**显示（可能与配置不同，用于核对计费口径） -->
      <div v-else-if="turnModel" class="model-badge">模型：{{ turnModel }}</div>

      <!--
        当前诊断设备提示（2-3 的前端半场）。

        为什么必须有：后端从 2-3 起就把“正在诊断哪台设备”做成了会话状态（`focus_inner_code`），
        追问“那它呢”不会再索要编号——但**用户看不见助手记住了哪台**，体验上是割裂的：
        他无法知道自己说“它”会不会指错，也不敢放心追问。
        来源是 done 帧的 `focusInnerCode`（服务端用确定性规则算出，不是模型的话术）。
      -->
      <div v-if="focusCode" class="focus-bar">
        <span class="txt">当前诊断设备：<strong>{{ focusCode }}</strong>（说“这台/它”指它）</span>
      </div>

      <div ref="bodyRef" class="chat-body">
        <div v-if="messages.length === 0" class="msg ai">
          <div class="ava">🤖</div>
          <div class="bubble">
            <span>{{ greeting }}</span>
            <br />
            <span class="muted">可以直接说：{{ quickQuestions[0].text }} / {{ quickQuestions[1].text }}</span>          </div>
        </div>
        <div v-for="msg in messages" :key="msg.id" class="msg" :class="msg.role">
          <div class="ava">{{ msg.role === 'user' ? '👤' : '🤖' }}</div>
          <div class="bubble">
            <template v-if="msg.role === 'user'">{{ msg.content }}</template>
            <template v-else>
              <!--
                工具轮次占位（2026-10-01 新增，§十 任务 6）。
                为什么必须有：助手挂上只读工具后，一次「先看紧急的」要经历
                「模型决定调工具 → 真查库 → 模型再作答」两步，而**工具轮次不产生 delta 帧**
                （见 app/graphs/chat_graph.py：命中 tool_calls 那一轮 reply 置空）。
                于是气泡会空白几秒 —— 用户无法区分“正在查数据”与“卡死了”。
                这里给出明确占位；caret 仍然保留，保持既有流式观感与 .cjs 验收脚本的锚点不变。
              -->
              <span v-if="msg.streaming && !msg.content" class="querying">正在查询数据…</span>
              <span v-else v-html="renderMarkdown(msg.content)"></span>
              <span v-if="msg.streaming" class="caret">▌</span>
            </template>
            <!--
              结构化诊断结论（2-4）：来自 done 帧的 `conclusion` 字段（后端只在真调了
              `build_device_diagnosis` 时才带）。**置信度是证据强度刻度、不是概率**，
              所以卡片必须同时显示依据与未知项——只画一条进度条会让人以为“这是病因的概率”。
            -->
            <div v-if="msg.conclusion" class="conclusion">
              <div class="conclusion-head">
                <b>诊断结论（结构化）</b>
                <span class="conf-badge">{{ confidenceLabel(msg.conclusion.confidence) }}</span>
              </div>
              <el-progress
                :percentage="confidencePercent(msg.conclusion.confidence)"
                :stroke-width="8"
                :show-text="false"
                class="conf-bar"
              />
              <div class="conclusion-basis">{{ msg.conclusion.confidenceBasis || '—' }}</div>
              <div v-if="(msg.conclusion.unknowns || []).length" class="conclusion-block">
                <div class="t">未知项（必须一并告知）</div>
                <ul>
                  <li v-for="(item, i) in msg.conclusion.unknowns" :key="i">{{ item }}</li>
                </ul>
              </div>
              <div v-if="(msg.conclusion.recommendedActions || []).length" class="conclusion-block">
                <div class="t">可执行动作</div>
                <ul>
                  <li v-for="(item, i) in msg.conclusion.recommendedActions" :key="i">{{ item }}</li>
                </ul>
              </div>
              <div class="conclusion-foot">
                证据 {{ msg.conclusion.evidenceCount || 0 }} 条
                <span v-if="(msg.conclusion.evidenceKinds || []).length" class="muted">
                  （{{ (msg.conclusion.evidenceKinds || []).join(' / ') }}）
                </span>
                · <span class="muted">置信度 = 证据强度刻度，不是概率；本结论不含病因推断</span>
              </div>
              <!--
                2-4：逐条证据默认折叠。帧里只投影前 3 条（后端有界，见 app/api/chat.py 的
                _project_evidence），总量看 evidenceCount——不要在这里假装「全部展开」。
              -->
              <div v-if="(msg.conclusion.evidence || []).length" class="conclusion-block evidence">
                <div class="t evidence-toggle" @click="msg.evidenceExpanded = !msg.evidenceExpanded">
                  <span class="caret">{{ msg.evidenceExpanded ? '▾' : '▸' }}</span>
                  证据明细（前 {{ msg.conclusion.evidence.length }} 条 / 共
                  {{ msg.conclusion.evidenceCount || 0 }} 条）
                </div>
                <ul v-show="msg.evidenceExpanded" class="evidence-list">
                  <li v-for="(item, i) in msg.conclusion.evidence" :key="i">
                    <span class="sev" :class="'sev-' + (item.severity || 'info')">
                      {{ severityLabel(item.severity) }}
                    </span>
                    <span class="kind">{{ item.kind }}</span>
                    <div class="summary">{{ item.summary }}</div>
                  </li>
                </ul>
              </div>
            </div>
            <!--
              本次查询口径（FIX-27）。

              为什么必须有：实测模型会把工具结果**改写/拼接**（把 90 天窗口的 0 条说成
              “近五年都没异常”）。模型的措辞不可控，但**工具返回的窗口与条数是事实**——
              把它直接展示出来，用户不必相信模型的那句话也能得出正确结论。
            -->
            <div v-if="(msg.dataCaliber || []).length" class="caliber">
              <div class="t">本次查询口径（来自工具，非模型转述）</div>
              <div v-for="(item, i) in msg.dataCaliber" :key="i" class="caliber-row">
                <code>{{ item.tool }}</code>
                <span v-if="item.windowDays" class="muted">
                  窗口 {{ item.windowDays }} 天
                  <template v-if="item.windowStart && item.windowEnd">
                    （{{ shortDate(item.windowStart) }} ~ {{ shortDate(item.windowEnd) }}）
                  </template>
                </span>
                <span v-if="countText(item.counts)" class="muted">{{ countText(item.counts) }}</span>
                <span v-if="item.windowSource === 'userTimeHint'" class="muted">按你提的时间范围</span>
              </div>
            </div>
            <!--
              数字保真提示（FIX-27）：回复里的数字在本次查询结果里没有出处。
              为什么只提示、不遮挡/不改写模型的回答：改写会让用户以为模型没说错，
              也会让人失去“这台机器在这件事上不可靠”的信号。提示 + 权威口径交给用户判断。
            -->
            <div v-if="msg.grounding && msg.grounding.grounded === false" class="grounding-warn">
              <span class="txt">
                提示：回复里的 {{ (msg.grounding.ungroundedNumbers || []).join('、') }}
                没有出现在本次查询结果中，请以上面的「本次查询口径」为准。
              </span>
            </div>
            <div v-if="msg.error" class="bubble-err">{{ msg.error }}</div>
          </div>
        </div>
      </div>

      <div class="quick">
        <span
          v-for="item in quickQuestions"
          :key="item.text"
          class="q"
          :class="{ disabled: sending || degraded }"
          @click="askQuick(item.text)"
        >{{ item.text }}</span>
      </div>

      <div class="chat-input">
        <el-input
          v-model="draft"
          :disabled="degraded || sending"
          placeholder="输入问题…"
          @keydown.enter="onEnter"
        />
        <el-button v-if="sending" type="danger" @click="stop">停止</el-button>
        <el-button v-else type="primary" :disabled="degraded || !draft.trim()" @click="send(draft)">发送</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup name="AgentAssistant">
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { chatWithAgent, probeAgentStatus, resumeAgentStream } from '@/api/manage/agent'
import useAgentStore from '@/store/modules/agent'
import useUserStore from '@/store/modules/user'

// marked 配置与 views/manage/report/index.vue 保持一致（v18 用 use 取代 setOptions）
marked.use({ breaks: true, gfm: true })

/** 内置场景的固定中文名（与改造前逐字一致，保证“后端没下发”时行为不变） */
const BUILTIN_SCENE_LABELS = {
  2: '补货 Agent',
  3: '诊断 Agent',
  4: '分析 Copilot'
}

/**
 * 场景列表（3-3 起为动态列表）：默认 3 项。
 *
 * 为什么默认仍是硬编码的 2/3/4（灰度友好）：`status.data.agents` 是 3-3 新增的下发字段，
 * 在下列情况下都会缺失或为空——老版本 Java 没扩展 status、上游 Python 不可达、
 * agent_config 表为空/DB 开关关闭、以及回滚到旧版本时。
 * 这些场景下抽屉必须与改造前**完全一致**：不能因为“场景清单取不到”就少 tab 或不可用，
 * 否则一次后端灰度/回滚就会让所有人失去对话入口。
 */
const scenes = ref(defaultScenes())

function defaultScenes() {
  return [2, 3, 4].map((value) => ({ value, label: BUILTIN_SCENE_LABELS[value], custom: false }))
}

/**
 * 用后端下发的 agents 合并出可见场景列表。
 *
 * 契约（3-3）：`data.agents = [{code,name,scene,runtime,builtin,tools,version}]`，
 * 包含已启用的 chat 载体配置（内置 chat 行 scene=1 + 自定义智能体 scene>=101）。
 * 合并规则与理由：
 *  1. agents 不是非空数组 → 直接返回默认 3 项（**回落**，见上）；
 *  2. scene=1 的内置 chat 行不变成 tab：抽屉从来没有“通用问答”tab（改造前就是 2/3/4），
 *     多一个通用 tab 等于偷偷新增一个能力入口；
 *  3. 内置 2/3/4 沿用固定中文名（配置台允许改名，入口名跟着变会让运营找不到）；
 *     后端 name 仅在内置映射缺失时兜底；
 *  4. 自定义场景（非内置，契约是 scene>=101）追加为 tab，label 用后端 name；
 *  5. 默认 3 项**始终保留**：即使后端某次只下发了自定义行（内置 chat 行被停用等），
 *     也不能让既有 tab 忽然消失——抽屉可用性由 `/agent/status` 的 enabled 决定，不由 tab 决定；
 *  6. 按 scene 升序：tab 顺序必须稳定，否则每次打开抽屉 tab 位置都可能变（点错的来源）。
 */
function mergeScenes(agents) {
  const list = Array.isArray(agents) ? agents : []
  if (list.length === 0) return defaultScenes()
  const byScene = new Map()
  list.forEach((agent) => {
    if (!agent) return
    const raw = agent.scene
    if (raw === null || raw === undefined || raw === '') return
    const value = Number(raw)
    // 缺 scene / 非数字 / scene<1 的下发行无法路由到 Python 侧图，不能成为 tab
    if (!Number.isFinite(value) || value < 1) return
    // scene=1 是内置 chat 载体的配置行，不是抽屉里的一个 tab（理由见上）
    if (value === 1) return
    const builtin = agent.builtin === 1 || agent.builtin === true || [2, 3, 4].indexOf(value) >= 0
    const label = (builtin && BUILTIN_SCENE_LABELS[value]) || agent.name || '场景 ' + value
    byScene.set(value, { value, label, custom: !builtin, builtin })
  })
  ;[2, 3, 4].forEach((value) => {
    if (!byScene.has(value)) {
      byScene.set(value, { value, label: BUILTIN_SCENE_LABELS[value], custom: false, builtin: true })
    }
  })
  return Array.from(byScene.values()).sort((a, b) => a.value - b.value)
}

/**
 * 自定义场景的通用快捷问题。
 *
 * 为什么不给自定义场景写专属问题：它们的工具子集/提示词由管理员在配置台按场景下发，
 * 前端无从知道“这个智能体到底能查什么”。写死具体问题=把做不到的事摆在快捷入口
 * （2026-10-01 已修过一次同类问题：提示与能力脱节比答错更耗信任）。
 */
const CUSTOM_QUICK_QUESTIONS = [
  { text: '这个场景你能做什么？' },
  { text: '你能查哪些数据？' }
]

// 快捷问题与原型 V2 抽屉一致，按场景各给一组。
//
// ⚠️ 这里的每一句都必须是**助手真能回答**的（2026-10-01 修正）：
// 旧版里放了“暂停明天的自动分析”“帮我建维修工单”“近 7 天各区域销量排行”这类问题，
// 而助手**没有任何写工具、也没挂数据分析工具**——用户点下去只能得到一句拒绝。
// 把做不到的事摆在快捷问题里，是“提示与能力脱节”的另一种形态，比答错还耗信任。
const QUICK_QUESTIONS = {
  2: [
    { text: '先看紧急的' },
    { text: '为什么补这么多可乐？' },
    { text: 'A1000001 库存怎么样？' },
    { text: 'A1000001 近 7 天卖得怎么样？' }
  ],
  3: [
    { text: '五道口那台机器不出货' },
    { text: 'A1000001 近五年有异常吗？' },
    { text: '这台设备以前遇到过同样的问题吗？' },
    { text: '这台机器还有多少库存？' }
  ],
  4: [
    { text: '这个场景你能做什么？' },
    { text: '各区域销量排行在哪看？' },
    { text: '缺货率口径是什么？' },
    { text: '为什么这个场景查不了数据？' }
  ]
}

// 欢迎语：**只承诺已实现的能力**，并明确指出写操作去哪里做（AGENTS §7.9）
const GREETINGS = {
  2: '你好，我是补货 Agent。可以查待确认建议、解释补货依据、查设备库存与近期销量；调整/建单请到「智能补货工作台」人工操作。',
  3: '你好，我是设备诊断 Agent。报设备编码或点位（如“五道口”），我会查订单/库存异常与历史维修记录。说明：本平台暂无结构化故障库，历史工单多为备注文本，我会如实标注有无可用依据；建维修工单请在工单管理页面操作。',
  4: '你好，我是运营分析 Copilot。当前版本还未接入数据分析工具：可以帮你理清口径、指出该去哪个页面看，但不会给出未经查询的数字。'
}

const agentStore = useAgentStore()
const userStore = useUserStore()
const visible = computed({
  get: () => agentStore.drawerVisible,
  set: (val) => (val ? agentStore.open() : agentStore.close())
})

const scene = ref(2)
const draft = ref('')
const sending = ref(false)
const degraded = computed(() => agentStore.degraded)
const degradedReason = computed(() => agentStore.degradedReason)
/** 服务端本轮的实际模式：'llm' | 'echo'（来自 meta 帧） */
const turnMode = ref('')
/** 服务端实际返回的模型名（来自 done 帧；echo 模式为空） */
const turnModel = ref('')
/**
 * 本会话当前诊断的设备（来自 done 帧的 focusInnerCode，2-3）。
 * 为什么放在会话级而不是气泡级：它表达的是“助手现在记的是哪台”，换会话才重置。
 */
const focusCode = ref('')
const messages = ref([])
const bodyRef = ref(null)
// 会话上下文：首轮由服务端下发 conversation_id（Python 侧 thread_id），后续轮次复用实现多轮
const conversationId = ref(undefined)
// 断点续传锚点：本已收到的最后一个 delta 帧 id。
// ⚠️ 只在“断线自动续接”时使用；**新一轮对话绝不能带**（带了会被服务端当成续传请求，
// 实测第二轮直接返回“会话不存在或无可续传内容”——真浏览器验证发现）
const lastEventId = ref(undefined)
// 当前流句柄（用于"停止"与卸载时释放连接）
let stream = null
let messageSeq = 0
// 状态探测只做一次（失败后由"重试"按钮显式触发，避免每次打开都打扰）
let probed = false

// ---------------------------------------------------------------------------
// 4-1 会话本地存档：刷新 / 重进不丢会话
//
// 存在 localStorage 而**不是**后端：这里只是本机体验缓存，业务事实仍在
// agent_conversation/agent_message（S13 历史会话可查）。因此：
//  - key 带 user + scene：不同用户、不同场景互不串（场景切换即清旧场景存档）；
//  - 只存对话文本 / conversationId / focusCode / 已丢弃条数，**不含任何密钥或 token**；
//  - 用户点「新会话」或清浏览器存储即可清空；
//  - 存储不可用（隐私模式）或配额满时只降级为“不恢复”，绝不阻断对话。
// ---------------------------------------------------------------------------
const STORAGE_PREFIX = 'dkd.agent.chat.v1'
/** 消息条数上限：超过从最旧开始丢，恢复时如实提示丢了多少条 */
const MAX_STORED_MESSAGES = 200
/** 序列化字节上限约 200KB：localStorage 是共享配额，聊天记录不能把它吃满 */
const MAX_STORED_BYTES = 200 * 1024
/** 单条正文落盘上限：防止一条超长回复就把配额打满（恢复时它是截断文本） */
const MAX_STORED_CONTENT_CHARS = 40000

/** 恢复提示文案（空串=不显示）；用户点「知道了」只是收起提示，存档仍在 */
const restoredNotice = ref('')
/** 累计被丢弃的消息条数（超条数/超字节时从最旧丢），随存档一起保存 */
const droppedCount = ref(0)
/** 会话被服务端判定失效时置 true：跳过本轮落盘，避免刚清掉的 key 又被 finally 写回来 */
let suppressPersist = false

function storageKey(sceneValue) {
  const user = userStore.id || userStore.name || 'anonymous'
  return STORAGE_PREFIX + '.' + user + '.scene.' + sceneValue
}

/** UTF-8 字节数：localStorage 按字符计配额但有实现差异，用字节估算更接近真实占用 */
function byteLength(text) {
  try {
    return new TextEncoder().encode(text).length
  } catch (error) {
    return text.length * 3
  }
}

function readStoredSession(sceneValue) {
  try {
    const raw = window.localStorage.getItem(storageKey(sceneValue))
    if (!raw) return null
    const payload = JSON.parse(raw)
    if (!payload || typeof payload !== 'object') return null
    if (!Array.isArray(payload.messages)) return null
    return payload
  } catch (error) {
    // 存档损坏 / 隐私模式禁用 storage：当作没有存档，不能让恢复逻辑把抽屉搞崩
    return null
  }
}

function writeStoredSession(payload) {
  try {
    window.localStorage.setItem(storageKey(scene.value), JSON.stringify(payload))
  } catch (error) {
    // 配额满 / 隐私模式：只影响“下次能否恢复”，不阻断当前对话
  }
}

function removeStoredSession(sceneValue) {
  try {
    window.localStorage.removeItem(storageKey(sceneValue))
  } catch (error) {
    // 忽略：清不掉存档也不该影响“新会话”本身
  }
}

/** UI 消息 → 可落盘结构：去掉 streaming 等运行时状态，只保留可回放的字段 */
function toStoredMessage(msg) {
  const content = typeof msg.content === 'string' ? msg.content : ''
  const clipped = content.length > MAX_STORED_CONTENT_CHARS
    ? content.slice(0, MAX_STORED_CONTENT_CHARS) + '\n…（本地存档截断）'
    : content
  return {
    id: msg.id,
    role: msg.role,
    content: clipped,
    error: msg.error || '',
    conclusion: msg.conclusion || null,
    dataCaliber: Array.isArray(msg.dataCaliber) ? msg.dataCaliber : [],
    grounding: msg.grounding || null
  }
}

/**
 * 落盘当前场景会话（在每轮对话结束后调用）。
 *
 * 只保存**已完成轮次**（streaming 的消息丢弃）：流式中刷新会丢本轮半截内容，
 * 这比恢复出“半句话”再当完整记录更好——半截回答会被误读成模型原话。
 */
function persistSession() {
  if (suppressPersist) return
  if (!messages.value.length) {
    removeStoredSession(scene.value)
    return
  }
  const list = messages.value.filter((msg) => msg && !msg.streaming && msg.role).map(toStoredMessage)
  let trimmed = 0
  while (list.length > MAX_STORED_MESSAGES) {
    list.shift()
    trimmed += 1
  }
  let payload = {
    conversationId: conversationId.value || null,
    focusCode: focusCode.value || '',
    messages: list,
    dropped: droppedCount.value + trimmed,
    updatedAt: Date.now()
  }
  let json = JSON.stringify(payload)
  // 超字节上限：继续从最旧丢，保证存档不会因为一次长对话把 localStorage 打满
  while (byteLength(json) > MAX_STORED_BYTES && list.length > 1) {
    list.shift()
    trimmed += 1
    payload = Object.assign({}, payload, { messages: list, dropped: droppedCount.value + trimmed })
    json = JSON.stringify(payload)
  }
  droppedCount.value = payload.dropped
  writeStoredSession(payload)
}

/**
 * 恢复本场景上次会话（仅在内存为空时调用，避免覆盖正在进行的对话）。
 *
 * ⚠️ 恢复后**绝不复用旧 lastEventId**：它属于已经断掉的那条流，
 * 带上会被服务端当成续传请求（“会话不存在或无可续传”那条实测坑）。
 */
function restoreStoredSession(sceneValue) {
  if (messages.value.length > 0 || conversationId.value) return false
  const payload = readStoredSession(sceneValue)
  if (!payload || payload.messages.length === 0) return false
  const restored = payload.messages.map((item) => ({
    id: Number(item.id) || 0,
    role: item.role === 'user' ? 'user' : 'assistant',
    content: typeof item.content === 'string' ? item.content : '',
    streaming: false,
    error: item.error || '',
    conclusion: item.conclusion || null,
    evidenceExpanded: false,
    dataCaliber: Array.isArray(item.dataCaliber) ? item.dataCaliber : [],
    grounding: item.grounding || null
  }))
  messages.value = restored
  messageSeq = restored.reduce((max, item) => Math.max(max, item.id || 0), 0)
  conversationId.value = payload.conversationId || undefined
  focusCode.value = typeof payload.focusCode === 'string' ? payload.focusCode : ''
  lastEventId.value = undefined
  droppedCount.value = Number(payload.dropped) || 0
  const droppedTip = droppedCount.value > 0
    ? '（较早的 ' + droppedCount.value + ' 条消息因存储上限未恢复）'
    : ''
  restoredNotice.value = '已恢复上次会话' + droppedTip + '；点右上角「+」可开启新会话并清空本地存档'
  scrollToBottom()
  return true
}

/** 服务端明确表示“这个 thread 没了/不可续传”→ 清本地存档（4-1，会话失效路径） */
function isSessionInvalid(error) {
  const message = String((error && error.message) || '')
  // 注意不要把「登录过期（无效的会话…）」算进来：那不是 thread 失效，清聊天存档会误伤
  return /会话不存在|无可续传|会话已失效|无有效会话/.test(message)
}

/**
 * 当前场景项（在**动态** scenes 里查；找不到返回 null，文案回落内置 2，不抛错）。
 * 放在快捷问题/欢迎语之前：两者都依赖它判断“是否自定义场景”。
 */
const currentScene = computed(() => {
  return scenes.value.filter((item) => item.value === scene.value)[0] || null
})

const quickQuestions = computed(() => {
  // 自定义场景用通用兜底（见 CUSTOM_QUICK_QUESTIONS）；模板固定读 [0]/[1]，故兜底必须有 2 项
  if (currentScene.value && currentScene.value.custom) return CUSTOM_QUICK_QUESTIONS
  return QUICK_QUESTIONS[scene.value] || QUICK_QUESTIONS[2]
})

/**
 * 欢迎语：自定义智能体只承诺“工具由管理员配置、以实际查询为准、不能改业务数据”，
 * 不复用内置场景的专属承诺（补货依据/设备诊断/分析口径都不该套到自定义场景上）。
 */
const greeting = computed(() => {
  if (currentScene.value && currentScene.value.custom) {
    return (
      '你好，我是自定义智能体「' + currentScene.value.label + '」。' +
      '我的可用工具由管理员在配置台按场景下发，能力以实际查询结果为准；' +
      '我不能修改任何业务数据（建单/调整/停用请到对应业务页面人工操作）。'
    )
  }
  return GREETINGS[scene.value] || GREETINGS[2]
})

function close() {
  agentStore.close()
}

/**
 * 置信度 → 进度条刻度（**刻度不是概率**）。
 *
 * 为什么单独映射而不是直接用后端字段：后端给的是语义档位（high/medium/low/none），
 * 百分比只用于画条；把映射放在一处并在卡片上写明“尺度不是概率”，
 * 免得以后有人把它当概率展示。
 */
const CONFIDENCE_SCALE = { high: 80, medium: 55, low: 30, none: 0 }
const CONFIDENCE_LABEL = {
  high: '证据充分',
  medium: '证据有限',
  low: '证据不足',
  none: '无异常证据'
}

function confidencePercent(value) {
  const hit = CONFIDENCE_SCALE[value]
  return hit === undefined ? 0 : hit
}

function confidenceLabel(value) {
  return CONFIDENCE_LABEL[value] || String(value || '—')
}

/**
 * 证据档位的中文标签（2-4 证据明细展开用）。
 * 为什么不在模板里写三元：档位与颜色是两处（文案 + class），集中在这里避免漂移。
 */
const EVIDENCE_SEVERITY_LABEL = { high: '高', medium: '中', info: '信息' }

function severityLabel(value) {
  return EVIDENCE_SEVERITY_LABEL[value] || String(value || '—')
}

function renderMarkdown(text) {
  if (!text) return ''
  // 必须经过 DOMPurify（AGENTS §2.2/§7.7：v-html 只允许渲染 sanitize 后的内容）
  return DOMPurify.sanitize(marked.parse(text))
}

/** 口径帧里的窗口起止只要日期（完整 ISO 时间戳对阅读没帮助） */
function shortDate(value) {
  const text = String(value || '')
  return text.length >= 10 ? text.slice(0, 10) : text
}

/** 计数项 → 一行中文（“订单异常 0 / 库存异常 0”）；空计数返回空串，不画空行 */
function countText(counts) {
  if (!counts) return ''
  return Object.keys(counts)
    .map((key) => `${key} ${counts[key]}`)
    .join(' / ')
}

function scrollToBottom() {
  nextTick(() => {
    const el = bodyRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function pushMessage(role, content) {
  messageSeq += 1
  const msg = {
    id: messageSeq,
    role,
    content,
    streaming: false,
    error: '',
    conclusion: null,
    // 2-4：证据明细默认折叠（展开状态属于该条消息自己的 UI 状态）
    evidenceExpanded: false,
    // 口径帧与保真核对（FIX-27）：没调工具时为空数组/空对象，前端据此不画
    dataCaliber: [],
    grounding: null
  }
  messages.value.push(msg)
  return msg
}

function resetConversation() {
  stop()
  messages.value = []
  conversationId.value = undefined
  lastEventId.value = undefined
  focusCode.value = ''
  draft.value = ''
  // 明确“新会话”= 清掉本场景的本地存档：否则下次打开抽屉会把用户刚清掉的旧会话又恢复出来
  removeStoredSession(scene.value)
  restoredNotice.value = ''
  droppedCount.value = 0
  suppressPersist = false
}

/**
 * 应用后端下发的场景列表（3-3）。
 *
 * 当前 scene 不在新列表时回落到 2（补货）：否则 tab 一个都不高亮，后续请求还会带着
 * 后端已不再接受的 scene（该自定义智能体已被停用/删除）。旧 thread 属于那个已不存在的
 * 场景，继续复用会把两种图路由混进同一条会话，所以一并开启新会话。
 * 只有确实存在旧会话时才提示，避免“一打开抽屉就弹一条通知”。
 */
function applyScenes(agents) {
  const next = mergeScenes(agents)
  scenes.value = next
  if (next.some((item) => item.value === scene.value)) return
  const previousScene = scene.value
  const hadConversation = !!conversationId.value
  scene.value = 2
  // 旧场景已不可用：连同它的本地存档一起清掉（否则刷新会恢复出已删除智能体的会话）
  removeStoredSession(previousScene)
  resetConversation()
  if (hadConversation) ElMessage.info('当前智能体已不可用，已回落到「补货 Agent」并开启新会话')
}

function switchScene(value) {
  if (value === scene.value) return
  const previousScene = scene.value
  // 场景隔离：切走即清旧场景存档，切回来/刷新不会恢复出跨场景的旧 thread
  removeStoredSession(previousScene)
  scene.value = value
  // 为什么切场景要新开会话：scene 决定 Python 侧的路由图（Phase 1 起），
  // 跨场景复用同一 thread 会让上下文语义混乱，因此明确重置并提示。
  resetConversation()
  // label 从**动态列表**取（自定义场景不在原硬编码表里，查不到就退回 scene 编号，不抛错）
  const hit = scenes.value.filter((s) => s.value === value)[0]
  ElMessage.info('已切换到「' + (hit ? hit.label : '场景 ' + value) + '」，已开启新会话')
}

/**
 * 探测网关/智能体就绪状态（首次打开时自动探测一次）
 * @param {boolean} force 是否强制重新探测（降级横幅上的"重试"）
 */
async function probeStatus(force) {
  if (probed && !force) return
  probed = true
  const status = await probeAgentStatus()
  // 3-3：场景清单与启用状态**互不影响**。agents 缺失/为空（老后端、上游不可达、配置表为空）
  // 一律回落硬编码 3 项；这里只更新列表，不参与 degraded 判定——场景清单取不到不该让抽屉不可用。
  // 重试（force=true）会重新走这里，因此恢复后能刷新出新下发的自定义场景。
  applyScenes(status && status.agents)
  if (status.unauthorized) {
    agentStore.setDegraded(true, '登录状态已过期，请重新登录后再使用 AI 助手')
  } else if (!status.enabled) {
    agentStore.setDegraded(true, '智能体服务未启用（agent.enabled=false），请联系管理员')
  } else if (status.upstream !== 'up') {
    agentStore.setDegraded(true, '智能体服务暂不可用（Python 服务未就绪），稍后重试')
  } else {
    agentStore.setDegraded(false, '')
  }
}

/**
 * 打开抽屉时的两件事：探测状态 +（可能）消费入口透传的预置问题。
 *
 * 2-6：设备/点位页的「AI 诊断」会带 scene=3 与一句预置问题进来，这里自动发起；
 * 2-8：若状态探测为降级，**不自动发**——那会把用户带进一个必然失败的请求，
 * 保持降级横幅、把问题留在输入框里，等恢复后用户一点就能发。
 */
watch(visible, async (val) => {
  if (!val) return
  const pending = agentStore.consumePending()
  if (!pending.prompt) {
    // 4-1：刷新/重进后自动恢复上次会话（内存为空才恢复，绝不覆盖正在进行的会话）
    restoreStoredSession(scene.value)
    probeStatus(false)
    return
  }
  await probeStatus(true)
  // 从设备/点位页跳进来 = 一次**新的诊断会话**：scene 决定 Python 侧图路由，
  // 复用旧会话会把不同场景的消息混在同一条 thread 里。
  conversationId.value = undefined
  messages.value = []
  focusCode.value = ''
  lastEventId.value = undefined
  if (pending.scene) scene.value = pending.scene
  // 入口透传的预置问题=新会话：清掉该场景旧存档，不能恢复出半路会话
  removeStoredSession(scene.value)
  restoredNotice.value = ''
  droppedCount.value = 0
  draft.value = pending.prompt
  if (!degraded.value) await send(pending.prompt)
})

/** 单帧处理：协议见 dkd-agent/app/api/chat.py（meta / delta / done / error） */
function handleFrame(frame, assistantMsg) {
  const data = frame.parsed || {}
  if (frame.id) lastEventId.value = frame.id
  if (frame.event === 'meta') {
    if (data.conversation_id) conversationId.value = data.conversation_id
    // 记录服务端本轮的模式：echo 必须显式告知用户（否则看起来就是“坏了”）
    turnMode.value = data.mode || ''
  } else if (frame.event === 'delta') {
    assistantMsg.content += data.text || ''
    scrollToBottom()
  } else if (frame.event === 'done') {
    // 模型名只在 done 帧才有（服务端按**实际返回**的模型回填，不按配置猜）
    if (data.model) turnModel.value = data.model
    // 结构化诊断结论（2-4）：服务端只在真调了诊断工具时带，否则严格为 null
    if (data.conclusion) assistantMsg.conclusion = data.conclusion
    // 口径帧（FIX-27）：**本轮**工具返回的窗口与计数（服务端已按“本轮”限定，不会带上几轮的）
    if (data.dataCaliber) assistantMsg.dataCaliber = data.dataCaliber
    if (data.grounding) assistantMsg.grounding = data.grounding
    // 多轮问诊的焦点（2-3）：后端确定性算出，前端只负责显示
    if (typeof data.focusInnerCode === 'string') focusCode.value = data.focusInnerCode
    if (data.mode && !turnMode.value) turnMode.value = data.mode
  } else if (frame.event === 'error') {
    assistantMsg.error = data.msg || '智能体返回错误'
    // degrade=true 表示服务不可用（网关合成），此时禁用输入而不是让用户反复失败
    if (data.degrade) agentStore.setDegraded(true, data.msg)
  }
}

async function send(text) {
  const content = (text || '').trim()
  if (!content || sending.value || degraded.value) return
  draft.value = ''
  pushMessage('user', content)
  const assistantMsg = pushMessage('assistant', '')
  assistantMsg.streaming = true
  sending.value = true
  lastEventId.value = undefined // 新一轮：清空续传锚点（见 lastEventId 注释）
  scrollToBottom()
  // loading 状态必须在 finally 复位（AGENTS §2.2）
  try {
    stream = chatWithAgent({
      message: content,
      conversationId: conversationId.value,
      scene: scene.value,
      onEvent: (frame) => handleFrame(frame, assistantMsg)
    })
    await stream.promise
  } catch (e) {
    if (e && e.aborted) {
      if (assistantMsg.content) assistantMsg.content += '\n\n_（已取消）_'
      else assistantMsg.content = '_（已取消）_'
    } else if (e && e.retryable && (await tryResume(content, assistantMsg))) {
      // 已自动续接完成：不当作失败（断线不丢已有内容）
    } else {
      assistantMsg.error = (e && e.message) || '对话失败'
      // 服务端判定会话已失效（会话不存在/无可续传）：清掉本地存档并丢弃 conversationId，
      // 下一次提问自动开启新会话。保留本页转录供用户回看，但**不再落盘**——
      // 否则 finally 里的 persistSession 会把刚清掉的 key 又写回来。
      if (isSessionInvalid(e)) {
        removeStoredSession(scene.value)
        conversationId.value = undefined
        restoredNotice.value = ''
        droppedCount.value = 0
        suppressPersist = true
        ElMessage.warning('原会话已在智能体服务侧失效（可能已过期或被清理），下一次提问将开启新会话')
      }
      // 登录过期不属“智能体故障”，提示要区分，否则运维会去查错东西
      if (e && e.unauthorized) agentStore.setDegraded(true, '登录状态已过期，请重新登录后再使用 AI 助手')
      else if (e && e.degraded) agentStore.setDegraded(true, e.message)
    }
  } finally {
    assistantMsg.streaming = false
    sending.value = false
    stream = null
    scrollToBottom()
    // 4-1：一轮结束后落盘（只存已完成轮次），刷新/重进可恢复 transcript 与 conversationId
    persistSession()
    suppressPersist = false
  }
}

/**
 * 断线自动续接（最多尝试一次）
 *
 * 命中条件：错误可重试（非用户取消/非信封类错误）+ 同一会话 + 已有帧锚点。
 * 服务端不会重跑对话图，只补发未收到的帧，因此不会重复计费也不会重复追加上下文。
 *
 * @returns {Promise<boolean>} 是否续接成功
 */
async function tryResume(originalMessage, assistantMsg) {
  if (!conversationId.value || !lastEventId.value) return false
  try {
    stream = resumeAgentStream({
      message: originalMessage,
      conversationId: conversationId.value,
      scene: scene.value,
      lastEventId: lastEventId.value,
      onEvent: (frame) => handleFrame(frame, assistantMsg)
    })
    await stream.promise
    return true
  } catch (e) {
    return false
  }
}

function askQuick(text) {
  if (sending.value || degraded.value) return
  send(text)
}

function stop() {
  if (stream) {
    stream.abort()
    stream = null
  }
}

function onEnter(event) {
  // 输入法组合中（中文候选）回车不发送
  if (event && event.isComposing) return
  event.preventDefault()
  send(draft.value)
}

onBeforeUnmount(stop)
</script>

<style lang="scss" scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.agent-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid #ebeef5;

  .avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: linear-gradient(135deg, #409eff, #7b5cff);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
  }

  .title {
    display: flex;
    flex-direction: column;
    line-height: 1.3;

    b {
      font-size: 14px;
    }

    span {
      font-size: 11px;
      color: #909399;
    }
  }

  .head-btn {
    margin-left: auto;
    color: #909399;

    & + .head-btn {
      margin-left: 0;
    }
  }
}

.agent-tabs {
  display: flex;
  /* 自定义智能体数量不固定，tab 必须能换行——否则下发第 4、5 个场景后会横向溢出被裁掉 */
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 16px;
  border-bottom: 1px solid #ebeef5;

  .tab {
    padding: 4px 10px;
    border-radius: 14px;
    font-size: 12px;
    background: #f4f4f5;
    color: #909399;
    cursor: pointer;

    &.on {
      background: #ecf5ff;
      color: #409eff;
      font-weight: 600;
    }
  }
}

.degrade-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #fdf6ec;
  border-bottom: 1px solid #faecd8;
  font-size: 12px;
  color: #e6a23c;

  .txt {
    flex: 1;
  }
}

/* 回显模式提示：与 degraded 区分开——服务是好的，只是没接真模型，用蓝色“状态”色而非警告色 */
.mode-bar {
  padding: 8px 16px;
  background: #ecf5ff;
  border-bottom: 1px solid #d9ecff;
  font-size: 12px;
  line-height: 1.7;
  color: #409eff;

  strong {
    font-weight: 600;
  }

  code {
    background: rgba(64, 158, 255, 0.12);
    padding: 0 4px;
    border-radius: 3px;
    font-size: 11px;
  }
}

/* 真模型时的轻量标识（证明“确实调了模型”，模型名按服务端实际返回显示） */
.model-badge {
  padding: 4px 16px;
  background: #fafbfc;
  border-bottom: 1px solid #eef0f3;
  font-size: 11px;
  color: #909399;
}

/* 当前诊断设备提示条（2-3）：后端早已记住，这里只是把它变成“看得见” */
.focus-bar {
  padding: 6px 16px;
  background: #f0f9eb;
  border-bottom: 1px solid #e1f3d8;
  font-size: 12px;
  color: #529b2e;

  strong {
    font-weight: 600;
  }
}

/* 4-1 会话恢复提示：用“成功色”而不是警告色——会话找回是好事，不是故障 */
.restore-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  background: #f0f9eb;
  border-bottom: 1px solid #e1f3d8;
  font-size: 12px;
  color: #529b2e;

  .txt {
    flex: 1;
    line-height: 1.6;
  }
}

.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  background: #fafbfc;

  .msg {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;

    .ava {
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: #f0f2f5;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      flex-shrink: 0;
    }

    .bubble {
      max-width: 88%;
      padding: 9px 12px;
      border-radius: 8px;
      background: #fff;
      border: 1px solid #ebeef5;
      font-size: 13px;
      line-height: 1.7;
      word-break: break-word;

      .muted {
        color: #909399;
        font-size: 12px;
      }
    }

    &.user {
      flex-direction: row-reverse;

      .bubble {
        background: #ecf5ff;
        border-color: #d9ecff;
        color: #303133;
      }
    }

    .caret {
      color: #409eff;
    }

    /* 工具轮次占位：与正文区分开（灰色 + 呼吸感），且不带光标闪动，避免被当成“正在打字” */
    .querying {
      color: #909399;
      font-size: 12px;
      animation: agent-querying-pulse 1.2s ease-in-out infinite;
    }

    @keyframes agent-querying-pulse {
      0%,
      100% {
        opacity: 0.45;
      }

      50% {
        opacity: 1;
      }
    }

    /* 结论卡片：与正文区分（浅色底 + 左侧色条），并与气泡的 markdown 内容分隔 */
    .conclusion {
      margin-top: 8px;
      padding: 8px 10px;
      border-radius: 6px;
      background: #f8fafc;
      border: 1px solid #e4e7ed;
      border-left: 3px solid #409eff;

      .conclusion-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 12px;
        margin-bottom: 6px;
      }

      .conf-badge {
        padding: 1px 8px;
        border-radius: 10px;
        background: #ecf5ff;
        color: #409eff;
        font-size: 11px;
      }

      .conf-bar {
        margin-bottom: 4px;
      }

      .conclusion-basis {
        font-size: 12px;
        color: #606266;
        line-height: 1.6;
      }

      .conclusion-block {
        margin-top: 6px;

        .t {
          font-size: 12px;
          color: #303133;
          font-weight: 600;
          margin-bottom: 2px;
        }

        ul {
          margin: 0;
          padding-left: 18px;

          li {
            font-size: 12px;
            color: #606266;
            line-height: 1.6;
          }
        }
      }

      .conclusion-foot {
        margin-top: 6px;
        padding-top: 4px;
        border-top: 1px dashed #e4e7ed;
        font-size: 11px;
        color: #909399;
      }

      /* 2-4 证据明细：默认折叠，点标题展开；档位用颜色区分（高红 / 中橙 / 信息灰） */
      .evidence {
        .evidence-toggle {
          cursor: pointer;
          user-select: none;

          .caret {
            display: inline-block;
            width: 12px;
            color: #909399;
            font-weight: 400;
          }
        }

        .evidence-list {
          list-style: none;
          padding-left: 12px;
          margin-top: 4px;

          li {
            display: flex;
            flex-wrap: wrap;
            align-items: baseline;
            gap: 6px;
            padding: 3px 0;
            border-bottom: 1px dashed #ebeef5;

            &:last-child {
              border-bottom: none;
            }
          }

          .sev {
            flex: none;
            padding: 0 6px;
            border-radius: 8px;
            font-size: 11px;
            line-height: 18px;

            &.sev-high {
              background: #fef0f0;
              color: #f56c6c;
            }

            &.sev-medium {
              background: #fdf6ec;
              color: #e6a23c;
            }

            &.sev-info {
              background: #f4f4f5;
              color: #909399;
            }
          }

          .kind {
            flex: none;
            font-size: 11px;
            color: #909399;
            font-family: var(--el-font-family-mono, monospace);
          }

          .summary {
            flex: 1 1 100%;
            font-size: 12px;
            color: #606266;
            line-height: 1.6;
          }
        }
      }
    }

    /* 口径帧（FIX-27）：灰色底 + 代码字体，与“模型说的话”视觉上区分开 */
    .caliber {
      margin-top: 8px;
      padding: 6px 10px;
      border-radius: 6px;
      background: #f4f4f5;
      border: 1px solid #e9e9eb;
      font-size: 12px;
      color: #606266;

      .t {
        font-weight: 600;
        color: #303133;
        margin-bottom: 2px;
      }

      .caliber-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        line-height: 1.7;
      }

      code {
        background: #fff;
        border: 1px solid #e9e9eb;
        border-radius: 3px;
        padding: 0 4px;
      }
    }

    /* 数字保真提示（FIX-27）：模型措辞与工具事实不一致时的提示 */
    .grounding-warn {
      margin-top: 6px;
      padding: 6px 10px;
      border-radius: 6px;
      background: #fdf6ec;
      border: 1px solid #faecd8;
      border-left: 3px solid #e6a23c;
      font-size: 12px;
      color: #b88230;
      line-height: 1.6;
    }

    .bubble-err {
      margin-top: 6px;
      padding-top: 6px;
      border-top: 1px dashed #fde2e2;
      color: #f56c6c;
      font-size: 12px;
    }
  }
}

.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 16px 0;

  .q {
    padding: 4px 10px;
    border-radius: 14px;
    border: 1px solid #ebeef5;
    font-size: 12px;
    color: #606266;
    cursor: pointer;

    &:hover {
      border-color: #409eff;
      color: #409eff;
    }

    &.disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }
}

.chat-input {
  display: flex;
  gap: 8px;
  padding: 10px 16px 16px;
}
</style>

<style lang="scss">
/*
 * 非 scoped：必须命中 Element Plus 抽屉的内部节点（组件被 teleport 到 body，
 * scoped 属性无法作用于 el-drawer 自身），仅重置内层 padding，避免出现嵌套滚动条。
 */
.el-drawer.agent-assistant-drawer {
  --el-drawer-padding-primary: 0;
}
</style>
