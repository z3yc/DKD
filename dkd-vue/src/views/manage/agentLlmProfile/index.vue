<template>
  <div class="app-container agent-llm-profile">
    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="primary"
          plain
          icon="Plus"
          v-hasPermi="['manage:agentLlmProfile:add']"
          @click="openAdd"
        >新增档案</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button plain icon="Refresh" @click="getList">刷新</el-button>
      </el-col>
    </el-row>

    <!--
      页面自解释 + 安全底线：
      模型档案只存「连接信息」，密钥值是环境变量名而不是值本身（AGENTS §7.1）。
      这里刻意**没有**任何密钥输入框/回显——一旦有人把密钥粘到页面，库就再也不是“无密钥”的了。
    -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="密钥只存环境变量名：页面只展示 api_key_env，永不回显密钥值"
      description="档案里保存的是「去哪个环境变量取密钥」（如 DKD_DEEPSEEK_API_KEY），密钥值只存在于部署环境/.env，不入库也不下发前端。「测试连通」只做一次最小探测并写探测记录，不改动档案本身。"
    />

    <div class="list-wrap">
      <!-- 三态①：加载骨架 -->
      <el-skeleton v-if="loading" :rows="8" animated class="list-skeleton" />

      <!-- 三态②：加载失败（原因 + 重试） -->
      <el-empty v-else-if="loadError" :description="'模型档案加载失败：' + loadError">
        <el-button type="primary" @click="getList">重试</el-button>
      </el-empty>

      <!-- 三态③：空清单（说明是表未初始化，而不是“档案被删光了”） -->
      <el-empty
        v-else-if="profileList.length === 0"
        description="暂无模型档案：可能是 agent_llm_profile 表未初始化（见 docs/ddl/agent_tool_llm_profile.sql 的 seed 段），或该环境尚未创建任何档案"
      >
        <el-button v-hasPermi="['manage:agentLlmProfile:add']" type="primary" @click="openAdd">新增档案</el-button>
      </el-empty>

      <el-table v-else :data="profileList" border size="small" row-key="id">
        <el-table-column label="编码" min-width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
            <el-tag v-if="row.isDefault === 1" size="small" type="success" class="ml4">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="名称 / 说明" min-width="200">
          <template #default="{ row }">
            <div class="name">{{ row.name || '—' }}</div>
            <div v-if="row.description" class="dim">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="供应商" width="130" align="center">
          <template #default="{ row }">{{ providerLabel(row.provider) }}</template>
        </el-table-column>
        <el-table-column label="模型" min-width="150">
          <template #default="{ row }">
            <span class="mono">{{ row.model || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="基址" min-width="210" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono">{{ row.baseUrl || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="密钥变量（仅变量名）" min-width="190">
          <template #default="{ row }">
            <el-tooltip content="只存环境变量名；密钥值在部署环境，页面不回显" placement="top">
              <span class="mono">{{ row.apiKeyEnv || '—' }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="超时 / 重试" width="120" align="center">
          <template #default="{ row }">{{ (row.timeoutS ?? '—') + ' s / ' + (row.maxRetries ?? 0) + ' 次' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="90" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled === 1"
              :loading="togglingId === row.id"
              :disabled="togglingId !== null && togglingId !== row.id"
              v-hasPermi="['manage:agentLlmProfile:edit']"
              @change="(val) => handleToggle(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentLlmProfile:edit']"
              @click="openEdit(row)"
            >编辑</el-button>
            <el-button
              size="small"
              type="primary"
              link
              v-hasPermi="['manage:agentLlmProfile:probe']"
              :loading="probingId === row.id"
              @click="handleProbe(row)"
            >测试连通</el-button>
            <el-button size="small" type="primary" link @click="openProbes(row)">探测记录</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!--
      新增 / 编辑抽屉。
      注意：整个表单里没有、也不会有「密钥」输入项——只填环境变量名。
      append-to-body 会把内容 teleport 到 body，相关样式一律写在 style 顶层（见文件末尾说明）。
    -->
    <el-drawer
      v-model="form.open"
      :title="form.mode === 'add' ? '新增模型档案' : '编辑模型档案'"
      size="680px"
      append-to-body
      :close-on-click-modal="false"
    >
      <div class="form-wrap">
        <el-alert
          class="mb8"
          type="warning"
          :closable="false"
          show-icon
          title="这里只填环境变量名，不要填密钥值"
          description="形如 DKD_DEEPSEEK_API_KEY（大写字母开头，可含数字/下划线）。密钥值放在部署环境/.env，由服务端按变量名读取；页面没有密钥输入框，后端 DTO 也不接收 apiKey 字段。"
        />
        <el-alert
          v-if="form.errorMsg"
          class="mb8"
          type="error"
          :closable="false"
          show-icon
          :title="'保存失败：' + form.errorMsg"
        />

        <el-form ref="formRef" :model="form.data" :rules="rules" label-width="120px">
          <el-form-item label="编码" prop="code">
            <el-input
              v-model="form.data.code"
              :disabled="form.mode === 'edit'"
              maxlength="32"
              placeholder="小写字母开头，3~32 位"
            />
            <div v-if="form.mode === 'edit'" class="hint">编码是档案唯一标识，创建后不可修改</div>
          </el-form-item>
          <el-form-item label="名称" prop="name">
            <el-input v-model="form.data.name" maxlength="50" placeholder="如 DeepSeek（默认）" />
          </el-form-item>
          <el-form-item label="供应商" prop="provider">
            <el-select v-model="form.data.provider" style="width: 100%">
              <el-option v-for="item in PROVIDERS" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="基址" prop="baseUrl">
            <el-input v-model="form.data.baseUrl" maxlength="255" placeholder="https://api.deepseek.com" />
            <div class="hint">OpenAI 兼容基址，必须以 http:// 或 https:// 开头</div>
          </el-form-item>
          <el-form-item label="模型名" prop="model">
            <el-input v-model="form.data.model" maxlength="64" placeholder="如 deepseek-chat" />
          </el-form-item>
          <el-form-item label="密钥变量名" prop="apiKeyEnv">
            <el-input v-model="form.data.apiKeyEnv" maxlength="64" placeholder="如 DKD_DEEPSEEK_API_KEY" />
            <div class="hint">只填变量名；填写密钥值会被后端忽略，请勿粘贴</div>
          </el-form-item>
          <el-form-item label="超时 / 重试">
            <div class="inline-field">
              <el-input-number v-model="form.data.timeoutS" :min="5" :max="300" :step="5" controls-position="right" style="width: 130px" />
              <span class="inline-unit">秒</span>
              <el-input-number v-model="form.data.maxRetries" :min="0" :max="5" controls-position="right" style="width: 110px" />
              <span class="inline-unit">次</span>
            </div>
            <div class="hint">超时 5~300 秒；重试 0~5 次（4xx 重试不会变好）</div>
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="form.data.enabled" :active-value="1" :inactive-value="0" />
            <span class="hint">停用后该档案不会被选用（改动下一次调用生效）</span>
          </el-form-item>
          <el-form-item label="设为默认">
            <el-switch v-model="form.data.isDefault" :active-value="1" :inactive-value="0" />
            <span class="hint">默认档案最多一个：保存为默认时后端会在同一事务里把其它档案的默认标记清掉</span>
          </el-form-item>
          <el-form-item label="说明" prop="description">
            <el-input
              v-model="form.data.description"
              type="textarea"
              :rows="2"
              maxlength="500"
              show-word-limit
              placeholder="用途 / 成本注意（给运维看）"
            />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <span class="dim foot-tip">密钥值不会经过本页面：档案只保存环境变量名</span>
        <el-button @click="form.open = false">取消</el-button>
        <el-button type="primary" :loading="form.saving" @click="submitForm">保存</el-button>
      </template>
    </el-drawer>

    <!--
      连通测试结果：**持久显示**在弹窗里而不是只弹 toast。
      失败原因要能对照 httpStatus/errorType/errorMsg 回看——一闪而过的提示等于逼用户再测一次，
      而每次测试都会写一条探测记录（噪声会把真实历史埋掉）。
    -->
    <el-dialog
      v-model="probeResult.open"
      :title="'连通测试结果：' + (probeResult.profileName || '')"
      width="620px"
      append-to-body
    >
      <div v-loading="probeResult.loading" element-loading-text="正在测试连通（会写入探测记录）…">
        <el-skeleton v-if="probeResult.loading" :rows="4" animated />
        <template v-else>
          <el-alert
            v-if="probeResult.requestFailed"
            class="mb8"
            type="error"
            :closable="false"
            show-icon
            :title="'测试请求失败：' + probeResult.failMsg"
            description="服务端在探测失败时通常也会写入记录；请到「探测记录」核对，先判断是网络/服务不可达还是档案配置问题。"
          />
          <el-descriptions v-if="probeResult.data" :column="2" border size="small">
            <el-descriptions-item label="结论">
              <el-tag size="small" :type="probeResult.data.ok === 1 || probeResult.data.ok === true ? 'success' : 'danger'">
                {{ probeResult.data.ok === 1 || probeResult.data.ok === true ? '连通成功' : '连通失败' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="HTTP 状态">
              {{ probeResult.data.httpStatus === null || probeResult.data.httpStatus === undefined ? '—' : probeResult.data.httpStatus }}
            </el-descriptions-item>
            <el-descriptions-item label="延迟">{{ formatMs(probeResult.data.latencyMs) }}</el-descriptions-item>
            <el-descriptions-item label="实际服务模型">{{ probeResult.data.modelServed || '—' }}</el-descriptions-item>
            <el-descriptions-item label="错误类型">{{ probeResult.data.errorType || '—' }}</el-descriptions-item>
            <el-descriptions-item label="追踪ID">
              <span class="mono">{{ probeResult.data.requestId || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="错误摘要" :span="2">
              <span v-if="probeResult.data.errorMsg" class="err-text">{{ probeResult.data.errorMsg }}</span>
              <span v-else class="dim">—</span>
            </el-descriptions-item>
          </el-descriptions>
          <div class="probe-foot">
            测试不会改动档案本身；结果已写入探测记录（可在「探测记录」按时间回看）。
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="probeResult.open = false">关闭</el-button>
        <el-button type="primary" @click="openProbesFromProbe">查看探测记录</el-button>
      </template>
    </el-dialog>

    <!-- 探测记录：只增历史，按时间倒序（后端 create_time desc） -->
    <el-drawer v-model="probes.open" :title="'探测记录：' + (probes.profileName || '')" size="78%" append-to-body>
      <div v-loading="probes.loading" class="probes-wrap">
        <el-alert
          class="mb8"
          type="info"
          :closable="false"
          show-icon
          title="探测记录只增不改"
          description="每次「测试连通」写一条记录（结论/HTTP 状态/延迟/实际模型/错误类型），不记录响应正文与密钥。失败记录同样保留——排障要看的是趋势，而不是只剩成功样本。"
        />
        <el-skeleton v-if="probes.loading" :rows="6" animated />
        <el-empty v-else-if="probes.failed" :description="'探测记录加载失败：' + probes.failMsg">
          <el-button type="primary" @click="loadProbes">重试</el-button>
        </el-empty>
        <el-empty v-else-if="probes.rows.length === 0" description="该档案还没有探测记录：点「测试连通」会生成第一条" />
        <template v-else>
          <el-table :data="probes.rows" border size="small" row-key="id">
            <el-table-column label="探测时间" width="170">
              <template #default="{ row }">{{ formatTime(row.createTime) }}</template>
            </el-table-column>
            <el-table-column label="结论" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.ok === 1 || row.ok === true ? 'success' : 'danger'">
                  {{ row.ok === 1 || row.ok === true ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="HTTP" width="80" align="center">
              <template #default="{ row }">{{ row.httpStatus === null || row.httpStatus === undefined ? '—' : row.httpStatus }}</template>
            </el-table-column>
            <el-table-column label="延迟" width="100">
              <template #default="{ row }">{{ formatMs(row.latencyMs) }}</template>
            </el-table-column>
            <el-table-column label="实际模型" min-width="130">
              <template #default="{ row }">
                <span class="mono">{{ row.modelServed || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="错误类型" min-width="130">
              <template #default="{ row }">{{ row.errorType || '—' }}</template>
            </el-table-column>
            <el-table-column label="错误摘要" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="row.errorMsg" class="err-text">{{ row.errorMsg }}</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="追踪ID" width="150">
              <template #default="{ row }">
                <span class="mono">{{ row.requestId || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作人" width="100">
              <template #default="{ row }">{{ row.createBy || '—' }}</template>
            </el-table-column>
          </el-table>
          <pagination
            v-show="probes.total > 0"
            :total="probes.total"
            v-model:page="probes.pageNum"
            v-model:limit="probes.pageSize"
            @pagination="loadProbes"
          />
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup name="AgentLlmProfile">
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import {
  addAgentLlmProfile,
  listAgentLlmProfiles,
  listAgentLlmProbes,
  probeAgentLlmProfile,
  setAgentLlmProfileEnabled,
  updateAgentLlmProfile
} from '@/api/manage/agentLlmProfile'

/**
 * 模型档案页（S14，排期任务 3-5）。
 *
 * 数据来源：`agent_llm_profile`（连接信息）+ `agent_llm_probe`（探测记录），
 * DDL 见 docs/ddl/agent_tool_llm_profile.sql；Java 写、Python 只读。
 *
 * 三个取舍：
 *  1. **没有密钥输入框**：档案只保存环境变量名（api_key_env），密钥值在部署环境。
 *     有输入框就一定会有人粘密钥；库一旦落过密钥，轮换与清理成本远高于省下的这一步。
 *  2. **测试连通失败也要写记录**：探测记录只增，失败样本是排障趋势的一部分；
 *     页面把失败原因（httpStatus/errorType/errorMsg）持久展示在弹窗里，而不是只闪一条 toast。
 *  3. **isDefault 互斥由后端保证**：页面只提示“最多一个默认”，不在前端伪造开关逻辑
 *     （前端多份判断只会与后端漂移，表现为“页面说设成了默认，实际没有”）。
 */

const PROVIDERS = [
  { value: 'openai_compatible', label: 'OpenAI 兼容' },
  { value: 'deepseek', label: 'DeepSeek' }
]

const loading = ref(true)
const loadError = ref('')
const profileList = ref([])
/** 正在启停/探测的行 id：只让该行按钮转圈，不锁整表 */
const togglingId = ref(null)
const probingId = ref(null)

const formRef = ref(null)
const form = reactive({
  open: false,
  mode: 'add',
  saving: false,
  errorMsg: '',
  data: emptyForm()
})

/** 连通测试结果弹窗（持久展示，不靠 toast） */
const probeResult = reactive({
  open: false,
  loading: false,
  requestFailed: false,
  failMsg: '',
  profileName: '',
  profileId: null,
  data: null
})

/** 探测记录抽屉（分页 + 三态） */
const probes = reactive({
  open: false,
  loading: false,
  failed: false,
  failMsg: '',
  profileId: null,
  profileName: '',
  rows: [],
  total: 0,
  pageNum: 1,
  pageSize: 10
})

/**
 * 表单校验规则与后端契约同口径（java-log 3-5：name 2~50、baseUrl http(s)、
 * apiKeyEnv ^[A-Z][A-Z0-9_]{2,63}$、timeoutS 5~300、maxRetries 0~5、code 正则）。
 * 为什么前端也要校一遍：request.js 拦截器会丢弃 code=400 的字段级 data 映射，
 * 页面拿不到“哪个字段错了”，只能靠本地规则把错误定位在表单上。
 */
const rules = {
  code: [
    { required: true, message: '编码必填', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_]{2,31}$/, message: '编码需小写字母开头，3~32 位小写字母/数字/下划线', trigger: 'blur' }
  ],
  name: [
    { required: true, message: '名称必填', trigger: 'blur' },
    { min: 2, max: 50, message: '名称 2~50 字', trigger: 'blur' }
  ],
  baseUrl: [
    { required: true, message: '基址必填', trigger: 'blur' },
    { pattern: /^https?:\/\/.+/, message: '基址需以 http:// 或 https:// 开头', trigger: 'blur' }
  ],
  model: [{ required: true, message: '模型名必填', trigger: 'blur' }],
  apiKeyEnv: [
    { required: true, message: '密钥环境变量名必填', trigger: 'blur' },
    { pattern: /^[A-Z][A-Z0-9_]{2,63}$/, message: '只填环境变量名：大写字母开头，可含数字/下划线', trigger: 'blur' }
  ]
}

function emptyForm() {
  return {
    id: null,
    code: '',
    name: '',
    provider: 'openai_compatible',
    baseUrl: '',
    model: '',
    apiKeyEnv: '',
    timeoutS: 60,
    maxRetries: 2,
    enabled: 1,
    isDefault: 0,
    description: ''
  }
}

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------
function providerLabel(value) {
  const hit = PROVIDERS.filter((item) => item.value === value)[0]
  return hit ? hit.label : String(value || '—')
}

function formatTime(value) {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : String(value)
}

/** 毫秒耗时：不足 1 秒按 ms，够 1 秒折成秒（探测延迟多是几十~几百毫秒，换算成 0.3s 反而难读） */
function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'
}

/** 后端返回 AjaxResult.success(list) → res.data 是数组；兼容 rows/直接数组，避免接口形态变动白屏 */
function normalizeRows(response) {
  if (Array.isArray(response)) return response
  if (response && Array.isArray(response.data)) return response.data
  if (response && Array.isArray(response.rows)) return response.rows
  return []
}

// ---------------------------------------------------------------------------
// 列表
// ---------------------------------------------------------------------------
/** 查询档案清单（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  loadError.value = ''
  listAgentLlmProfiles()
    .then((response) => {
      profileList.value = normalizeRows(response)
    })
    .catch((error) => {
      loadError.value = (error && error.message) || '网络异常'
      profileList.value = []
    })
    .finally(() => {
      loading.value = false
    })
}

/** 启停：失败时开关按 row.enabled 自动回到原位（不自造回滚逻辑） */
function handleToggle(row, enabled) {
  togglingId.value = row.id
  setAgentLlmProfileEnabled(row.id, { enabled: enabled ? 1 : 0 })
    .then((response) => {
      const updated = response && response.data
      if (updated && updated.id) {
        Object.assign(row, updated)
      } else {
        row.enabled = enabled ? 1 : 0
      }
      ElMessage.success(enabled ? '已启用「' + row.name + '」' : '已停用「' + row.name + '」')
    })
    .catch((error) => {
      ElMessage.error('启停失败：' + ((error && error.message) || '网络异常'))
    })
    .finally(() => {
      togglingId.value = null
    })
}

// ---------------------------------------------------------------------------
// 新增 / 编辑抽屉
// ---------------------------------------------------------------------------
function openAdd() {
  form.mode = 'add'
  form.data = emptyForm()
  form.errorMsg = ''
  form.open = true
  nextTick(() => formRef.value && formRef.value.clearValidate())
}

function openEdit(row) {
  form.mode = 'edit'
  form.data = {
    id: row.id,
    code: row.code || '',
    name: row.name || '',
    provider: row.provider || 'openai_compatible',
    baseUrl: row.baseUrl || '',
    model: row.model || '',
    apiKeyEnv: row.apiKeyEnv || '',
    timeoutS: row.timeoutS ?? 60,
    maxRetries: row.maxRetries ?? 2,
    enabled: row.enabled ?? 1,
    isDefault: row.isDefault ?? 0,
    description: row.description || ''
  }
  form.errorMsg = ''
  form.open = true
  nextTick(() => formRef.value && formRef.value.clearValidate())
}

/**
 * 保存档案。
 *
 * 成功后才关闭抽屉并重拉列表：保存后 isDefault 可能让**别的行**的默认标记变化，
 * 只改本地这一行会显示两个“默认”（列表必须以后端为准）。
 */
function submitForm() {
  if (!formRef.value) return
  formRef.value.validate().then(() => {
    form.saving = true
    form.errorMsg = ''
    const payload = Object.assign({}, form.data)
    delete payload.id
    const request = form.mode === 'add'
      ? addAgentLlmProfile(payload)
      : updateAgentLlmProfile(form.data.id, payload)
    request
      .then(() => {
        ElMessage.success(form.mode === 'add' ? '已新增模型档案' : '已保存模型档案，下一次调用生效')
        form.open = false
        getList()
      })
      .catch((error) => {
        // 保留在抽屉里持久展示（silent 写接口没有拦截器 toast）
        form.errorMsg = (error && error.message) || '网络异常'
      })
      .finally(() => {
        form.saving = false
      })
  }).catch(() => {})
}

// ---------------------------------------------------------------------------
// 连通测试
// ---------------------------------------------------------------------------
/**
 * 测试连通：结果**持久展示**在弹窗里。
 *
 * 两类失败要分开：
 *  - 后端 200 + data.ok=false（缺环境变量/鉴权失败/超时等）：展示 httpStatus/errorType/errorMsg；
 *  - 请求本身失败（Python 不可达 → 后端 code500）：展示后端 msg，并提示“通常已写记录”。
 * 无论哪类都不关闭弹窗、不清空结果——用户需要边看边改档案。
 */
function handleProbe(row) {
  probeResult.open = true
  probeResult.loading = true
  probeResult.requestFailed = false
  probeResult.failMsg = ''
  probeResult.profileName = row.name || row.code || ''
  probeResult.profileId = row.id
  probeResult.data = null
  probingId.value = row.id
  probeAgentLlmProfile(row.id)
    .then((response) => {
      probeResult.data = response && response.data ? response.data : null
      if (!probeResult.data) probeResult.requestFailed = true
      if (!probeResult.data) probeResult.failMsg = '响应缺少探测结果'
    })
    .catch((error) => {
      probeResult.requestFailed = true
      probeResult.failMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      probeResult.loading = false
      probingId.value = null
    })
}

// ---------------------------------------------------------------------------
// 探测记录
// ---------------------------------------------------------------------------
function openProbes(row) {
  probes.profileId = row.id
  probes.profileName = row.name || row.code || ''
  probes.pageNum = 1
  probes.open = true
  loadProbes()
}

/** 从测试结果弹窗跳到记录（先关结果弹窗，避免两层弹窗叠着） */
function openProbesFromProbe() {
  const profile = profileList.value.filter((item) => item.id === probeResult.profileId)[0]
  probeResult.open = false
  if (profile) {
    openProbes(profile)
  } else {
    ElMessage.warning('档案列表已刷新，请在列表中重新打开「探测记录」')
  }
}

/** 加载探测记录（loading 必须 finally 复位） */
function loadProbes() {
  if (!probes.profileId) return
  probes.loading = true
  probes.failed = false
  probes.failMsg = ''
  listAgentLlmProbes(probes.profileId, { pageNum: probes.pageNum, pageSize: probes.pageSize })
    .then((response) => {
      probes.rows = (response && response.rows) || (response && response.data) || []
      probes.total = (response && response.total) || probes.rows.length
    })
    .catch((error) => {
      probes.rows = []
      probes.total = 0
      probes.failed = true
      probes.failMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      probes.loading = false
    })
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
/*
 * ⚠️ 抽屉/弹窗都带 append-to-body，内容 teleport 到 <body>，祖先 `.agent-llm-profile` 不在其
 * DOM 路径上——弹窗内样式一律写顶层（决策审计页踩过：裹在根类下时选择器整条不匹配）。
 */

.list-wrap {
  min-height: 200px;
}

.list-skeleton {
  padding: 8px 0;
}

.name {
  font-weight: 600;
}

.ml4 {
  margin-left: 4px;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.err-text {
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.form-wrap {
  max-height: 72vh;
  overflow-y: auto;
}

.inline-field {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inline-unit {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.hint {
  margin-top: 2px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.foot-tip {
  float: left;
  line-height: 32px;
  font-size: 12px;
}

.probe-foot {
  margin-top: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.probes-wrap {
  min-height: 200px;
}
</style>
