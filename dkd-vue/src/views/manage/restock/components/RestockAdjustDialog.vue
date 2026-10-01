<template>
  <el-dialog
    :model-value="modelValue"
    title="调整补货建议"
    width="560px"
    append-to-body
    :close-on-click-modal="!loading"
    :before-close="onBeforeClose"
  >
    <div v-if="plan" class="adjust-dlg">
      <div class="adjust-dlg__meta">
        {{ plan.innerCode }} · <b>{{ currentItem.skuName || '（未命名商品）' }}</b>
        <span class="dim">货道 {{ currentItem.channelCode }}</span>
      </div>
      <div class="adjust-dlg__sub">
        当前库存 {{ currentItem.currentQuantity }}/{{ currentItem.maxCapacity }}
        · Agent 原建议 +{{ currentItem.suggestedQuantity }}
        · 预计可支撑 {{ currentItem.estimatedDays }} 天
      </div>

      <!-- 多货道计划：必须先选清楚“改哪条”，否则会静默改到第一个货道 -->
      <div v-if="plan.items.length > 1" class="adjust-dlg__field">
        <span class="k">目标货道</span>
        <el-select v-model="channelCode" size="small" style="width: 220px">
          <el-option
            v-for="item in plan.items"
            :key="item.channelCode"
            :label="`${item.channelCode} · ${item.skuName || '未命名'}（建议 +${item.suggestedQuantity}）`"
            :value="item.channelCode"
          />
        </el-select>
      </div>

      <el-radio-group v-model="mode" size="small" class="adjust-dlg__mode">
        <el-radio-button label="quantity">指定数量</el-radio-button>
        <el-radio-button label="recompute">按新参数重算</el-radio-button>
      </el-radio-group>

      <!-- 模式一：人工直接给数量 -->
      <div v-if="mode === 'quantity'" class="adjust-dlg__section">
        <div class="t">补货数量（上限 {{ quantityUpperBound }}，即货道容量 − 现库存）</div>
        <el-input-number v-model="quantity" :min="0" :max="quantityUpperBound" size="small" />
        <div class="quick-set">
          <el-button size="small" text @click="quantity = quantityUpperBound">补满货道</el-button>
          <el-button size="small" text @click="quantity = Math.max(1, Math.ceil(currentItem.suggestedQuantity / 2))">减半</el-button>
          <el-button size="small" text @click="quantity = currentItem.suggestedQuantity">恢复建议值</el-button>
        </div>
      </div>

      <!-- 模式二：修正预测参数后由后端重算（不是让运营口算） -->
      <div v-else class="adjust-dlg__section">
        <div class="t">预测窗口</div>
        <div class="adjust-dlg__chips">
          <el-button
            v-for="day in WINDOW_CHOICES"
            :key="day"
            size="small"
            :type="windowDays === day ? 'primary' : 'default'"
            :plain="windowDays !== day"
            @click="windowDays = day"
          >{{ day }} 天</el-button>
        </div>

        <div class="t" style="margin-top: 10px">服务水平</div>
        <div class="slider-row">
          <el-slider
            v-model="serviceLevel"
            :min="0.9"
            :max="0.99"
            :step="0.01"
            :show-tooltip="false"
            style="width: 260px"
          />
          <span class="slider-val">{{ serviceLevel.toFixed(2) }} → 预计缺货率 {{ serviceLevelRisk }}%</span>
        </div>
        <div class="adjust-dlg__tip">保存后由服务端按新参数重新计算建议量，数据源与 06:00 分析完全同源。</div>
      </div>

      <div class="adjust-dlg__label">
        调整原因<b class="req">*</b>
        <span class="hint">（必填，将反馈给复盘模型用于迭代预测）</span>
      </div>
      <div class="adjust-dlg__chips">
        <el-button
          v-for="preset in REASON_PRESETS"
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
        :rows="2"
        maxlength="500"
        show-word-limit
        placeholder="也可自行填写原因（必填）"
      />
    </div>

    <template #footer>
      <el-button :disabled="loading" @click="close">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">保存调整</el-button>
    </template>
  </el-dialog>
</template>

<script setup name="RestockAdjustDialog">
import { ElMessage } from 'element-plus'

