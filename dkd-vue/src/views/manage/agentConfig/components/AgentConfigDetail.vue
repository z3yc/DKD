<template>
  <el-drawer
    :model-value="modelValue"
    title="智能体配置详情（只读）"
    size="680px"
    append-to-body
    @update:model-value="(val) => emit('update:modelValue', val)"
  >
    <div v-loading="loading" class="detail-wrap">
      <el-skeleton v-if="loading" :rows="8" animated />
      <el-empty v-else-if="failed" :description="'详情加载失败：' + failMsg">
        <el-button type="primary" @click="reload">重试</el-button>
      </el-empty>
      <el-empty v-else-if="!detail" description="未取到配置详情（可能已被删除，本期不支持删除，请联系后端排查）" />

      <template v-else>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="只读视图"
          description="配置是运行期事实：修改请点「编辑」，每次保存会 +1 版本并写入版本历史。内置智能体不可删除，编码与运行载体不可改。"
        />

        <div class="section-title">基本信息</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="名称">
            {{ detail.name || '—' }}
            <el-tag v-if="detail.builtin === 1" size="small" type="info" class="ml4">内置</el-tag>
            <el-tag v-else size="small" type="warning" class="ml4">自定义</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="编码">
            <span class="mono">{{ detail.code || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="运行载体">{{ runtimeLabel(detail.runtime) }}</el-descriptions-item>
          <el-descriptions-item label="场景码">{{ sceneText(detail) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="detail.enabled === 1 ? 'success' : 'info'">
              {{ detail.enabled === 1 ? '启用' : '停用' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="版本">
            <span class="mono">v{{ detail.version }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.enabled === 0" label="停用原因" :span="2">
            {{ detail.disabledReason || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="用途与边界" :span="2">{{ detail.description || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建">{{ actorText(detail.createBy, detail.createTime) }}</el-descriptions-item>
          <el-descriptions-item label="最近更新">{{ actorText(detail.updateBy, detail.updateTime) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.changeNote" label="变更说明" :span="2">
            {{ detail.changeNote }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="section-title">模型与调用</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="模型">{{ modelText(detail) }}</el-descriptions-item>
          <el-descriptions-item label="温度 temperature">{{ nullableText(detail.temperature) }}</el-descriptions-item>
          <el-descriptions-item label="输出上限 max_tokens">{{ nullableText(detail.maxTokens) }}</el-descriptions-item>
          <el-descriptions-item label="调用超时">{{ detail.timeoutS }} 秒</el-descriptions-item>
          <el-descriptions-item label="失败重试">{{ detail.maxRetries }} 次</el-descriptions-item>
        </el-descriptions>

        <div class="section-title">业务参数</div>
        <div v-if="paramEntries.length" class="param-table">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item v-for="item in paramEntries" :key="item.key" :label="item.label">
              <span class="mono">{{ item.value }}</span>
              <div v-if="item.hint" class="hint">{{ item.hint }}</div>
            </el-descriptions-item>
          </el-descriptions>
        </div>
        <el-empty v-else :image-size="60" :description="detail.runtime === 'chat' ? '对话载体没有业务参数（params_json 为 {}）' : '未配置业务参数（运行时会回落到代码默认值）'" />

        <div class="section-title">
          系统提示词
          <span v-if="promptLength" class="section-sub">共 {{ promptLength }} 字</span>
        </div>
        <el-alert
          v-if="detail.runtime === 'fault'"
          type="info"
          :closable="false"
          show-icon
          title="该载体不调用 LLM"
          description="诊断结论由 Python 侧规则合成，提示词配置不生效（保存时置 NULL，契约 §1）。"
        />
        <template v-else>
          <el-alert
            v-if="unknownPlaceholders.length"
            type="error"
            :closable="false"
            show-icon
            :title="'检测到不支持的占位符：' + unknownPlaceholders.join(' ') + '（运行期会渲染失败，请编辑修正）'"
            class="mb8"
          />
          <pre v-if="promptText" class="prompt-body">{{ promptText }}</pre>
          <el-empty v-else :image-size="60" description="未配置提示词（运行时会回落到代码内置默认模板）" />
          <div v-if="placeholders.length" class="hint">
            本载体允许的占位符：
            <span v-for="name in placeholders" :key="name" class="mono placeholder-tag">{{ '{' + name + '}' }}</span>
          </div>
        </template>

        <div class="section-title">可用工具</div>
        <template v-if="detail.runtime === 'chat'">
          <div v-if="toolsList.length" class="tools">
            <el-tag v-for="name in toolsList" :key="name" size="small" class="tool-tag">
              <span class="mono">{{ name }}</span>
              <span class="tool-label">{{ toolLabel(name) }}</span>
            </el-tag>
          </div>
          <el-empty v-else :image-size="60" description="未配置工具（运行时该智能体查不了数据）" />
        </template>
        <div v-else class="hint">该载体的流程由代码固定，首期不支持工具子集（tools 恒为空）。</div>
      </template>
    </div>

    <template #footer>
      <el-button v-if="detail" :disabled="!detail.id" @click="emit('history', detail)">版本历史</el-button>
      <el-button
        v-if="detail"
        type="primary"
        v-hasPermi="['manage:agentConfig:edit']"
        @click="emit('edit', detail)"
      >编辑</el-button>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-drawer>
</template>

<script setup name="AgentConfigDetail">
import { getAgentConfig, getAgentConfigMeta } from '@/api/manage/agentConfig'

/**
 * S2 · 只读详情抽屉（契约 §6.3）。
 *
 * 三条刻意的设计：
 *  1. **只读**：配置是运行期事实，改必须走编辑（版本 +1 并留 history）。
 *     详情里给编辑入口，但不做行内编辑——否则“看了一眼就能改”会绕过变更说明。
 *  2. **提示词按原样 <pre> 渲染**（文本插值，不用 v-html）：提示词里可能出现
 *     `<`、`{{ }}` 等字符，插值天然转义，没有注入面（AGENTS §7.7）。
 *  3. **参数/占位符/工具的中文说明属展示层，选项与区间仍来自后端 meta**：
 *     前端不定义区间，避免与 Java/Python 第三份定义漂移。
 */
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  configId: {
    type: [Number, String],
    default: null
  }
})
const emit = defineEmits(['update:modelValue', 'edit', 'history'])

const loading = ref(false)
const failed = ref(false)
const failMsg = ref('')
const detail = ref(null)
const meta = ref(null)

const RUNTIMES = [
  { value: 'chat', label: '对话载体' },
  { value: 'restock', label: '补货载体' },
  { value: 'fault', label: '诊断载体' }
]

const PARAM_LABELS = {
  quantile: '销量分位数',
  coverage_days: '覆盖天数',
  service_factor: '服务水平系数',
  factor_min: '校准系数下限',
  factor_max: '校准系数上限',
  batch_size: '单批货道数',
  window_hours: '分析窗口(小时)',
  confidence_order_threshold: '订单异常阈值'
}

const TOOL_LABELS = {
  list_restock_suggestions: '补货建议列表',
  get_machine_inventory: '设备库存查询',
  list_inflight_work_orders: '在途工单',
  get_sales_summary: '销量汇总',
  find_devices: '设备检索',
  list_device_anomalies: '设备异常检测',
  search_device_history: '历史工单检索',
  build_device_diagnosis: '设备诊断规则'
}

const promptText = computed(() => (detail.value && detail.value.systemPrompt) || '')
const promptLength = computed(() => promptText.value.length)
const placeholders = computed(() => (meta.value && meta.value.placeholders) || [])
const toolsList = computed(() => {
  const raw = (detail.value && detail.value.tools) || ''
  return raw.split(',').map((name) => name.trim()).filter((name) => name)
})

const unknownPlaceholders = computed(() => {
  const allowed = placeholders.value
  const found = []
  const pattern = /\{([a-zA-Z0-9_]+)\}/g
  let match
  while ((match = pattern.exec(promptText.value)) !== null) {
    const name = match[1]
    if (allowed.indexOf(name) < 0 && found.indexOf(name) < 0) found.push('{' + name + '}')
  }
  return found
})

const paramEntries = computed(() => {
  const params = parseParams(detail.value && detail.value.paramsJson)
  const specs = (meta.value && meta.value.paramSpecs) || []
  if (specs.length) {
    return specs.map((spec) => ({
      key: spec.key,
      label: Object.prototype.hasOwnProperty.call(PARAM_LABELS, spec.key) ? PARAM_LABELS[spec.key] : spec.key,
      value: params[spec.key] === undefined || params[spec.key] === null ? spec.default : params[spec.key],
      hint: '默认 ' + spec.default + ' · 区间 ' + spec.min + '~' + spec.max
    }))
  }
  // 元数据未到/失败时退回原始 key：宁可显示编码，也不要显示成“没有参数”（后者是错误结论）
  return Object.keys(params).map((key) => ({
    key,
    label: Object.prototype.hasOwnProperty.call(PARAM_LABELS, key) ? PARAM_LABELS[key] : key,
    value: params[key],
    hint: ''
  }))
})

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

function runtimeLabel(value) {
  const hit = RUNTIMES.filter((item) => item.value === value)[0]
  return hit ? hit.label + '（' + value + '）' : value || '—'
}

function sceneText(item) {
  if (!item) return '—'
  // 内置 chat 行承载 1~4 四个场景（scene 填 1），列表/详情都显示成 1-4 才不会被误读成“只有场景1”
  if (item.runtime === 'chat' && item.builtin === 1) return '1-4（内置四场景共用）'
  return item.scene === null || item.scene === undefined ? '—' : String(item.scene)
}

function modelText(item) {
  if (!item) return '—'
  if (item.runtime === 'fault') return '不调用 LLM'
  if (!item.model) return '默认（环境变量 DKD_AGENT_LLM_MODEL）'
  return item.model
}

function nullableText(value) {
  return value === null || value === undefined || value === '' ? '模型默认' : String(value)
}

function actorText(actor, time) {
  if (!actor && !time) return '—'
  return (actor || '—') + ' · ' + (time || '—')
}

function toolLabel(name) {
  return Object.prototype.hasOwnProperty.call(TOOL_LABELS, name) ? TOOL_LABELS[name] : ''
}

function reload() {
  if (!props.configId) return
  loading.value = true
  failed.value = false
  failMsg.value = ''
  meta.value = null
  getAgentConfig(props.configId)
    .then((response) => {
      detail.value = response.data || null
      if (!detail.value) {
        failed.value = true
        failMsg.value = '接口返回为空'
        return
      }
      loadMeta(detail.value.runtime)
    })
    .catch((error) => {
      failed.value = true
      failMsg.value = (error && error.message) || '请求失败'
    })
    .finally(() => {
      loading.value = false
    })
}

/** 元数据失败不影响基本信息展示，只影响参数区间的中文提示（不阻塞用户看配置） */
function loadMeta(runtime) {
  if (!runtime) return
  getAgentConfigMeta(runtime)
    .then((response) => {
      meta.value = response.data || null
    })
    .catch(() => {
      meta.value = null
    })
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reload()
  }
)

defineExpose({ reload })
</script>

<style lang="scss" scoped>
/* 抽屉 append-to-body 会把内容 teleport 到 body，样式规则一律写在顶层（不裹在页面根类下） */
.detail-wrap {
  min-height: 200px;
}

.section-title {
  margin: 18px 0 10px;
  font-size: 14px;
  font-weight: 600;
}

.section-sub {
  margin-left: 6px;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.prompt-body {
  margin: 0;
  padding: 10px;
  max-height: 320px;
  overflow: auto;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.placeholder-tag {
  margin-right: 4px;
  padding: 0 4px;
  background: var(--el-fill-color-light);
  border-radius: 2px;
}

.hint {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-tag {
  .tool-label {
    margin-left: 4px;
    color: var(--el-color-info);
  }
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.ml4 {
  margin-left: 4px;
}

.mb8 {
  margin-bottom: 8px;
}
</style>
