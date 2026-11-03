<template>
  <el-drawer
    :model-value="modelValue"
    :title="drawerTitle"
    size="760px"
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="onVisibleChange"
  >
    <div v-loading="loading" class="form-wrap">
      <el-skeleton v-if="loading" :rows="10" animated />

      <el-empty v-else-if="loadFailed" :description="'配置详情加载失败：' + failMsg">
        <el-button type="primary" @click="loadDetail">重试</el-button>
        <el-button @click="close">关闭</el-button>
      </el-empty>

      <el-form v-else-if="form" ref="formRef" :model="form" :rules="rules" label-width="128px" class="config-form">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          :title="mode === 'add' ? '新增自定义智能体' : '编辑智能体配置'"
          :description="mode === 'add'
            ? '本期自定义智能体只放开 chat 载体（契约 Q1）：编码 3~32 位小写字母/数字/下划线，场景码由后端自动分配（≥101，1~4 为内置保留）。'
            : '保存后对该智能体的下一次调用生效（进行中的运行不受影响）；每次保存 +1 版本并写入历史。内置智能体的编码/运行载体/内置标记只读。'"
        />

        <el-alert
          v-if="metaFailed"
          type="warning"
          :closable="false"
          show-icon
          title="元数据加载失败，暂不能保存"
          class="form-alert"
        >
          参数区间 / 工具白名单 / 默认提示词都来自后端 meta；缺了它保存会把参数写空，
          等于让配置静默失效。
          <el-button link type="primary" @click="loadMeta(form.runtime, {})">重试加载元数据</el-button>
        </el-alert>

        <!-- ============ 基本信息 ============ -->
        <div class="section-title">基本信息</div>
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" maxlength="30" placeholder="如 点位巡检 Copilot" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="编码" prop="code">
              <el-input
                v-model="form.code"
                :disabled="mode === 'edit'"
                maxlength="32"
                placeholder="小写字母开头，3~32 位"
              />
              <div v-if="mode === 'edit'" class="hint">编码是配置的唯一标识，创建后不可修改</div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item label="运行载体">
              <el-input :model-value="runtimeText" disabled />
              <div v-if="mode === 'add'" class="hint">本期新增只放开 chat 载体（Q1）</div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="场景码">
              <el-input :model-value="sceneText" disabled />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="用途与边界" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            maxlength="200"
            show-word-limit
            placeholder="必填：这个智能体负责什么、不负责什么（会显示在列表页，避免运维选错智能体）"
          />
        </el-form-item>

        <!-- ============ 模型与调用 ============ -->
        <div class="section-title">模型与调用</div>
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item label="模型">
              <el-select v-model="form.model" clearable placeholder="跟随环境变量 DKD_AGENT_LLM_MODEL" style="width: 100%">
                <el-option v-for="item in modelOptions" :key="item" :label="item" :value="item" />
              </el-select>
              <div class="hint">留空 = 走环境默认（未配置 = 现状）；可选项由后端 meta.models 下发</div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="温度">
              <div class="inline-field">
                <el-input-number
                  :model-value="form.temperature"
                  :min="0"
                  :max="2"
                  :step="0.1"
                  :precision="1"
                  controls-position="right"
                  placeholder="模型默认"
                  style="width: 130px"
                  @update:model-value="(val) => (form.temperature = val)"
                />
                <el-button link type="primary" @click="form.temperature = null">用模型默认</el-button>
              </div>
              <div class="hint" :class="{ 'hint-warn': temperatureWarn }">
                <template v-if="temperatureWarn">
                  当前 {{ form.temperature }} &gt; 1.2：回答发散风险明显增加（后端不拒绝，但建议 ≤1.2）
                </template>
                <template v-else>null = 模型默认；范围 0~2</template>
              </div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item label="输出上限">
              <div class="inline-field">
                <el-input-number
                  :model-value="form.maxTokens"
                  :min="256"
                  :max="8192"
                  :step="256"
                  controls-position="right"
                  placeholder="模型默认"
                  style="width: 150px"
                  @update:model-value="(val) => (form.maxTokens = val)"
                />
                <el-button link type="primary" @click="form.maxTokens = null">用模型默认</el-button>
              </div>
              <div class="hint">max_tokens：null = 模型默认；范围 256~8192</div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="超时 / 重试">
              <div class="inline-field">
                <el-input-number v-model="form.timeoutS" :min="5" :max="300" :step="5" controls-position="right" style="width: 120px" />
                <span class="inline-unit">秒</span>
                <el-input-number v-model="form.maxRetries" :min="0" :max="5" controls-position="right" style="width: 100px" />
                <span class="inline-unit">次</span>
              </div>
              <div class="hint">超时 5~300 秒；重试 0~5 次（4xx 重试不会变好，只有超时/5xx 才重试）</div>
            </el-form-item>
          </el-col>
        </el-row>

        <!-- ============ 提示词 ============ -->
        <div class="section-title">系统提示词</div>
        <el-form-item v-if="form.runtime === 'fault'" label="提示词">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="诊断载体不调用 LLM"
            description="诊断结论由 Python 侧规则合成，提示词配置不生效；保存时置 NULL（契约 §1）。"
          />
        </el-form-item>
        <el-form-item v-else label="提示词" prop="systemPrompt">
          <PromptEditor
            v-model="form.systemPrompt"
            :runtime="form.runtime"
            :placeholders="placeholders"
            :default-prompt="defaultPrompt"
          />
        </el-form-item>

        <!-- ============ 工具 ============ -->
        <div class="section-title">可用工具</div>
        <el-form-item v-if="form.runtime !== 'chat'" label="工具子集">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="该载体不支持工具子集"
            description="补货 / 诊断载体的流程由代码固定，保存时 tools 置空（契约 §4.4）。"
          />
        </el-form-item>
        <el-form-item v-else label="工具集" prop="tools">
          <ToolPicker v-model="form.tools" :tools="tools" />
        </el-form-item>

        <!-- ============ 参数 ============ -->
        <div class="section-title">业务参数</div>
        <ParamFields
          v-model="form.paramsJson"
          :specs="paramSpecs"
          :defaults="defaultParams"
          :runtime="form.runtime"
          :disabled="form.runtime === 'chat'"
        />

        <!-- ============ 变更说明 ============ -->
        <template v-if="mode === 'edit'">
          <div class="section-title">变更说明</div>
          <el-form-item label="本次变更" prop="changeNote">
            <el-input
              v-model="form.changeNote"
              maxlength="200"
              show-word-limit
              placeholder="选填：写清这次为什么改（会写进版本历史，事后复盘靠它）"
            />
          </el-form-item>
        </template>
      </el-form>
    </div>

    <template #footer>
      <span v-if="form && form.version" class="footer-tip">
        当前版本 v{{ form.version }}；保存后 +1 并写入版本历史
      </span>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="saveDisabled" @click="submit">保存</el-button>
    </template>
  </el-drawer>