/**
 * 调整补货建议弹窗（原型 V2「调整补货建议」的后端化，排期 1-11）。
 *
 * 两种模式**互斥**，这是后端定的硬约束（`RestockService.adjust`）：
 *   - 指定数量 → 直接按人工给的数量走；
 *   - 修正预测参数 → 由服务端按新窗口/服务水平**重算**。
 * 两者同时给会被拒（「分不清哪个是人工意图」），所以 UI 上用单选强制二选一，
 * 而不是让运营提交后才吃到 400 —— 能提前拦的错误不该留给用户试。
 *
 * ⚠️ 字段名坑：`windowDays`/`serviceLevel` 是后端显式 alias，但 `channel_code` 没有 alias。
 * 所以 payload 里数量模式必须发 snake_case 的 `channel_code`，
 * 发成 `channelCode` 会被 Pydantic 当未知字段丢掉 → 表现成“我选了 A 货道却改了 B 货道”。
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** `summarize_plan()` 的输出（含 items[]）；为 null 时弹窗内容不渲染 */
  plan: { type: Object, default: null },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue', 'confirm'])

/** 与后端 `DKD_AGENT_RESTOCK_WINDOW_WEIGHTS` 的窗口集合（7:0.5,14:0.3,30:0.2）保持一致 */
const WINDOW_CHOICES = [7, 14, 30]
const REASON_PRESETS = ['销量预估偏高', '现场陈列空间受限', '促销计划已调整', '设备即将撤机', '其他']

const mode = ref('quantity')
const channelCode = ref('')
const quantity = ref(0)
const windowDays = ref(7)
const serviceLevel = ref(0.95)
const reason = ref('')

const currentItem = computed(() => {
  const items = (props.plan && props.plan.items) || []
  return items.find((it) => it.channelCode === channelCode.value) || items[0] || {}
})

const quantityUpperBound = computed(() => {
  const item = currentItem.value
  if (!item || typeof item.maxCapacity !== 'number') return 0
  return Math.max(0, item.maxCapacity - item.currentQuantity)
})

const serviceLevelRisk = computed(() => Math.round((1 - serviceLevel.value) * 100))

// 打开时重置为「该计划第一条货道的原建议值」，避免沿用上一次计划的状态
watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    const items = (props.plan && props.plan.items) || []
    const first = items[0] || {}
    mode.value = 'quantity'
    channelCode.value = first.channelCode || ''
    quantity.value = first.suggestedQuantity || 0
    windowDays.value = 7
    serviceLevel.value = 0.95
    reason.value = ''
  }
)

function close() {
  if (props.loading) return
  emit('update:modelValue', false)
}

function onBeforeClose(done) {
  if (props.loading) return
  done()
}

function submit() {
  const text = (reason.value || '').trim()
  if (!text) {
    ElMessage.warning('调整必须填写原因（用于复盘归因，不能留空）')
    return
  }

  if (mode.value === 'quantity') {
    // 客户端先夹一道：后端也会拒（人工输入越界不静默夹取，见 1-6 的设计取舍），
    // 但让运营点完才知道“填多了”是不必要的往返
    if (quantity.value > quantityUpperBound.value) {
      ElMessage.warning(`补货数量不能超过货道空余（${quantityUpperBound.value}），否则补货后库存会溢出`)
      return
    }
    emit('confirm', {
      reason: text,
      quantity: quantity.value,
      // ⚠️ 必须 snake_case（见组件头注释）
      channel_code: channelCode.value || undefined
    })
    return
  }

  emit('confirm', {
    reason: text,
    windowDays: windowDays.value,
    serviceLevel: serviceLevel.value
  })
}
</script>

<style lang="scss" scoped>
.adjust-dlg {
  &__meta {
    font-size: 14px;
    margin-bottom: 2px;
  }

  &__sub {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-bottom: 14px;
  }

  &__mode {
    margin-bottom: 14px;
  }

  &__field {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;

    .k {
      font-size: 13px;
      color: var(--el-text-color-regular);
    }
  }

  &__section {
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 6px;
    padding: 12px;
    margin-bottom: 14px;

    .t {
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 8px;
    }
  }

  &__chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
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

  &__tip {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-top: 8px;
  }

  .quick-set {
    margin-top: 8px;
    display: flex;
    gap: 4px;
  }

  .slider-row {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .slider-val {
    font-size: 12px;
    color: var(--el-color-primary);
    font-weight: 600;
  }

  .dim {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-left: 6px;
  }
}
</style>
