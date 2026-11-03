<template>
  <!-- 版本对比视图（契约 §6.3：PromptEditor 负责「版本 diff 视图」） -->
  <div v-if="diffMode" class="prompt-diff">
    <div class="diff-head">
      <span class="diff-label">{{ sourceLabel }}</span>
      <span class="diff-arrow">→</span>
      <span class="diff-label">{{ targetLabel }}</span>
      <span class="diff-stat">
        <span class="diff-stat__add">+{{ diffStats.add }}</span>
        <span class="diff-stat__del">-{{ diffStats.del }}</span>
        <span class="dim">行</span>
      </span>
    </div>
    <el-alert
      v-if="identical"
      type="info"
      :closable="false"
      show-icon
      title="两份提示词完全一致，无需对比"
    />
    <div v-else class="diff-body">
      <div
        v-for="(line, index) in diffRows"
        :key="index"
        class="diff-line"
        :class="'diff-line--' + line.type"
      >
        <span class="diff-line__no">{{ line.oldNo }}</span>
        <span class="diff-line__no">{{ line.newNo }}</span>
        <span class="diff-line__sign">{{ line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ' }}</span>
        <span class="diff-line__text">{{ line.text }}</span>
      </div>
    </div>
  </div>

  <!-- 编辑视图 -->
  <div v-else class="prompt-editor">
    <div class="prompt-toolbar">
      <div class="placeholder-chips">
        <span class="chip-label">可用占位符：</span>
        <el-tooltip
          v-for="name in placeholders"
          :key="name"
          :content="'点击插入 {' + name + '}'"
          placement="top"
        >
          <el-tag size="small" class="placeholder-chip" @click="insertPlaceholder(name)">
            {{ '{' + name + '}' }}
          </el-tag>
        </el-tooltip>
        <span v-if="!placeholders.length" class="dim">
          本载体不允许占位符（写入未定义的 <span class="mono">{xxx}</span> 会被后端拒绝）
        </span>
      </div>
      <el-button size="small" :disabled="!defaultPrompt" @click="resetToDefault">恢复默认</el-button>
    </div>

    <el-input
      ref="inputRef"
      type="textarea"
      :rows="16"
      :model-value="modelValue"
      :placeholder="placeholderText"
      @update:model-value="onInput"
    />

    <div class="prompt-foot">
      <span>
        字数
        <b :class="{ bad: !lengthOk }">{{ length }}</b>
        / 要求 {{ minLength }}~{{ maxLength }}
      </span>
      <span v-if="unknownList.length" class="bad">
        不支持的占位符：{{ unknownText }}（保存会被后端拒绝；白名单见上方）
      </span>
      <span v-else class="dim">占位符在运行时由代码填充，模板里只能出现上方白名单中的名字</span>
    </div>
  </div>
</template>

<script setup name="PromptEditor">
import { ElMessage, ElMessageBox } from 'element-plus'

/**
 * 系统提示词编辑器（契约 §6.3）。
 *
 * 两个模式：
 *  1. 编辑（默认）：字数 100~8000 校验提示 + 占位符白名单插入 + 恢复默认；
 *  2. 对比（diff-mode）：VersionHistory 里看历史快照时，与当前版本逐行比对。
 *
 * 为什么白名单必须来自后端 meta.placeholders：占位符是 Python 模板渲染时的
 * 变量名，写错一个（例如把 `{window_days}` 写进 chat 模板）不会在保存时报错，
 * 而是运行期渲染失败/原样花括号——那比现在直接拒绝更糟（契约 §1 的偏差说明）。
 */
const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  runtime: {
    type: String,
    default: ''
  },
  /** meta.placeholders：本载体允许的占位符名 */
  placeholders: {
    type: Array,
    default: () => []
  },
  /** meta.defaultPrompt：恢复默认用；fault 载体为空（不调用 LLM） */
  defaultPrompt: {
    type: String,
    default: ''
  },
  minLength: {
    type: Number,
    default: 100
  },
  maxLength: {
    type: Number,
    default: 8000
  },
  // ---- diff 模式参数 ----
  diffMode: {
    type: Boolean,
    default: false
  },
  sourceText: {
    type: String,
    default: ''
  },
  targetText: {
    type: String,
    default: ''
  },
  sourceLabel: {
    type: String,
    default: '历史版本'
  },
  targetLabel: {
    type: String,
    default: '当前版本'
  }
})
const emit = defineEmits(['update:modelValue'])

const inputRef = ref(null)

const length = computed(() => (props.modelValue || '').length)
const lengthOk = computed(() => length.value >= props.minLength && length.value <= props.maxLength)

const placeholderText = computed(() => {
  const pieces = ['至少 ' + props.minLength + ' 字：写清角色设定、能力边界、输出格式约束。']
  if (props.placeholders.length) {
    pieces.push('可用占位符：' + props.placeholders.map((n) => '{' + n + '}').join(' '))
  }
  return pieces.join('\n')
})

/** 当前文本里出现、但不在白名单内的占位符（实时提示；最终拦截在表单校验里） */
const unknownList = computed(() => {
  const allowed = props.placeholders || []
  const found = []
  const pattern = /\{([a-zA-Z0-9_]+)\}/g
  let match
  while ((match = pattern.exec(props.modelValue || '')) !== null) {
    const name = match[1]
    if (allowed.indexOf(name) < 0 && found.indexOf(name) < 0) found.push(name)
  }
  return found
})