</template>

<script setup name="AgentConfigForm">
import { ElMessage } from 'element-plus'
import ParamFields from './ParamFields.vue'
import PromptEditor from './PromptEditor.vue'
import ToolPicker from './ToolPicker.vue'
import { addAgentConfig, getAgentConfig, getAgentConfigMeta, updateAgentConfig } from '@/api/manage/agentConfig'

/**
 * S3/S4 · 新增 / 编辑抽屉（契约 §6.3）。
 *
 * 关键约束（每条都有“为什么”）：
 *  1. 编辑时**先拉详情再渲染表单**：列表 VO 不一定带全量字段（如完整提示词），
 *     直接用列表行回填再保存，会把没展示出来的字段覆盖成空值。
 *  2. 详情/元数据加载失败时**禁止保存**：后端 meta 提供参数区间、工具白名单、
 *     默认提示词；缺了它保存会把 paramsJson 写空、把工具清空——配置看起来保存成功，
 *     实际静默失效。
 *  3. 启停状态在表单里只读：S5 的启停有独立的二次确认 + 停用原因（契约 §6.3），
 *     如果表单也能改 enabled，就会出现“保存顺手把停用改回启用”且没写原因。
 *     保存时把原 enabled/disabledReason 原样带回，避免编辑别的字段时意外改变启停。
 *  4. 409 不在这里弹提示：父组件统一用 ElMessageBox 提示并刷新详情（否则会和
 *     request.js 的拦截器各弹一条）。本组件只关掉抽屉，避免用户拿着旧 version 反复重试。
 */
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'edit' // add | edit
  },
  configId: {
    type: [Number, String],
    default: null
  }
})
const emit = defineEmits(['update:modelValue', 'saved', 'conflict'])

