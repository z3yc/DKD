<template>
  <el-drawer
    :model-value="modelValue"
    :title="'版本历史' + (configName ? '：' + configName : '')"
    size="860px"
    append-to-body
    @update:model-value="(val) => emit('update:modelValue', val)"
  >
    <div v-loading="loading" class="history-wrap">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="版本只增不改"
        description="每次保存（含启停、回滚）都会 +1 版本并写一条快照。回滚 = 把历史快照复制为新版本，不删除任何历史，因此可以再滚回来。快照字段与 agent_config 列名一致（snake_case）。"
      />

      <!--
        5-1：评测报告区块（按版本展示）。放在版本表上方而不是每行展开：
        评测是“跨版本对照”的数据，逐行展开会在同一抽屉里发起 N 次请求（N+1），
        一次拉回按时间倒序展示，版本号本身就能对上历史行。
      -->
      <EvalReportPanel
        v-if="configCode"
        class="history-eval"
        :config-code="configCode"
        :limit="50"
        empty-text="该配置暂无评测报告"
      />

      <el-skeleton v-if="loading" :rows="6" animated class="history-skeleton" />
      <el-empty v-else-if="failed" :description="'版本历史加载失败：' + failMsg">
        <el-button type="primary" @click="load">重试</el-button>
      </el-empty>
      <el-empty v-else-if="!rows.length" description="暂无版本快照（该配置可能还没保存过，或历史表数据缺失）" />

      <el-table v-else :data="rows" border size="small" row-key="id" class="history-table">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="snapshot">
              <template v-if="snapshotOf(row)">
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item label="名称">{{ snapPick(snapshotOf(row), 'name') || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="编码">
                    <span class="mono">{{ snapPick(snapshotOf(row), 'code') || '—' }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="运行载体">{{ snapPick(snapshotOf(row), 'runtime') || '—' }}</el-descriptions-item>
                  <el-descriptions-item label="场景码">{{ snapPick(snapshotOf(row), 'scene') }}</el-descriptions-item>
                  <el-descriptions-item label="启用状态">
                    {{ snapEnabledText(row) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="快照版本">
                    <span class="mono">v{{ row.version }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item v-if="snapEnabledValue(row) === 0" label="停用原因" :span="2">
                    {{ snapPick(snapshotOf(row), 'disabled_reason') || '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="模型">{{ snapPick(snapshotOf(row), 'model') || '默认（环境变量）' }}</el-descriptions-item>
                  <el-descriptions-item label="温度">{{ snapText(snapPick(snapshotOf(row), 'temperature')) }}</el-descriptions-item>
                  <el-descriptions-item label="max_tokens">{{ snapText(snapPick(snapshotOf(row), 'max_tokens')) }}</el-descriptions-item>
                  <el-descriptions-item label="超时 / 重试">
                    {{ snapPick(snapshotOf(row), 'timeout_s') }} 秒 / {{ snapPick(snapshotOf(row), 'max_retries') }} 次
                  </el-descriptions-item>
                  <el-descriptions-item label="工具" :span="2">
                    <span class="mono">{{ snapPick(snapshotOf(row), 'tools') || '—' }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="业务参数" :span="2">
                    <pre class="snap-pre snap-pre--small">{{ snapParamsText(row) }}</pre>
                  </el-descriptions-item>
                </el-descriptions>

                <div class="snap-block">
                  <div class="snap-block__title">系统提示词快照（{{ snapPrompt(row).length }} 字）</div>
                  <pre v-if="snapPrompt(row)" class="snap-pre">{{ snapPrompt(row) }}</pre>
                  <div v-else class="dim">该版本没有提示词（fault 载体不调用 LLM，或历史数据缺失）</div>
                </div>
                <div class="snap-actions">
                  <el-button size="small" :loading="diff.loading" @click="openDiff(row)">与当前版本对比提示词</el-button>
                </div>
              </template>
              <el-alert
                v-else
                type="error"
                :closable="false"
                show-icon
                title="快照解析失败"
                description="历史数据不是合法 JSON，无法展示；该版本不允许回滚（否则会把损坏内容写回当前配置）。请联系后端排查。"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="120">
          <template #default="{ row }">
            <span class="mono">v{{ row.version }}</span>
            <el-tag v-if="row.version === currentVersionValue" size="small" type="success" class="ml4">当前</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="变更说明" min-width="240">
          <template #default="{ row }">{{ row.changeNote || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作人" width="100">
          <template #default="{ row }">{{ row.createBy || '—' }}</template>
        </el-table-column>
        <el-table-column label="快照时间" width="170">
          <template #default="{ row }">{{ row.createTime || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentConfig:rollback']"
              :disabled="row.version === currentVersionValue || !snapshotOf(row)"
              :loading="rollingBack === row.version"
              @click="handleRollback(row)"
            >回滚</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 提示词版本对比：历史快照 vs 当前配置（当前提示词按需拉详情，列表可能不带全量字段） -->
    <el-dialog v-model="diff.open" title="提示词版本对比" width="920px" append-to-body>
      <div v-loading="diff.loading" class="diff-wrap">
        <PromptEditor
          v-if="!diff.loading"
          diff-mode
          :source-text="diff.source"
          :target-text="diff.target"
          :source-label="'历史版本 v' + diff.version"
          :target-label="'当前版本 v' + (currentVersionValue || '?')"
        />
        <div v-if="!diff.loading && !diff.target" class="dim diff-empty-tip">
          当前版本未取到提示词（可能该配置为 fault 载体，或详情接口失败）。
        </div>
      </div>
      <template #footer>
        <el-button @click="diff.open = false">关闭</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup name="VersionHistory">
import { ElMessage, ElMessageBox } from 'element-plus'
import PromptEditor from './PromptEditor.vue'
import EvalReportPanel from './EvalReportPanel.vue'
import { getAgentConfig, listAgentConfigHistory, rollbackAgentConfig } from '@/api/manage/agentConfig'

/**
 * 版本历史 + 快照查看 + 回滚（契约 §6.3 VersionHistory）。
 *
 * 为什么回滚要二次确认且写清“新版本号”：回滚不是 undelete，而是复制旧快照为
 * 新版本；用户如果以为“滚回 v3 就删掉了 v4~v6”，会做出错误决策。提示里明确
 * “不删除任何历史，可再滚回来”，并把当前版本号写出来。
 *
 * 快照解析失败时**禁止回滚**：后端回滚是按 (code, version) 复制快照，
 * 前端拦住一个明显损坏的快照，比让后端把损坏内容写进当前配置再排查更省事。
 */
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  configId: {
    type: [Number, String],
    default: null
  },
  currentVersion: {
    type: Number,
    default: null
  },
  configName: {
    type: String,
    default: ''
  },
  /** 配置编码（5-1 评测报告按 configCode 查；新增/未知时可空，面板自行不请求） */
  configCode: {
    type: String,
    default: ''
  }
})
const emit = defineEmits(['update:modelValue', 'refreshed', 'conflict'])

const loading = ref(false)
const failed = ref(false)
const failMsg = ref('')
const rows = ref([])
const rollingBack = ref(null)
const diff = reactive({ open: false, loading: false, version: null, source: '', target: '' })

const currentVersionValue = computed(() => {
  return props.currentVersion === null || props.currentVersion === undefined
    ? null
    : Number(props.currentVersion)
})

/** 回滚后的新版本号（用于二次确认文案；后端返回 data.version 后以返回值为准） */
const nextVersionText = computed(() => {
  return currentVersionValue.value === null ? '当前版本 +1' : 'v' + (currentVersionValue.value + 1)
})

function load() {
  if (!props.configId) return
  loading.value = true
  failed.value = false
  failMsg.value = ''
  listAgentConfigHistory(props.configId)
    .then((response) => {
      // 契约 §4.1 是 AjaxResult；这里兼容 TableDataInfo（rows）以防后端换成分页返回
      const list = Array.isArray(response.data) ? response.data : (response.rows || [])
      rows.value = list.slice().sort((a, b) => (b.version || 0) - (a.version || 0))
    })
    .catch((error) => {
      failed.value = true
      failMsg.value = (error && error.message) || '请求失败'
    })
    .finally(() => {
      loading.value = false
    })
}

// ---------------------------------------------------------------------------
// 快照解析（snapshot 为 JSON 字符串，键名 snake_case；同时容忍对象与 camelCase）
// ---------------------------------------------------------------------------
function snapshotOf(row) {
  if (!row || !row.snapshot) return null
  if (typeof row.snapshot === 'object') return row.snapshot
  try {
    const parsed = JSON.parse(row.snapshot)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch (error) {
    return null
  }
}

function snapPick(snapshot, snakeKey, camelKey) {
  if (!snapshot) return null
  if (Object.prototype.hasOwnProperty.call(snapshot, snakeKey)) return snapshot[snakeKey]
  if (camelKey && Object.prototype.hasOwnProperty.call(snapshot, camelKey)) return snapshot[camelKey]
  return null
}

function snapText(value) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

/** 快照里的启用状态；null 表示快照缺字段（≠停用），必须区分开，否则会把“没数据”显示成“已停用” */
function snapEnabledValue(row) {
  const value = snapPick(snapshotOf(row), 'enabled', 'enabled')
  if (value === null || value === undefined || value === '') return null
  return Number(value)
}

function snapEnabledText(row) {
  const value = snapEnabledValue(row)
  if (value === null) return '—'
  return value === 1 ? '启用' : '停用'
}

function snapPrompt(row) {
  const snapshot = snapshotOf(row)
  const value = snapPick(snapshot, 'system_prompt', 'systemPrompt')
  return value === null || value === undefined ? '' : String(value)
}

function snapParamsText(row) {
  const snapshot = snapshotOf(row)
  const value = snapPick(snapshot, 'params_json', 'paramsJson')
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch (error) {
      return value
    }
  }
  return JSON.stringify(value, null, 2)
}

// ---------------------------------------------------------------------------
// 提示词对比
// ---------------------------------------------------------------------------
function openDiff(row) {
  diff.open = true
  diff.loading = true
  diff.version = row.version
  diff.source = snapPrompt(row)
  diff.target = ''
  getAgentConfig(props.configId)
    .then((response) => {
      const data = response.data || {}
      diff.target = data.systemPrompt || ''
    })
    .catch((error) => {
      diff.open = false
      ElMessage.error((error && error.message) || '当前版本提示词加载失败，无法对比')
    })
    .finally(() => {
      diff.loading = false
    })
}

// ---------------------------------------------------------------------------
// 回滚
// ---------------------------------------------------------------------------
function handleRollback(row) {
  // 乐观锁的 version 必填（契约 §4.5）：拿不到当前版本就不该发请求，
  // 否则后端只能报参数错误，用户看到的是一个没有信息量的失败
  if (currentVersionValue.value === null) {
    ElMessage.warning('未取到当前版本号，请关闭版本历史后重新打开再回滚')
    return
  }
  ElMessageBox.confirm(
    '将把 v' + row.version + ' 的整份配置（提示词/工具/参数/模型设置/启停状态）复制为新版本 ' +
      nextVersionText.value + '，不删除任何历史，之后还可以再滚回来。确定继续？',
    '确认回滚',
    {
      type: 'warning',
      confirmButtonText: '确认回滚',
      cancelButtonText: '取消'
    }
  )
    .then(() => doRollback(row))
    .catch(() => {})
}

function doRollback(row) {
  rollingBack.value = row.version
  rollbackAgentConfig(props.configId, {
    targetVersion: row.version,
    version: currentVersionValue.value,
    changeNote: '回滚至 v' + row.version
  })
    .then((response) => {
      const next = savedVersion(response, (currentVersionValue.value || 0) + 1)
      ElMessage.success('已回滚至 v' + row.version + '，当前版本 v' + next + ' 已生效')
      emit('refreshed', { version: next, targetVersion: row.version })
      load()
    })
    .catch((error) => {
      if (error && error.code === 409) {
        // 统一交给父组件：ElMessageBox 提示 + 刷新详情；这里只刷新自己的历史列表
        emit('conflict')
        load()
        return
      }
      ElMessage.error((error && error.message) || '回滚失败，请稍后重试')
    })
    .finally(() => {
      rollingBack.value = null
    })
}

/**
 * 从成功响应里取新版本号。
 *
 * 后端约定 data = 更新后的 VO（含 version）；兜底用本地 +1，是为了后端若只回
 * true 时不把提示显示成 “版本 vundefined”。兜底值只用于提示文案，不参与任何写操作。
 */
function savedVersion(response, fallback) {
  const data = response && response.data
  if (data && typeof data === 'object' && data.version !== null && data.version !== undefined) {
    return data.version
  }
  if (typeof data === 'number') return data
  return fallback
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) load()
  }
)

defineExpose({ reload: load })
</script>

<style lang="scss" scoped>
/* 抽屉 append-to-body → 样式规则一律写顶层 */
.history-wrap {
  min-height: 200px;
}

.history-skeleton {
  margin-top: 16px;
}

.history-table {
  margin-top: 12px;
}

.snapshot {
  padding: 8px 12px;
}

.snap-block {
  margin-top: 12px;

  &__title {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 6px;
  }
}

.snap-pre {
  max-height: 260px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;

  &--small {
    max-height: 140px;
    padding: 6px;
  }
}

.snap-actions {
  margin-top: 12px;
}

.diff-wrap {
  min-height: 200px;
}

.diff-empty-tip {
  margin-top: 8px;
  font-size: 12px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.ml4 {
  margin-left: 4px;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
