<template>
  <div class="tool-picker">
    <div class="tool-toolbar">
      <span class="tool-count">
        已选 <b>{{ selected.length }}</b> / {{ tools.length }}
        <span class="dim">（chat 载体至少选择 1 个，否则保存会被拒绝）</span>
      </span>
      <span>
        <el-button size="small" link type="primary" :disabled="!tools.length" @click="selectAll">全选</el-button>
        <el-button size="small" link type="danger" :disabled="!selected.length" @click="clearAll">清空</el-button>
      </span>
    </div>

    <el-checkbox-group v-if="tools.length" :model-value="selected" class="tool-group" @update:model-value="onChange">
      <el-checkbox v-for="name in tools" :key="name" :label="name" class="tool-item">
        <span class="tool-name">{{ name }}</span>
        <span class="tool-label">{{ labelOf(name) }}</span>
      </el-checkbox>
    </el-checkbox-group>

    <el-empty
      v-else
      :image-size="60"
      description="元数据未返回工具清单（meta.tools 为空），暂不可选；保存会因工具为空被拒绝"
    />

    <el-alert
      v-if="selected.length === 0"
      type="warning"
      :closable="false"
      show-icon
      title="未选择任何工具"
      description="对话载体没有工具时只能回答“查不了”，且后端校验要求 chat 载体至少 1 个工具。"
      class="tool-alert"
    />
  </div>
</template>

<script setup name="ToolPicker">
/**
 * 工具多选（契约 §6.3 ToolPicker）。
 *
 * 选项来源只用 meta.tools（后端白名单 = Python TOOL_WHITELIST_BY_RUNTIME[chat] 的并集）。
 * 这里的中文名只是展示用的标签映射：未登记的工具有意**原样显示编码**，
 * 而不是隐藏或显示“未知工具”——新工具上线时运维能立刻看见名字，方便判断漏改文案。
 *
 * 数据格式：DB 里 tools 是逗号分隔字符串（contract §4.3），组件对内外都用字符串，
 * 避免父组件来回转换。
 */
const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  tools: {
    type: Array,
    default: () => []
  },
  disabled: {
    type: Boolean,
    default: false
  }
})
const emit = defineEmits(['update:modelValue'])

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

const selected = computed(() => {
  return (props.modelValue || '')
    .split(',')
    .map((name) => name.trim())
    .filter((name) => name)
})

function labelOf(name) {
  return Object.prototype.hasOwnProperty.call(TOOL_LABELS, name) ? TOOL_LABELS[name] : ''
}

/**
 * 只提交白名单内的值：`el-checkbox-group` 回传的是“当前渲染出来的已勾选项”，
 * 后端下线某工具后旧配置里的残留名字会在这里被剔掉（父组件加载时也会提示一次，
 * 见 AgentConfigForm 的 sanitizeTools）。
 */
function onChange(values) {
  const list = Array.isArray(values) ? values : []
  emit('update:modelValue', list.join(','))
}

function selectAll() {
  emit('update:modelValue', props.tools.join(','))
}

function clearAll() {
  emit('update:modelValue', '')
}
</script>

<style lang="scss" scoped>
.tool-picker {
  width: 100%;
}

.tool-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);

  b {
    color: var(--el-text-color-primary);
  }
}

.tool-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
}

.tool-item {
  min-width: 240px;
  margin-right: 0;
}

.tool-name {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  margin-right: 6px;
}

.tool-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.tool-alert {
  margin-top: 8px;
}

.dim {
  color: var(--el-text-color-secondary);
}
</style>