const formRef = ref(null)
const form = ref(null)
const meta = ref(null)
const loading = ref(false)
const loadFailed = ref(false)
const failMsg = ref('')
const metaFailed = ref(false)
const saving = ref(false)

const RUNTIME_TEXT = {
  chat: '对话载体（chat）',
  restock: '补货载体（restock）',
  fault: '诊断载体（fault）'
}

const drawerTitle = computed(() => {
  const prefix = props.mode === 'add' ? '新增自定义智能体' : '编辑智能体配置'
  return form.value && form.value.name ? prefix + '：' + form.value.name : prefix
})

const placeholders = computed(() => (meta.value && meta.value.placeholders) || [])
const defaultPrompt = computed(() => (meta.value && meta.value.defaultPrompt) || '')
const paramSpecs = computed(() => (meta.value && meta.value.paramSpecs) || [])
const defaultParams = computed(() => (meta.value && meta.value.defaultParams) || {})
const tools = computed(() => (meta.value && meta.value.tools) || [])

const modelOptions = computed(() => {
  const list = ((meta.value && meta.value.models) || []).slice()
  // 配置里若有历史模型名（不在当前白名单），也要显示出来，否则下拉框看起来是空的，
  // 用户一保存就可能把它换掉——而“显示原值”至少让改动是显式的
  if (form.value && form.value.model && list.indexOf(form.value.model) < 0) list.unshift(form.value.model)
  return list
})

const runtimeText = computed(() => (form.value && RUNTIME_TEXT[form.value.runtime]) || '—')

const sceneText = computed(() => {
  if (!form.value) return '—'
  if (props.mode === 'add') return '保存后自动分配（≥101）'
  if (form.value.runtime === 'chat' && form.value.builtin === 1) return '1-4（内置四场景共用）'
  return form.value.scene === null || form.value.scene === undefined ? '—' : String(form.value.scene)
})

const temperatureWarn = computed(() => {
  if (!form.value) return false
  const value = form.value.temperature
  return value !== null && value !== undefined && Number(value) > 1.2
})

/** 元数据失败时禁止保存（理由见文件头第 2 条） */
const saveDisabled = computed(() => {
  return loading.value || loadFailed.value || metaFailed.value
})

const rules = computed(() => {
  const result = {
    name: [
      { required: true, message: '请输入智能体名称', trigger: 'blur' },
      { min: 2, max: 30, message: '名称需 2~30 字', trigger: 'blur' }
    ],
    description: [
      { required: true, message: '请填写用途与边界（≤200 字）', trigger: 'blur' },
      { max: 200, message: '描述不超过 200 字', trigger: 'blur' }
    ],
    changeNote: [{ max: 200, message: '变更说明不超过 200 字', trigger: 'blur' }]
  }
  // 新增才校验编码：编辑时编码只读，不该因为历史编码不合当前规则而卡住保存
  if (props.mode === 'add') {
    result.code = [
      { required: true, message: '请输入编码', trigger: 'blur' },
      {
        pattern: /^[a-z][a-z0-9_]{2,31}$/,
        message: '编码需为 3~32 位小写字母/数字/下划线，且以字母开头',
        trigger: 'blur'
      }
    ]
  }
  if (form.value && form.value.runtime !== 'fault') {
    result.systemPrompt = [{ validator: validatePrompt, trigger: 'blur' }]
  }
  if (form.value && form.value.runtime === 'chat') {
    result.tools = [{ validator: validateTools, trigger: 'change' }]
  }
  return result
})

// ---------------------------------------------------------------------------
// 数据加载与回填
// ---------------------------------------------------------------------------
function init() {
  loadFailed.value = false
  failMsg.value = ''
  metaFailed.value = false
  meta.value = null
  if (formRef.value) formRef.value.clearValidate()
  if (props.mode === 'add') {
    prepareAdd()
  } else {
    loadDetail()
  }
}

