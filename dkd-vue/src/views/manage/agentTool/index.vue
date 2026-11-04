<template>
  <div class="app-container agent-tool">
    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button plain icon="Refresh" @click="getList">刷新</el-button>
      </el-col>
    </el-row>

    <!--
      页面自解释：说明“能改什么、不能改什么”以及停用后的真实效果。
      工具是模型能做什么的边界：`read_only=0` 的写工具永远不该被模型直接调用
      （AGENTS §7.9 人工确认红线），所以页面刻意不提供 code / readOnly 的编辑入口。
    -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="本页只改「启停 / 超时 / 行数上限」；编码与只读属性由代码和接入层决定"
      description="写工具（read_only=0）永远不会被模型直接调用：即使在这里启用，也只能走「人工确认后回调 Java」的路径。停用工具后，下一次运行组装工具清单时该工具不再注入模型，无需发版。超时/行数上限是工具级声明值，实际读取护栏仍由读取层统一兜底。"
    />

    <div class="list-wrap">
      <!-- 三态①：加载骨架 -->
      <el-skeleton v-if="loading" :rows="8" animated class="list-skeleton" />

      <!-- 三态②：加载失败（原因 + 重试，不静默变成空清单） -->
      <el-empty v-else-if="loadError" :description="'工具清单加载失败：' + loadError">
        <el-button type="primary" @click="getList">重试</el-button>
      </el-empty>

      <!-- 三态③：空清单（说明是表未初始化，而不是“工具全被删了”） -->
      <el-empty
        v-else-if="toolList.length === 0"
        description="工具清单为空：可能是 agent_tool 表未初始化（见 docs/ddl/agent_tool_llm_profile.sql 的 seed 段），或该环境尚未接入工具注册表"
      />

      <el-table v-else :data="toolList" border size="small" row-key="id">
        <el-table-column label="工具编码" min-width="190">
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column label="名称 / 说明" min-width="220">
          <template #default="{ row }">
            <div class="name-line">
              <span class="name">{{ row.name || '—' }}</span>
              <el-tag v-if="row.builtin === 1" size="small" type="info">内置</el-tag>
              <el-tag v-else size="small" type="warning">自定义</el-tag>
            </div>
            <div v-if="row.description" class="dim">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="sourceTag(row.source)">{{ sourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="适用载体" width="100" align="center">
          <template #default="{ row }">{{ scopeLabel(row.scope) }}</template>
        </el-table-column>
        <el-table-column label="只读" width="100" align="center">
          <template #default="{ row }">
            <!-- 写工具用 tooltip 写清“启用≠模型可直调”，避免运营以为开关一开就能自动建单 -->
            <el-tooltip
              :disabled="row.readOnly === 1"
              content="写工具：不会被模型直接调用，只能人工确认后回调 Java"
              placement="top"
            >
              <el-tag size="small" :type="row.readOnly === 1 ? 'success' : 'danger'">
                {{ row.readOnly === 1 ? '只读' : '写工具' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled === 1"
              :loading="togglingId === row.id"
              :disabled="togglingId !== null && togglingId !== row.id"
              v-hasPermi="['manage:agentTool:edit']"
              @change="(val) => handleToggle(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="超时" width="110" align="center">
          <template #default="{ row }">{{ formatMs(row.timeoutMs) }}</template>
        </el-table-column>
        <el-table-column label="行数上限" width="100" align="center">
          <template #default="{ row }">{{ row.rowLimit ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentTool:edit']"
              @click="openEdit(row)"
            >编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!--
      编辑弹窗：只有超时/行数上限可改。
      编码/来源/只读/内置用 el-descriptions 只读展示而不是 disabled 输入框——
      disabled 输入框会让人以为“以后权限够了就能改”，而这些字段在契约上是结构性边界。
    -->
    <el-dialog v-model="edit.open" title="编辑工具参数" width="640px" append-to-body :close-on-click-modal="false">
      <div v-if="edit.row" class="edit-wrap">
        <el-alert
          v-if="edit.errorMsg"
          class="mb8"
          type="error"
          :closable="false"
          show-icon
          :title="'保存失败：' + edit.errorMsg"
        />

        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="工具编码" :span="2">
            <span class="mono">{{ edit.row.code }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="名称">{{ edit.row.name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ sourceLabel(edit.row.source) }}</el-descriptions-item>
          <el-descriptions-item label="适用载体">{{ scopeLabel(edit.row.scope) }}</el-descriptions-item>
          <el-descriptions-item label="只读属性">
            <el-tag size="small" :type="edit.row.readOnly === 1 ? 'success' : 'danger'">
              {{ edit.row.readOnly === 1 ? '只读（可被模型调用）' : '写工具（禁止模型直调）' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="说明" :span="2">{{ edit.row.description || '—' }}</el-descriptions-item>
        </el-descriptions>

        <el-form label-width="110px" class="edit-form">
          <el-form-item label="启用状态">
            <el-switch v-model="edit.enabled" />
            <span class="hint">停用后下一次运行不再注入该工具（无需发版）</span>
          </el-form-item>
          <el-form-item label="超时（毫秒）">
            <el-input-number
              v-model="edit.timeoutMs"
              :min="100"
              :max="30000"
              :step="100"
              controls-position="right"
              style="width: 160px"
            />
            <span class="hint">100~30000 ms；MCP 工具由接入层强制 ≤5000 ms</span>
          </el-form-item>
          <el-form-item label="行数上限">
            <el-input-number
              v-model="edit.rowLimit"
              :min="1"
              :max="5000"
              :step="100"
              controls-position="right"
              style="width: 160px"
            />
            <span class="hint">1~5000 行；MCP 工具由接入层强制 ≤200 行（防止一次查询把上下文打爆）</span>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="edit.open = false">取消</el-button>
        <el-button type="primary" :loading="edit.saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="AgentTool">
import { ElMessage } from 'element-plus'
import { listAgentTools, updateAgentTool } from '@/api/manage/agentTool'

/**
 * 工具中心页（排期任务 3-4）。
 *
 * 数据来源：`agent_tool` 注册表（docs/ddl/agent_tool_llm_profile.sql），
 * 由 Java 维护元数据与开关，Python 侧只读。本页只做三件事：看清单、启停、调超时/行数上限。
 *
 * 三个取舍：
 *  1. **不提供新增/删除**：工具由代码（或 MCP 接入层）发布，`code` 必须能映射到实现，
 *     页面上「新建」一个没有实现的工具只会在运行时报错。启停已覆盖“临时下线”的需求。
 *  2. **code / readOnly 只读展示**：readOnly 是安全边界（写工具禁止模型直调，AGENTS §7.9），
 *     页面能改它等于给了一条绕过人工确认的旁路。
 *  3. **改完不缓存**：保存成功后重新拉列表（而不是只改本地对象），因为后端会做区间夹取，
 *     本地那份可能已经不是库里的值。
 */

const loading = ref(true)
const loadError = ref('')
const toolList = ref([])
/** 正在启停的行 id：只让这一行的开关转圈，不锁整张表 */
const togglingId = ref(null)

/** 编辑弹窗状态；errorMsg 持久展示在弹窗里（silent 写接口不能只靠拦截器 toast） */
const edit = reactive({
  open: false,
  saving: false,
  errorMsg: '',
  row: null,
  enabled: true,
  timeoutMs: 10000,
  rowLimit: 2000
})

const SOURCES = {
  local: { label: '本地', tag: '' },
  mcp: { label: 'MCP', tag: 'warning' }
}
const SCOPES = {
  chat: '对话',
  restock: '补货',
  fault: '诊断',
  all: '全部'
}

function sourceLabel(value) {
  const hit = SOURCES[value]
  return hit ? hit.label : String(value || '—')
}

function sourceTag(value) {
  const hit = SOURCES[value]
  return hit ? hit.tag : 'info'
}

function scopeLabel(value) {
  return SCOPES[value] || String(value || '—')
}

/** 工具级超时（毫秒）：不足 1 秒按 ms 显示，够 1 秒折成秒（与运行时间线页同一口径） */
function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'
}

/**
 * 后端返回 AjaxResult.success(list) → res.data 直接是数组。
 * 兼容 rows/直接数组两种历史形态：这套接口约定曾从 TableDataInfo 改过，兜底能少一次线上白屏。
 */
function normalizeRows(response) {
  if (Array.isArray(response)) return response
  if (response && Array.isArray(response.data)) return response.data
  if (response && Array.isArray(response.rows)) return response.rows
  return []
}

/** 查询工具清单（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  loadError.value = ''
  listAgentTools()
    .then((response) => {
      toolList.value = normalizeRows(response)
    })
    .catch((error) => {
      // 拦截器已提示传输/业务错误；这里保留原因给失败态并清空旧数据（否则会展示过期清单）
      loadError.value = (error && error.message) || '网络异常'
      toolList.value = []
    })
    .finally(() => {
      loading.value = false
    })
}

/**
 * 启停开关。
 *
 * 开关的 :model-value 直接读 row.enabled（而不是本地 v-model）：请求失败时行数据没被改过，
 * 开关自然回到原位，不需要手写回滚逻辑（手写回滚最怕的是“回滚了但请求其实成功了”）。
 */
function handleToggle(row, enabled) {
  togglingId.value = row.id
  updateAgentTool(row.id, {
    enabled: enabled ? 1 : 0,
    timeoutMs: row.timeoutMs,
    rowLimit: row.rowLimit
  })
    .then((response) => {
      // 用后端返回的对象回填：区间夹取后可能与提交值不同，不能假设“提交什么就是什么”
      const updated = response && response.data
      if (updated && updated.id) {
        Object.assign(row, updated)
      } else {
        row.enabled = enabled ? 1 : 0
      }
      ElMessage.success(enabled ? '已启用工具「' + row.name + '」，下一次运行生效' : '已停用工具「' + row.name + '」，下一次运行生效')
    })
    .catch((error) => {
      ElMessage.error('启停失败：' + ((error && error.message) || '网络异常'))
    })
    .finally(() => {
      togglingId.value = null
    })
}

function openEdit(row) {
  edit.row = row
  edit.enabled = row.enabled === 1
  edit.timeoutMs = row.timeoutMs ?? 10000
  edit.rowLimit = row.rowLimit ?? 2000
  edit.errorMsg = ''
  edit.saving = false
  edit.open = true
}

/**
 * 保存工具参数。
 *
 * 校验只做“后端契约的区间预校验”（100~30000 / 1~5000），不重复实现业务规则：
 * 真正裁决仍在后端（它还会校验 code/source/readOnly 是否被绕过），前端提前拦是为了
 * 让错误出现在表单旁边，而不是变成一条一闪而过的 toast。
 */
function submitEdit() {
  if (!edit.row) return
  if (!Number.isFinite(edit.timeoutMs) || edit.timeoutMs < 100 || edit.timeoutMs > 30000) {
    edit.errorMsg = '超时必须在 100~30000 毫秒之间'
    return
  }
  if (!Number.isFinite(edit.rowLimit) || edit.rowLimit < 1 || edit.rowLimit > 5000) {
    edit.errorMsg = '行数上限必须在 1~5000 之间'
    return
  }
  edit.saving = true
  edit.errorMsg = ''
  updateAgentTool(edit.row.id, {
    enabled: edit.enabled ? 1 : 0,
    timeoutMs: edit.timeoutMs,
    rowLimit: edit.rowLimit
  })
    .then(() => {
      ElMessage.success('已保存，工具参数下一次运行生效')
      edit.open = false
      // 重新拉列表：后端可能夹取数值，本地对象不是权威值
      getList()
    })
    .catch((error) => {
      edit.errorMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      edit.saving = false
    })
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
.list-wrap {
  min-height: 200px;
}

.list-skeleton {
  padding: 8px 0;
}

.name-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.name {
  font-weight: 600;
}

.dim {
  margin-top: 2px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.edit-wrap {
  max-height: 60vh;
  overflow-y: auto;
}

.edit-form {
  margin-top: 14px;
}

.hint {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
