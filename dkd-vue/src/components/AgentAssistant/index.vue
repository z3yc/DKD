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

      <!-- 场景切换：scene 参数与 Python 侧契约一致（1-通用 2-补货 3-诊断 4-运营分析） -->
      <div class="agent-tabs">
        <span
          v-for="item in SCENES"
          :key="item.value"
          class="tab"
          :class="{ on: scene === item.value }"
          @click="switchScene(item.value)"
        >{{ item.label }}</span>
      </div>

      <!-- 降级提示：agent.enabled=false 或 Python 不可达（方案 §3.3 要求明确告知，不伪造会话能力） -->
      <div v-if="degraded" class="degrade-bar">
        <span class="txt">{{ degradedReason || '智能体服务暂不可用，多轮对话已暂停' }}</span>
        <el-button link type="primary" @click="probeStatus(true)">重试</el-button>
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
              <span v-html="renderMarkdown(msg.content)"></span>
              <span v-if="msg.streaming" class="caret">▌</span>
            </template>
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
import { chatWithAgent, probeAgentStatus } from '@/api/manage/agent'
import useAgentStore from '@/store/modules/agent'

// marked 配置与 views/manage/report/index.vue 保持一致（v18 用 use 取代 setOptions）
marked.use({ breaks: true, gfm: true })

const SCENES = [
  { value: 2, label: '补货 Agent' },
  { value: 3, label: '诊断 Agent' },
  { value: 4, label: '分析 Copilot' }
]

// 快捷问题与原型 V2 抽屉一致，按场景各给一组（区别于通用问答）
const QUICK_QUESTIONS = {
  2: [
    { text: '先看紧急的' },
    { text: '为什么补这么多可乐？' },
    { text: '暂停明天的自动分析' },
    { text: '上期复盘如何？' }
  ],
  3: [
    { text: '3 号门那台机器不出货' },
    { text: '这台设备近 30 天故障记录' },
    { text: '同类故障一般怎么处理？' },
    { text: '帮我建维修工单' }
  ],
  4: [
    { text: '近 7 天各区域销量排行' },
    { text: '今天各点位销售趋势' },
    { text: '饮品品类占比分布' },
    { text: '上周补货准确率怎么样？' }
  ]
}

const GREETINGS = {
  2: '你好，我是补货 Agent。可以查询待确认建议、解释补货依据，也可以暂停/恢复自动分析。',
  3: '你好，我是设备诊断 Agent。描述设备现象或点位，我来定位可能原因并给出处置建议。',
  4: '你好，我是运营分析 Copilot。用一句话描述要看的数据，我会给出图表与口径说明。'
}

const agentStore = useAgentStore()
const visible = computed({
  get: () => agentStore.drawerVisible,
  set: (val) => (val ? agentStore.open() : agentStore.close())
})

const scene = ref(2)
const draft = ref('')
const sending = ref(false)
const degraded = computed(() => agentStore.degraded)
const degradedReason = computed(() => agentStore.degradedReason)
const messages = ref([])
const bodyRef = ref(null)
// 会话上下文：首轮由服务端下发 conversation_id（Python 侧 thread_id），后续轮次复用实现多轮
const conversationId = ref(undefined)
// 断点续传锚点：记录最后一个 delta 帧 id
const lastEventId = ref(undefined)
// 当前流句柄（用于"停止"与卸载时释放连接）
let stream = null
let messageSeq = 0
// 状态探测只做一次（失败后由"重试"按钮显式触发，避免每次打开都打扰）
let probed = false

const quickQuestions = computed(() => QUICK_QUESTIONS[scene.value] || QUICK_QUESTIONS[2])
const greeting = computed(() => GREETINGS[scene.value] || GREETINGS[2])

function close() {
  agentStore.close()
}

function renderMarkdown(text) {
  if (!text) return ''
  // 必须经过 DOMPurify（AGENTS §2.2/§7.7：v-html 只允许渲染 sanitize 后的内容）
  return DOMPurify.sanitize(marked.parse(text))
}

function scrollToBottom() {
  nextTick(() => {
    const el = bodyRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function pushMessage(role, content) {
  messageSeq += 1
  const msg = { id: messageSeq, role, content, streaming: false, error: '' }
  messages.value.push(msg)
  return msg
}

function resetConversation() {
  stop()
  messages.value = []
  conversationId.value = undefined
  lastEventId.value = undefined
  draft.value = ''
}

function switchScene(value) {
  if (value === scene.value) return
  scene.value = value
  // 为什么切场景要新开会话：scene 决定 Python 侧的路由图（Phase 1 起），
  // 跨场景复用同一 thread 会让上下文语义混乱，因此明确重置并提示。
  resetConversation()
  ElMessage.info('已切换到「' + SCENES.filter((s) => s.value === value)[0].label + '」，已开启新会话')
}

/**
 * 探测网关/智能体就绪状态（首次打开时自动探测一次）
 * @param {boolean} force 是否强制重新探测（降级横幅上的"重试"）
 */
async function probeStatus(force) {
  if (probed && !force) return
  probed = true
  const status = await probeAgentStatus()
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

watch(visible, (val) => {
  if (val) probeStatus(false)
})

/** 单帧处理：协议见 dkd-agent/app/api/chat.py（meta / delta / done / error） */
function handleFrame(frame, assistantMsg) {
  const data = frame.parsed || {}
  if (frame.id) lastEventId.value = frame.id
  if (frame.event === 'meta') {
    if (data.conversation_id) conversationId.value = data.conversation_id
  } else if (frame.event === 'delta') {
    assistantMsg.content += data.text || ''
    scrollToBottom()
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
  scrollToBottom()
  // loading 状态必须在 finally 复位（AGENTS §2.2）
  try {
    stream = chatWithAgent({
      message: content,
      conversationId: conversationId.value,
      scene: scene.value,
      lastEventId: lastEventId.value,
      onEvent: (frame) => handleFrame(frame, assistantMsg)
    })
    await stream.promise
  } catch (e) {
    if (e && e.aborted) {
      if (assistantMsg.content) assistantMsg.content += '\n\n_（已取消）_'
      else assistantMsg.content = '_（已取消）_'
    } else {
      assistantMsg.error = (e && e.message) || '对话失败'
      // 登录过期不属“智能体故障”，提示要区分，否则运维会去查错东西
      if (e && e.unauthorized) agentStore.setDegraded(true, '登录状态已过期，请重新登录后再使用 AI 助手')
      else if (e && e.degraded) agentStore.setDegraded(true, e.message)
    }
  } finally {
    assistantMsg.streaming = false
    sending.value = false
    stream = null
    scrollToBottom()
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