function prepareAdd() {
  loading.value = false
  // 新增：runtime 固定 chat（Q1），scene 由后端分配；enabled 默认 1（停用走 S5 的独立流程）
  form.value = {
    id: null,
    code: '',
    name: '',
    description: '',
    runtime: 'chat',
    scene: null,
    builtin: 0,
    systemPrompt: '',
    tools: '',
    paramsJson: {},
    model: '',
    temperature: null,
    maxTokens: null,
    timeoutS: 60,
    maxRetries: 2,
    enabled: 1,
    disabledReason: null,
    version: null,
    changeNote: ''
  }
  loadMeta('chat', { prefill: true })
}

function loadDetail() {
  loading.value = true
  loadFailed.value = false
  failMsg.value = ''
  form.value = null
  getAgentConfig(props.configId)
    .then((response) => {
      const data = response.data
      if (!data) {
        loadFailed.value = true
        failMsg.value = '接口返回为空'
        return
      }
      form.value = normalize(data)
      loadMeta(form.value.runtime, { prefill: false })
    })
    .catch((error) => {
      loadFailed.value = true
      failMsg.value = (error && error.message) || '请求失败'
    })
    .finally(() => {
      loading.value = false
    })
}

function normalize(data) {
  return {
    id: data.id,
    code: data.code || '',
    name: data.name || '',
    description: data.description || '',
    runtime: data.runtime || 'chat',
    scene: data.scene,
    builtin: data.builtin === null || data.builtin === undefined ? 0 : data.builtin,
    systemPrompt: data.systemPrompt || '',
    tools: data.tools || '',
    // 启停只读：原样带回，避免“编辑提示词顺手把停用改成启用”
    enabled: data.enabled === null || data.enabled === undefined ? 1 : data.enabled,
    disabledReason: data.disabledReason || null,
    paramsJson: parseParams(data.paramsJson),
    model: data.model || '',
    temperature: toNumberOrNull(data.temperature),
    maxTokens: toNumberOrNull(data.maxTokens),
    timeoutS: data.timeoutS === null || data.timeoutS === undefined ? 60 : data.timeoutS,
    maxRetries: data.maxRetries === null || data.maxRetries === undefined ? 2 : data.maxRetries,
    version: data.version,
    changeNote: data.changeNote || ''
  }
}

function loadMeta(runtime, options) {
  if (!runtime) return
  metaFailed.value = false
  getAgentConfigMeta(runtime)
    .then((response) => {
      meta.value = response.data || null
      if (!meta.value) {
        metaFailed.value = true
        return
      }
      if (props.mode === 'add') {
        if (options.prefill) {
          // 新增默认值取代码内置默认，降低“空表单保存出半成品配置”的概率；
          // 用户改不改都是显式动作（保存后面板会展示版本号）
          if (!form.value.systemPrompt) form.value.systemPrompt = meta.value.defaultPrompt || ''
          // 默认全选工具 = 与内置 AI 助手的默认能力面一致；可再裁剪
          if (!form.value.tools) form.value.tools = (meta.value.tools || []).join(',')
          form.value.paramsJson = mergeDefaults(meta.value.defaultParams, form.value.paramsJson)
        }
      } else {
        // 编辑：补齐参数默认值（历史配置可能缺 key），并剔除已下线的工具名
        form.value.paramsJson = mergeDefaults(meta.value.defaultParams, form.value.paramsJson)
        sanitizeTools()
      }
    })
    .catch(() => {
      metaFailed.value = true
    })
}

/** 合并默认参数：缺 key 的补默认值，已有的保留用户/库里的值 */
function mergeDefaults(defaults, current) {
  const merged = Object.assign({}, defaults || {})
  Object.keys(current || {}).forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(merged, key)) merged[key] = current[key]
  })
  return merged
}

/**
 * 剔除不在白名单内的工具名。
 *
 * 为什么不在 ToolPicker 里静默丢弃：用户需要知道“我原来选的工具已经下线了”，
 * 否则保存后能力面悄悄变小，排查时没人知道是这次保存造成的。
 */
