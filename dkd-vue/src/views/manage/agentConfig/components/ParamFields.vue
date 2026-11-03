<template>
  <div class="param-fields">
    <div v-if="!specs.length" class="param-empty">
      {{ runtime === 'chat' ? '对话载体没有业务参数（params_json 固定为 {}）' : '元数据未返回参数定义，暂无参数可配' }}
    </div>
    <template v-else>
      <div class="param-head">
        <span class="param-head__tip">
          区间与默认值由后端 meta.paramSpecs 下发（与 Python <span class="mono">agent_defaults.PARAM_SPECS</span> 同源）；
          超出区间保存会被后端拒绝，低于/高于实际所需请改代码默认值而不是硬填。
        </span>
        <el-button size="small" link type="primary" :disabled="!hasDefaults" @click="resetAll">全部恢复默认</el-button>
      </div>
      <el-row :gutter="16">
        <el-col v-for="spec in specs" :key="spec.key" :xs="24" :sm="12">
          <el-form-item :label="labelOf(spec.key)">
            <el-input-number
              :model-value="valueOf(spec.key)"
              :min="spec.min"
              :max="spec.max"
              :step="stepOf(spec)"
              :precision="spec.type === 'int' ? 0 : 2"
              :disabled="disabled"
              controls-position="right"
              style="width: 100%"
              @update:model-value="onChange(spec.key, $event)"
            />
            <div class="param-hint">
              <span>{{ descOf(spec.key) }}</span>
              <span class="param-range">
                默认 {{ spec.default }} · 区间 {{ spec.min }}~{{ spec.max }}
              </span>
            </div>
          </el-form-item>
        </el-col>
      </el-row>
      <!-- 跨字段约束只在这里提示，最终仍以后端校验为准：前端拒了但后端能过，会让用户以为前端坏了；
           反过来（前端放过、后端拒）才是必须避免的，所以这里必须和契约一致 -->
      <el-alert
        v-if="factorConflict"
        type="error"
        :closable="false"
        show-icon
        title="校准系数下限必须小于上限"
        description="factor_min >= factor_max 时后端会拒绝保存（Python 侧读取到这种值会回落默认值，等于配置静默失效）。"
        class="param-alert"
      />
    </template>
  </div>
</template>

<script setup name="ParamFields">
/**
 * 业务参数动态渲染（契约 §6.3 ParamFields）。
 *
 * 为什么按 meta.paramSpecs 渲染而不是写死表单：restock / fault 两载体的参数
 * schema 完全不同，且区间属于“Java 常量镜像 Python 默认值”的口径。
 * 前端写死一份 = 第三份定义，漂移后会出现“页面能填、后端拒”的死角。
 */
const props = defineProps({
  /** params_json 对象（父组件保证是对象，不是字符串） */
  modelValue: {
    type: Object,
    default: () => ({})
  },
  /** meta.paramSpecs：[{ key, type, min, max, default }] */
  specs: {
    type: Array,
    default: () => []
  },
  /** meta.defaultParams：{ key: value }，用于「全部恢复默认」 */
  defaults: {
    type: Object,
    default: () => ({})
  },
  runtime: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  }
})
const emit = defineEmits(['update:modelValue'])

/**
 * 参数的中文名与业务含义（纯展示层）。
 *
 * 未登记的 key 会原样显示编码——不静默显示成“未知参数”：
 * 后端新增参数而前端还没补文案时，运维能直接看见新 key，比看到一个
 * 看不懂的“未知”更容易发现漏改。
 */
const PARAM_LABELS = {
  quantile: { label: '销量分位数', desc: '基线取销量的第几分位，越高越保守' },
  coverage_days: { label: '覆盖天数', desc: '目标库存覆盖多少天的销量' },
  service_factor: { label: '服务水平系数', desc: '安全库存放大系数，越大越不容易缺货' },
  factor_min: { label: '校准系数下限', desc: 'LLM 校准后系数允许的最小值' },
  factor_max: { label: '校准系数上限', desc: 'LLM 校准后系数允许的最大值' },
  batch_size: { label: '单批货道数', desc: 'LLM 校准每批处理的货道数量' },
  window_hours: { label: '分析窗口(小时)', desc: '诊断回看的历史时间窗口' },
  confidence_order_threshold: { label: '订单异常阈值', desc: '订单异常条数达到该值判为高置信' }
}

const hasDefaults = computed(() => props.defaults && Object.keys(props.defaults).length > 0)

const factorConflict = computed(() => {
  if (props.runtime !== 'restock') return false
  const min = Number(props.modelValue.factor_min)
  const max = Number(props.modelValue.factor_max)
  return !Number.isNaN(min) && !Number.isNaN(max) && min >= max
})

function labelOf(key) {
  return Object.prototype.hasOwnProperty.call(PARAM_LABELS, key) ? PARAM_LABELS[key].label : key
}

function descOf(key) {
  return Object.prototype.hasOwnProperty.call(PARAM_LABELS, key) ? PARAM_LABELS[key].desc : ''
}

function stepOf(spec) {
  if (spec.type === 'int') return 1
  // 浮点参数（分位数/系数）用 0.01：0.1 的步长调不出 0.75 这类常用值
  return 0.01
}

function valueOf(key) {
  const value = props.modelValue ? props.modelValue[key] : null
  if (value === null || value === undefined || value === '') return undefined
  const number = Number(value)
  return Number.isNaN(number) ? undefined : number
}

/** 每次修改都 emit 一份新对象：避免直接改 props（Vue 单向数据流），也让父组件 v-model 正常触发 */
function onChange(key, value) {
  const next = Object.assign({}, props.modelValue || {})
  next[key] = value
  emit('update:modelValue', next)
}

function resetAll() {
  const defaults = props.defaults || {}
  const next = {}
  props.specs.forEach((spec) => {
    // 后端没给某 key 的默认值时保留当前值，而不是清空（清空等于把参数改成 undefined，保存会被拒）
    next[spec.key] = Object.prototype.hasOwnProperty.call(defaults, spec.key) ? defaults[spec.key] : valueOf(spec.key)
  })
  emit('update:modelValue', next)
}
</script>

<style lang="scss" scoped>
.param-fields {
  width: 100%;
}

.param-empty {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 4px 0;
}

.param-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;

  &__tip {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    line-height: 1.6;
  }
}

.param-hint {
  display: flex;
  flex-direction: column;
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.param-range {
  color: var(--el-text-color-placeholder);
}

.param-alert {
  margin-top: 4px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}
</style>
