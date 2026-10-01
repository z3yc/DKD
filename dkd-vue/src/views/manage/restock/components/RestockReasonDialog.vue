<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="520px"
    append-to-body
    :close-on-click-modal="!loading"
    :before-close="onBeforeClose"
  >
    <div class="reason-dlg">
      <div v-if="description" class="reason-dlg__desc">{{ description }}</div>

      <div class="reason-dlg__label">
        {{ reasonLabel }}<b class="req">*</b>
        <span class="hint">（将写入决策留痕，用于复盘归因）</span>
      </div>

      <div class="reason-dlg__chips">
        <el-button
          v-for="preset in presets"
          :key="preset"
          size="small"
          :type="reason === preset ? 'primary' : 'default'"
          :plain="reason !== preset"
          @click="reason = preset"
        >{{ preset }}</el-button>
      </div>

      <el-input
        v-model="reason"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        placeholder="也可自行填写原因（必填）"
      />
    </div>

    <template #footer>
      <el-button :disabled="loading" @click="close">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">{{ confirmText }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup name="RestockReasonDialog">
import { ElMessage } from 'element-plus'

/**
 * 原因必填弹窗（跳过 / 恢复建议 / 暂停自动分析 / 恢复自动分析 四类操作共用）。
 *
 * 为什么抽成一个组件：这四个操作在后端是**同一套约束**——原因必填、写留痕、
 * 空原因会被拒（skip/restore 服务层校验 + pause/resume 服务层校验）。
 * 各写一份的结果必然是「其中一处忘了校验」，运营就会看到「点了没反应」。
 *
 * 仿真的原型交互：原因快选 chips + 自由文本二选一（选 chip 会直接填入文本框，可再编辑），
 * 而不是「必须选 chip」——运营遇到的现场原因很难穷举，逼着选会让留痕变成假数据。
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '请填写原因' },
  /** 弹窗正文说明（纯文本；需要富文本/换行的场景由调用方拆成多句） */
  description: { type: String, default: '' },
  /** 原因字段标签，如「跳过原因」 */
  reasonLabel: { type: String, default: '原因' },
  /** 原因快选（点击填入文本框，不锁死） */
  presets: { type: Array, default: () => [] },
  confirmText: { type: String, default: '确认' },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue', 'confirm'])

const reason = ref('')

// 每次打开都清空：复用上一次的原因会让留痕里出现「张冠李戴」的解释
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) reason.value = ''
  }
)

function close() {
  if (props.loading) return
  emit('update:modelValue', false)
}

function onBeforeClose(done) {
  // 提交中不允许关闭：否则请求还在飞、弹窗已消失，运营会重复点
  if (props.loading) return
  done()
}

function submit() {
  const text = (reason.value || '').trim()
  if (!text) {
    ElMessage.warning(`请先填写${props.reasonLabel}（必填，用于事后解释与复盘归因）`)
    return
  }
  emit('confirm', text)
}
</script>

<style lang="scss" scoped>
.reason-dlg {
  &__desc {
    color: var(--el-text-color-regular);
    line-height: 1.7;
    margin-bottom: 12px;
  }

  &__label {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 8px;

    .req {
      color: var(--el-color-danger);
      margin-left: 2px;
    }

    .hint {
      font-weight: 400;
      color: var(--el-text-color-secondary);
      font-size: 12px;
    }
  }

  &__chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 10px;
  }
}
</style>