function sanitizeTools() {
  if (!form.value || form.value.runtime !== 'chat') return
  const allowed = tools.value
  const current = (form.value.tools || '').split(',').map((name) => name.trim()).filter((name) => name)
  const kept = current.filter((name) => allowed.indexOf(name) >= 0)
  const removed = current.filter((name) => allowed.indexOf(name) < 0)
  if (removed.length) {
    form.value.tools = kept.join(',')
    ElMessage.warning('已剔除不在工具白名单内的工具：' + removed.join('、') + '（它们已下线，保存后不再生效）')
  }
}

// ---------------------------------------------------------------------------
// 校验
// ---------------------------------------------------------------------------
function unknownPlaceholdersOf(text) {
  const allowed = placeholders.value
  const found = []
  const pattern = /\{([a-zA-Z0-9_]+)\}/g
  let match
  while ((match = pattern.exec(text || '')) !== null) {
    const name = match[1]
    if (allowed.indexOf(name) < 0 && found.indexOf(name) < 0) found.push('{' + name + '}')
  }
  return found
}

function validatePrompt(rule, value, callback) {
  const text = value || ''
  if (text.length < 100) {
    callback(new Error('提示词至少 100 字（当前 ' + text.length + ' 字）：过短的设定会让模型行为不稳定'))
    return
  }
  if (text.length > 8000) {
    callback(new Error('提示词最多 8000 字（当前 ' + text.length + ' 字）'))
    return
  }
  const unknown = unknownPlaceholdersOf(text)
  if (unknown.length) {
    callback(
      new Error(
        '不支持的占位符 ' + unknown.join(' ') + '；本载体只允许 ' +
          (placeholders.value.length ? placeholders.value.map((n) => '{' + n + '}').join(' ') : '（无占位符）')
      )
    )
    return
  }
  callback()
}

function validateTools(rule, value, callback) {
  const list = (value || '').split(',').map((name) => name.trim()).filter((name) => name)
  if (!list.length) {
    callback(new Error('对话载体至少选择 1 个工具（否则该智能体无法查询任何数据）'))
    return
  }
  callback()
}

/** 跨字段/跨参数校验（el-form 规则表达不了的部分） */
function checkCrossFields() {
  const timeout = Number(form.value.timeoutS)
  if (Number.isNaN(timeout) || timeout < 5 || timeout > 300) {
    ElMessage.warning('调用超时需要 5~300 秒')
    return false
  }
  const retries = Number(form.value.maxRetries)
  if (Number.isNaN(retries) || retries < 0 || retries > 5) {
    ElMessage.warning('失败重试次数需要 0~5 次')
    return false
  }
  const temperature = form.value.temperature
  if (temperature !== null && temperature !== undefined && (Number(temperature) < 0 || Number(temperature) > 2)) {
    ElMessage.warning('温度需要在 0~2 之间（留空 = 模型默认）')
    return false
  }
  const maxTokens = form.value.maxTokens
  if (maxTokens !== null && maxTokens !== undefined && (Number(maxTokens) < 256 || Number(maxTokens) > 8192)) {
    ElMessage.warning('输出上限需要在 256~8192 之间（留空 = 模型默认）')
    return false
  }
  if (form.value.runtime === 'restock') {
    const params = form.value.paramsJson || {}
    const min = Number(params.factor_min)
    const max = Number(params.factor_max)
    if (!Number.isNaN(min) && !Number.isNaN(max) && min >= max) {
      ElMessage.warning('校准系数下限必须小于上限（否则后端会拒绝保存）')
      return false
    }
  }
  if (form.value.enabled === 0) {
    const reason = (form.value.disabledReason || '').trim()
    if (reason.length < 5 || reason.length > 100) {
      ElMessage.warning('该配置当前处于停用状态，停用原因需保持 5~100 字')
      return false
    }
  }
  return true
}

// ---------------------------------------------------------------------------
// 保存
// ---------------------------------------------------------------------------
function submit() {
  if (!formRef.value) return
  formRef.value.validate((valid) => {
    if (!valid) return
    if (!checkCrossFields()) return
    doSave()
  })
}

