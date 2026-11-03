<template>
  <div class="app-container agent-config">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="76px">
      <el-form-item label="名称" prop="name">
        <el-input
          v-model="queryParams.name"
          placeholder="智能体名称"
          clearable
          style="width: 160px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="编码" prop="code">
        <el-input
          v-model="queryParams.code"
          placeholder="如 patrol_copilot"
          clearable
          style="width: 170px"
          @keyup.enter="handleQuery"
        />
      </el-form-item>
      <el-form-item label="运行载体" prop="runtime">
        <el-select v-model="queryParams.runtime" placeholder="全部载体" clearable style="width: 140px">
          <el-option v-for="item in RUNTIMES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="启用状态" prop="enabled">
        <el-select v-model="queryParams.enabled" placeholder="全部状态" clearable style="width: 120px">
          <el-option label="启用" :value="1" />
          <el-option label="停用" :value="0" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="primary"
          plain
          icon="Plus"
          v-hasPermi="['manage:agentConfig:add']"
          @click="handleAdd"
        >新增智能体</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button plain icon="Refresh" @click="getList">刷新</el-button>
      </el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList" />
    </el-row>

    <!-- 页面自解释：配置是运行期事实，改完的下一次调用才生效；本期不提供删除（契约 §4.4） -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="配置保存后对该智能体的下一次调用生效，进行中的运行不受影响"
      description="内置智能体（补货 / 诊断 / 助手）不可删除，初始值取自现有代码常量——未修改前行为与现状完全一致。本期不提供删除：不再使用请「停用」并填写原因（会留版本历史）。"
    />

    <div class="list-wrap">
      <!-- 三态①：加载骨架（首屏/翻页/搜索期间都显示，避免把“加载中”误判成“没有数据”） -->
      <el-skeleton v-if="loading" :rows="6" animated class="list-skeleton" />

      <!-- 三态②：加载失败（给出原因 + 重试，不静默变成空列表） -->
      <el-empty v-else-if="loadError" :description="'配置列表加载失败：' + loadError">
        <el-button type="primary" @click="getList">重试</el-button>
      </el-empty>

      <!-- 三态③：空数据（提示这是筛选结果为空，而不是“系统里还没有智能体”） -->
      <el-empty v-else-if="configList.length === 0" description="当前筛选条件下没有匹配的智能体，可放宽筛选条件">
        <el-button @click="resetQuery">调整筛选条件</el-button>
      </el-empty>

      <el-table v-else :data="configList" border size="small" row-key="id">
        <el-table-column label="智能体名称" min-width="200">
          <template #default="{ row }">
            <span class="name">{{ row.name }}</span>
            <el-tag v-if="row.builtin === 1" size="small" type="info" class="ml4">内置</el-tag>
            <el-tag v-else size="small" type="warning" class="ml4">自定义</el-tag>
            <div v-if="row.description" class="dim desc">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="编码" width="160">
          <template #default="{ row }">
            <span class="mono">{{ row.code || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="运行载体" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="runtimeTag(row.runtime)">{{ runtimeLabel(row.runtime) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="场景" width="90" align="center">
          <template #default="{ row }">{{ sceneText(row) }}</template>
        </el-table-column>
        <el-table-column label="模型 / 调用" min-width="180">
          <template #default="{ row }">
            <span v-if="row.runtime === 'fault'" class="dim">不调用 LLM</span>
            <span v-else-if="row.model">{{ row.model }}</span>
            <span v-else class="dim">默认（环境变量）</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90" align="center">
          <template #default="{ row }">
            <!-- 停用原因挂在标签 tooltip 上：列表不占列，但点开就能看到“为什么停的” -->
            <el-tooltip
              :disabled="row.enabled === 1 || !row.disabledReason"
              :content="'停用原因：' + (row.disabledReason || '')"
              placement="top"
            >
              <el-tag size="small" :type="row.enabled === 1 ? 'success' : 'info'">
                {{ row.enabled === 1 ? '启用' : '停用' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="80" align="center">
          <template #default="{ row }">
            <span class="mono">v{{ row.version }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最近更新" width="170">
          <template #default="{ row }">
            <el-tooltip :content="updatedFull(row)" placement="top">
              <span>{{ updatedBrief(row) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentConfig:query']"
              @click="openDetail(row)"
            >详情</el-button>
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentConfig:edit']"
              @click="openEdit(row)"
            >编辑</el-button>
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentConfig:query']"
              @click="openHistory(row)"
            >历史</el-button>
            <el-button
              size="small"
              link
              :type="row.enabled === 1 ? 'danger' : 'success'"
              v-hasPermi="['manage:agentConfig:edit']"
              :loading="togglingId === row.id"
              @click="handleToggle(row)"
            >{{ row.enabled === 1 ? '停用' : '启用' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <pagination
      v-show="total > 0"
      :total="total"
      v-model:page="queryParams.pageNum"
      v-model:limit="queryParams.pageSize"
      @pagination="getList"
    />

    <!-- S2 只读详情 -->
    <AgentConfigDetail
      ref="detailRef"
      v-model="detail.open"
      :config-id="detail.configId"
      @edit="handleDetailEdit"
      @history="handleDetailHistory"
    />

    <!-- S3/S4 编辑 / 新增（右侧抽屉） -->
    <AgentConfigForm
      v-model="form.open"
      :mode="form.mode"
      :config-id="form.configId"
      @saved="handleSaved"
      @conflict="handleConflict"
    />

    <!-- 版本历史 + 回滚 -->
    <VersionHistory
      v-model="history.open"
      :config-id="history.configId"
      :current-version="history.currentVersion"
      :config-name="history.configName"
      @refreshed="handleRolledBack"
      @conflict="handleConflict"
    />
  </div>
</template>

<script setup name="AgentConfig">
import { h } from 'vue'
import { ElInput, ElMessage, ElMessageBox } from 'element-plus'
import AgentConfigDetail from './components/AgentConfigDetail.vue'
import AgentConfigForm from './components/AgentConfigForm.vue'
import VersionHistory from './components/VersionHistory.vue'
import { listAgentConfig, setAgentConfigEnabled } from '@/api/manage/agentConfig'

/**
 * S1 智能体配置列表页（排期任务 2-6 / 实现契约 §6.3）。
 *
 * 页面职责：筛选 + 分页 + 三态 + 抽屉编排（详情 / 编辑新增 / 版本历史），
 * 以及 S5 启停（ElMessageBox 二次确认 + 停用原因 5~100 字）。
 *
 * 几个关键取舍：
 *  1. **列表不提供删除**：契约 §4.4 明确本期不提供 delete（避免与 agent_config_history
 *     引用断裂）。所以“下线一个智能体”的动作是停用，且必须写原因。
 *  2. **启停独立于编辑表单**：表单保存时把原 enabled/disabledReason 原样带回，
 *     否则“只改提示词”会顺手把停用的智能体改回启用，而没有任何原因留痕。
 *  3. **409 统一在页面处置**：写接口都带 silent:true（见 api/manage/agentConfig.js），
 *     子组件遇到 409 只 emit conflict；由本页弹一次 ElMessageBox 并刷新列表/详情，
 *     避免拦截器与页面各弹一条。
 *  4. **三态分开渲染**：加载中/失败/空数据必须视觉可区分——把“加载失败”画成空列表，
 *     运维会以为配置被删了。
 */

const RUNTIMES = [
  { value: 'chat', label: '对话载体' },
  { value: 'restock', label: '补货载体' },
  { value: 'fault', label: '诊断载体' }
]

const loading = ref(true)
const loadError = ref('')
const showSearch = ref(true)
const configList = ref([])
const total = ref(0)
const queryRef = ref(null)
const detailRef = ref(null)
/** 正在启停的行 id：只让这一行的按钮转圈，不锁整张表 */
const togglingId = ref(null)

/** 三个抽屉的状态分开管理：详情/编辑/历史互不干扰，也便于各自按需 reload */
const detail = reactive({ open: false, configId: null })
const form = reactive({ open: false, mode: 'edit', configId: null })
const history = reactive({ open: false, configId: null, currentVersion: null, configName: '' })

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    name: null,
    code: null,
    runtime: null,
    enabled: null
  }
})
const { queryParams } = toRefs(data)

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------
function runtimeLabel(value) {
  const hit = RUNTIMES.filter((item) => item.value === value)[0]
  return hit ? hit.label : value || '—'
}

/** el-tag 的 type 白名单：success/info/warning/danger/''（'' = 默认主色，写 primary 会触发 prop 告警） */
function runtimeTag(value) {
  return { chat: '', restock: 'success', fault: 'warning' }[value] ?? 'info'
}

function sceneText(row) {
  // 内置 chat 行承载 1~4 四个场景（scene 存 1），显示 1-4 才不会被误读成“只有场景 1”
  if (row.runtime === 'chat' && row.builtin === 1) return '1-4'
  return row.scene === null || row.scene === undefined ? '—' : String(row.scene)
}

function updatedBrief(row) {
  const time = row.updateTime ? String(row.updateTime) : ''
  const actor = row.updateBy || '—'
  return time.length >= 16 ? actor + ' · ' + time.slice(5, 16) : actor + ' · ' + (time || '—')
}

function updatedFull(row) {
  return (row.updateBy || '—') + ' · ' + (row.updateTime || '—')
}

// ---------------------------------------------------------------------------
// 列表
// ---------------------------------------------------------------------------
function getList() {
  loading.value = true
  loadError.value = ''
  listAgentConfig(queryParams.value)
    .then((response) => {
      configList.value = response.rows || []
      total.value = response.total || 0
      syncHistoryVersion()
    })
    .catch((error) => {
      // 拦截器已提示传输/业务错误；这里保留原因给三态失败区，并清空旧数据
      // （否则会把上一次的列表当成这一次的结果，误导用户）
      loadError.value = (error && error.message) || '网络异常'
      configList.value = []
      total.value = 0
    })
    .finally(() => {
      loading.value = false
    })
}

/** 让历史抽屉的「当前版本」跟上列表：回滚/保存后父组件的 window 里版本号才是最新的 */
function syncHistoryVersion() {
  if (!history.open || history.configId === null) return
  const hit = configList.value.filter((row) => row.id === history.configId)[0]
  if (hit) history.currentVersion = hit.version
}

function handleQuery() {
  queryParams.value.pageNum = 1
  getList()
}

function resetQuery() {
  if (queryRef.value) queryRef.value.resetFields()
  handleQuery()
}

// ---------------------------------------------------------------------------
// 抽屉编排
// ---------------------------------------------------------------------------
function openDetail(row) {
  detail.configId = row.id
  detail.open = true
}

function openEdit(row) {
  form.mode = 'edit'
  form.configId = row.id
  form.open = true
}

function handleAdd() {
  form.mode = 'add'
  form.configId = null
  form.open = true
}

function openHistory(row) {
  history.configId = row.id
  history.currentVersion = row.version
  history.configName = row.name
  history.open = true
}

function handleDetailEdit(item) {
  detail.open = false
  form.mode = 'edit'
  form.configId = item.id
  form.open = true
}

function handleDetailHistory(item) {
  history.configId = item.id
  history.currentVersion = item.version
  history.configName = item.name
  history.open = true
}

/** 保存成功后：刷新列表（新版本号/名称/启用状态都可能变），详情若开着也一起刷新 */
function handleSaved() {
  getList()
  if (detail.open) detailRef.value?.reload()
}

/** 回滚成功后：同上（历史抽屉负责刷新它自己的列表） */
function handleRolledBack() {
  getList()
  if (detail.open) detailRef.value?.reload()
}

// ---------------------------------------------------------------------------
// S5 启停
// ---------------------------------------------------------------------------
function handleToggle(row) {
  if (row.enabled === 1) {
    confirmDisable(row)
  } else {
    confirmEnable(row)
  }
}

function confirmEnable(row) {
  ElMessageBox.confirm(
    '启用后「' + row.name + '」对该智能体的下一次调用立即生效（进行中的运行不受影响）。',
    '启用确认',
    { type: 'info', confirmButtonText: '启用', cancelButtonText: '取消' }
  )
    .then(() => submitEnabled(row, 1, null))
    .catch(() => {})
}

/**
 * 停用确认（S5）：二次确认 + 原因必填 5~100 字。
 *
 * 为什么用 ElMessageBox.confirm + 自绘输入框，而不是 prompt：
 * 停用是破坏性动作，需要警告图标与「确认停用/取消」的明确按钮；Reason 又要带
 * 字数校验。ElMessageBox.confirm 没有输入位，所以把 textarea 放进 message 的 VNode，
 * 并在 beforeClose 里校验——不通过就不 done()，弹窗保持打开供用户改。
 * （输入框内容放普通对象 state，VNode 的 onUpdate 与按钮回调闭包共享同一份。）
 */
function confirmDisable(row) {
  const state = { reason: '' }
  ElMessageBox.confirm(
    h('div', {}, [
      h(
        'p',
        { style: 'margin: 0 0 6px; line-height: 1.6;' },
        '停用后「' + row.name + '」的下一次调用会被拒绝（进行中的运行不受影响）；停用原因会写入版本历史。'
      ),
      h(ElInput, {
        modelValue: state.reason,
        'onUpdate:modelValue': (val) => {
          state.reason = val
        },
        type: 'textarea',
        rows: 3,
        maxlength: 100,
        showWordLimit: true,
        placeholder: '必填：5~100 字（例如：模型切换期间临时停用）',
        style: 'margin-top: 4px;'
      })
    ]),
    '停用确认',
    {
      type: 'warning',
      confirmButtonText: '确认停用',
      cancelButtonText: '取消',
      beforeClose: (action, instance, done) => {
        if (action !== 'confirm') {
          done()
          return
        }
        const reason = (state.reason || '').trim()
        if (reason.length < 5 || reason.length > 100) {
          ElMessage.warning('停用原因需 5~100 字（当前 ' + reason.length + ' 字）')
          return
        }
        done()
      }
    }
  )
    .then(() => submitEnabled(row, 0, state.reason.trim()))
    .catch(() => {})
}

function submitEnabled(row, enabled, reason) {
  togglingId.value = row.id
  setAgentConfigEnabled(row.id, {
    enabled,
    disabledReason: enabled === 1 ? null : reason,
    // 乐观锁：带当前版本，别人刚改过就 409，而不是悄悄覆盖（契约 §4.5）
    version: row.version
  })
    .then((response) => {
      const next = savedVersion(response, row.version + 1)
      ElMessage.success(
        (enabled === 1 ? '已启用' : '已停用') + '，版本 v' + next + ' 已生效'
      )
      getList()
      if (detail.open) detailRef.value?.reload()
    })
    .catch((error) => {
      if (error && error.code === 409) {
        handleConflict()
        return
      }
      // 写接口带 silent:true → 拦截器不弹，这里必须给可读反馈
      ElMessage.error((error && error.message) || '操作失败，请稍后重试')
    })
    .finally(() => {
      // loading 必须复位（AGENTS §2.2），否则按钮会一直转圈
      togglingId.value = null
    })
}

// ---------------------------------------------------------------------------
// 409 乐观锁冲突（所有子组件的写操作统一走这里）
// ---------------------------------------------------------------------------
function handleConflict() {
  ElMessageBox.alert(
    '配置已被他人更新，请刷新后重试。为免覆盖他人的修改，本页会重新加载最新数据；你刚才的改动没有保存。',
    '版本冲突（409）',
    { type: 'warning', confirmButtonText: '刷新最新数据' }
  )
    .then(refreshAfterConflict)
    .catch(refreshAfterConflict)
}

function refreshAfterConflict() {
  getList()
  if (detail.open) detailRef.value?.reload()
}

/**
 * 从写接口成功响应里取新版本号。
 *
 * 后端约定 data = 更新后的 VO（含 version）；兜底用本地 version+1 只为提示文案，
 * 不参与任何写操作——后端若只回 true，也好过提示“版本 vundefined”。
 */
function savedVersion(response, fallback) {
  const data = response && response.data
  if (data && typeof data === 'object' && data.version !== null && data.version !== undefined) {
    return data.version
  }
  if (typeof data === 'number') return data
  return fallback
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
.list-wrap {
  min-height: 240px;
}

.list-skeleton {
  padding: 8px 0;
}

.name {
  font-weight: 500;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.desc {
  margin-top: 2px;
  line-height: 1.5;
}

.ml4 {
  margin-left: 4px;
}
</style>