const unknownText = computed(() => unknownList.value.map((n) => '{' + n + '}').join(' '))

/**
 * 插入占位符到光标处（没有光标位置时追加到末尾）。
 * 为什么按光标插入：提示词里占位符有固定位置（例如 {tool_list} 必须跟在工具说明后），
 * 一律追加到末尾会逼用户手工剪切粘贴。
 */
function insertPlaceholder(name) {
  const token = '{' + name + '}'
  const text = props.modelValue || ''
  const textarea = inputRef.value && inputRef.value.textarea
  if (!textarea) {
    emit('update:modelValue', text + token)
    return
  }
  const start = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : text.length
  const end = typeof textarea.selectionEnd === 'number' ? textarea.selectionEnd : text.length
  const next = text.slice(0, start) + token + text.slice(end)
  emit('update:modelValue', next)
  nextTick(() => {
    textarea.focus()
    const pos = start + token.length
    textarea.setSelectionRange(pos, pos)
  })
}

function onInput(value) {
  emit('update:modelValue', value || '')
}

function resetToDefault() {
  if (!props.defaultPrompt) return
  ElMessageBox.confirm(
    '将用该载体的代码内置默认提示词覆盖当前编辑内容（未保存前不影响线上）。确定恢复默认？',
    '恢复默认提示词',
    { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' }
  )
    .then(() => {
      emit('update:modelValue', props.defaultPrompt)
      ElMessage.success('已填入默认提示词，保存后生效')
    })
    .catch(() => {})
}

// ---------------------------------------------------------------------------
// diff：按行 LCS
// ---------------------------------------------------------------------------
const diffRows = computed(() => buildDiffLines(props.sourceText, props.targetText))

const identical = computed(() => {
  const source = props.sourceText || ''
  const target = props.targetText || ''
  return source === target
})

const diffStats = computed(() => {
  let add = 0
  let del = 0
  diffRows.value.forEach((line) => {
    if (line.type === 'add') add += 1
    else if (line.type === 'del') del += 1
  })
  return { add, del }
})

/**
 * 逐行 diff（最长公共子序列）。
 *
 * 为什么自己算而不引第三方 diff 库：占位符/提示词规模是 8000 字上限、行数通常 <200，
 * O(n*m) 的 DP 完全够用；为一次对比引一个新依赖违反「不新增依赖」约束。
 * 极端情况（8000 字全写成一行）也只有 1 行，不会退化成大矩阵。
 */
function buildDiffLines(oldText, newText) {
  const oldLines = String(oldText || '').split('\n')
  const newLines = String(newText || '').split('\n')
  const n = oldLines.length
  const m = newLines.length
  const dp = []
  for (let i = 0; i <= n; i += 1) {
    dp.push(new Array(m + 1).fill(0))
  }
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        oldLines[i] === newLines[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const rows = []
  let i = 0
  let j = 0
  let oldNo = 1
  let newNo = 1
  while (i < n && j < m) {
    if (oldLines[i] === newLines[j]) {
      rows.push({ type: 'equal', text: oldLines[i], oldNo: oldNo++, newNo: newNo++ })
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: 'del', text: oldLines[i], oldNo: oldNo++, newNo: '' })
      i += 1
    } else {
      rows.push({ type: 'add', text: newLines[j], oldNo: '', newNo: newNo++ })
      j += 1
    }
  }
  while (i < n) {
    rows.push({ type: 'del', text: oldLines[i], oldNo: oldNo++, newNo: '' })
    i += 1
  }
  while (j < m) {
    rows.push({ type: 'add', text: newLines[j], oldNo: '', newNo: newNo++ })
    j += 1
  }
  return rows
}
</script>

<style lang="scss" scoped>
.prompt-editor {
  width: 100%;
}

.prompt-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.placeholder-chips {
  line-height: 24px;
  font-size: 12px;
}

.chip-label {
  color: var(--el-text-color-secondary);
  margin-right: 4px;
}

.placeholder-chip {
  cursor: pointer;
  margin-right: 4px;
  font-family: Consolas, Monaco, monospace;
}

.prompt-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);

  b {
    font-weight: 600;
  }
}

.bad {
  color: var(--el-color-danger);
}

.dim {
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: Consolas, Monaco, monospace;
}

/* ---- diff ---- */
.diff-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}

.diff-label {
  font-weight: 600;
}

.diff-arrow {
  color: var(--el-text-color-secondary);
}

.diff-stat {
  margin-left: auto;

  &__add {
    color: var(--el-color-success);
    margin-right: 8px;
  }

  &__del {
    color: var(--el-color-danger);
  }
}

.diff-body {
  max-height: 56vh;
  overflow: auto;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.6;
}

.diff-line {
  display: flex;
  align-items: flex-start;
  white-space: pre-wrap;
  word-break: break-all;

  &__no {
    flex: 0 0 44px;
    padding: 0 6px;
    text-align: right;
    color: var(--el-text-color-placeholder);
    background: var(--el-fill-color-lighter);
    user-select: none;
  }

  &__sign {
    flex: 0 0 16px;
    text-align: center;
    user-select: none;
  }

  &__text {
    flex: 1;
    padding-right: 8px;
  }

  &--add {
    background: var(--el-color-success-light-9);
  }

  &--del {
    background: var(--el-color-danger-light-9);
  }
}
</style>