function doSave() {
  saving.value = true
  const payload = buildPayload()
  const req = props.mode === 'add' ? addAgentConfig(payload) : updateAgentConfig(props.configId, payload)
  req
    .then((response) => {
      const fallback = props.mode === 'add' ? 1 : (form.value.version || 0) + 1
      const next = savedVersion(response, fallback)
      ElMessage.success('已保存，版本 v' + next + ' 已生效')
      emit('saved', { version: next })
      close()
    })
    .catch((error) => {
      if (error && error.code === 409) {
        // 版本冲突：交给父组件统一 ElMessageBox + 刷新详情；本抽屉关闭，
        // 否则用户会拿着旧 version 反复点保存（每次都会 409）
        emit('conflict')
        close()
        return
      }
      ElMessage.error((error && error.message) || '保存失败，请稍后重试')
    })
    .finally(() => {
      saving.value = false
    })
}

function buildPayload() {
  const isChat = form.value.runtime === 'chat'
  const isFault = form.value.runtime === 'fault'
  const payload = {
    code: form.value.code,
    name: (form.value.name || '').trim(),
    description: (form.value.description || '').trim(),
    runtime: form.value.runtime,
    builtin: form.value.builtin,
    systemPrompt: isFault ? null : form.value.systemPrompt,
    // restock/fault 必须为空（契约 §4.4）；chat 至少 1 个（校验已拦截）
    tools: isChat ? (form.value.tools || '') : '',
    // 空串 = 走环境变量默认（未配置 = 现状）；null 会被 Java 当成“未设置”
    model: form.value.model || '',
    temperature: toNumberOrNull(form.value.temperature),
    maxTokens: toNumberOrNull(form.value.maxTokens),
    timeoutS: form.value.timeoutS,
    maxRetries: form.value.maxRetries,
    paramsJson: isChat ? {} : normalizeParams(form.value.paramsJson),
    enabled: form.value.enabled,
    disabledReason: form.value.enabled === 0 ? (form.value.disabledReason || null) : null,
    changeNote: (form.value.changeNote || '').trim() || null
  }
  if (props.mode === 'add') {
    // 新增：scene 由后端按「当前最大 scene+1 且 ≥101」分配；builtin 强制 0（Q1 不允许造内置）
    payload.builtin = 0
    payload.runtime = 'chat'
  } else {
    payload.id = form.value.id
    payload.code = form.value.code
    payload.scene = form.value.scene
    payload.version = form.value.version
  }
  return payload
}

/** 按 meta.paramSpecs 收口参数：丢弃未知键、按类型转数字，避免把表单杂项写进 params_json */
function normalizeParams(raw) {
  const source = raw || {}
  const specs = paramSpecs.value
  if (!specs.length) return {}
  const out = {}
  specs.forEach((spec) => {
    const value = source[spec.key]
    if (value === null || value === undefined || value === '') {
      out[spec.key] = spec.default
      return
    }
    out[spec.key] = spec.type === 'int' ? parseInt(value, 10) : Number(value)
  })
  return out
}

/**
 * 从成功响应里取新版本号（后端约定 data = 更新后的 VO）。
 *
 * 兜底用本地 version + 1：仅用于提示文案，绝不参与写操作；后端若只回 true，
 * 也好过把提示显示成“版本 vundefined”。
 */
function savedVersion(response, fallback) {
  const data = response && response.data
  if (data && typeof data === 'object' && data.version !== null && data.version !== undefined) {
    return data.version
  }
  if (typeof data === 'number') return data
  return fallback
}

function parseParams(raw) {
  if (!raw) return {}
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw)
      return parsed && typeof parsed === 'object' ? parsed : {}
    } catch (error) {
      return {}
    }
  }
  return typeof raw === 'object' ? raw : {}
}

function toNumberOrNull(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isNaN(number) ? null : number
}

// ---------------------------------------------------------------------------
// 开关
// ---------------------------------------------------------------------------
function onVisibleChange(val) {
  emit('update:modelValue', val)
}

function close() {
  emit('update:modelValue', false)
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) init()
  }
)
</script>

<style lang="scss" scoped>
/* 抽屉 append-to-body → 样式规则写在顶层，不要裹在页面根类下 */
.form-wrap {
  min-height: 240px;
}

.form-alert {
  margin-top: 8px;
}

.section-title {
  margin: 18px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 14px;
  font-weight: 600;
}

.inline-field {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.inline-unit {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.hint {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.hint-warn {
  color: var(--el-color-warning);
}

.footer-tip {
  float: left;
  line-height: 32px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
