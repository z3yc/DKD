<template>
  <div class="app-container agent-mcp">
    <!--
      安全说明置顶：MCP 是本项目安全面最大的接入点（外部进程/网络、可能绕过 sql_guard）。
      页面把四条边界直接写在最显眼的位置，而不是藏在帮助文档里：
      ① 只接只读工具；② 默认停用、需安全复核后再开；③ 密钥只存环境变量名；
      ④ 先启用再探测——智能体服务只加载 enabled=1 的 server 配置，停用状态下探测/同步会失败。
    -->
    <el-alert
      class="mb8"
      type="warning"
      :closable="false"
      show-icon
      title="安全边界：只接只读工具 · 新 server 默认停用（需安全复核）· 密钥只存环境变量名"
      description="MCP 会引入外部进程/网络，可能绕过本地 sql_guard 或把数据带出。因此：① 只有 read_only=1 的工具允许同步进模型工具清单，写工具与「缺少只读标记」一律拒绝（写操作仍只能人工确认后回调 Java）；② 新建 server 默认 enabled=0，请先完成安全复核（出网白名单 / 超时 / 行数）再启用；③ 页面只保存「HTTP 头名 → 环境变量名」的映射，密钥值在部署环境，不入库、不回显；④ 正确顺序是「新增 → 启用（复核）→ 探测 → 看工具 → 同步」——智能体服务只加载 enabled=1 的配置，停用状态下探测/同步不可用。"
    />

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5">
        <el-button
          type="primary"
          plain
          icon="Plus"
          v-hasPermi="['manage:agentMcp:add']"
          @click="openAdd"
        >新增 server</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button plain icon="Refresh" @click="getList">刷新</el-button>
      </el-col>
    </el-row>

    <div class="list-wrap">
      <!-- 三态①：加载骨架 -->
      <el-skeleton v-if="loading" :rows="8" animated class="list-skeleton" />

      <!-- 三态②：加载失败（原因 + 重试） -->
      <el-empty v-else-if="loadError" :description="'MCP server 列表加载失败：' + loadError">
        <el-button type="primary" @click="getList">重试</el-button>
      </el-empty>

      <!-- 三态③：空清单（说明是还没接 server，而不是“列表坏了”；同时写清正确操作顺序） -->
      <el-empty
        v-else-if="serverList.length === 0"
        description="还没有接入任何 MCP server：正确顺序是「新增 → 启用（先完成安全复核）→ 探测 → 看工具 → 同步」；停用状态下智能体服务不会加载该配置，探测/同步不可用"
      >
        <el-button v-hasPermi="['manage:agentMcp:add']" type="primary" @click="openAdd">新增 server</el-button>
      </el-empty>

      <el-table v-else :data="serverList" border size="small" row-key="id">
        <el-table-column label="编码" min-width="150">
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column label="名称" prop="name" min-width="140" show-overflow-tooltip />
        <el-table-column label="接入地址" min-width="240">
          <template #default="{ row }">
            <el-tooltip :content="row.endpoint || '—'" placement="top">
              <div class="mono endpoint">{{ row.endpoint || '—' }}</div>
            </el-tooltip>
            <div class="dim">
              {{ transportLabel(row.transport) }} · 超时 {{ formatMs(row.timeoutMs) }} · 返回 ≤{{ row.rowLimit ?? '—' }} 行
              · 只读专用 {{ row.readOnlyOnly === 1 ? '是' : '否' }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="探测状态" width="120" align="center">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.lastProbeMsg" :content="row.lastProbeMsg || ''" placement="top">
              <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="最近探测" width="170">
          <template #default="{ row }">
            <span v-if="row.lastProbeTime">{{ formatTime(row.lastProbeTime) }}</span>
            <span v-else class="dim">从未探测</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled === 1"
              :loading="togglingId === row.id"
              :disabled="togglingId !== null && togglingId !== row.id"
              v-hasPermi="['manage:agentMcp:edit']"
              @change="(val) => handleToggle(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" align="center" fixed="right">
          <template #default="{ row }">
            <!--
              探测/同步在**停用状态下不可用**：Python 接入层的 load_server_by_code 只加载 enabled=1
              的配置，停用 server 去探测只会 404→502。这里直接禁用并解释原因，比让用户点一个
              必然失败的按钮更好——安全流程本来就是「先启用（复核）→ 再探测」。
            -->
            <el-tooltip
              :disabled="row.enabled === 1"
              content="请先启用（需安全复核）再探测/同步：智能体服务只加载 enabled=1 的 server 配置"
              placement="top"
            >
              <span class="op-wrap">
                <el-button
                  size="small"
                  type="primary"
                  link
                  v-hasPermi="['manage:agentMcp:probe']"
                  :disabled="row.enabled !== 1"
                  :loading="probingId === row.id"
                  @click="handleProbe(row)"
                >探测</el-button>
              </span>
            </el-tooltip>
            <el-tooltip
              :disabled="row.enabled === 1"
              content="请先启用（需安全复核）再探测/同步：智能体服务只加载 enabled=1 的 server 配置"
              placement="top"
            >
              <span class="op-wrap">
                <el-button
                  size="small"
                  type="primary"
                  link
                  v-hasPermi="['manage:agentMcp:probe']"
                  :disabled="row.enabled !== 1"
                  :loading="syncingId === row.id"
                  @click="handleSync(row)"
                >同步</el-button>
              </span>
            </el-tooltip>
            <el-button size="small" type="primary" link @click="openTools(row)">工具</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!--
      新增 / 编辑抽屉。
      transport 本期只开 http：stdio 需要在本机拉起外部进程，安全面比出网还大，本期明确拒绝
      （后端也会拒绝，这里把选项置灰并写清原因，避免用户填完才被 400）。
      整个抽屉没有、也不会有密钥输入框——只填「头名 → 环境变量名」映射。
    -->
    <el-drawer
      v-model="form.open"
      :title="form.mode === 'add' ? '新增 MCP server' : '编辑 MCP server'"
      size="720px"
      append-to-body
      :close-on-click-modal="false"
    >
      <div class="form-wrap">
        <el-alert
          class="mb8"
          type="info"
          :closable="false"
          show-icon
          title="只填环境变量名，不填密钥值；新 server 默认停用"
          description="envKeys 形如 Authorization → DKD_MCP_X_KEY：左边是发给 MCP server 的 HTTP 头名，右边是「从哪个环境变量取密钥」。密钥值只存在于部署环境，后端不接收 apiKey 字段。新建后默认 enabled=0，完成安全复核（出网白名单/超时/行数）后再启用。"
        />
        <el-alert
          v-if="form.errorMsg"
          class="mb8"
          type="error"
          :closable="false"
          show-icon
          :title="'保存失败：' + form.errorMsg"
        />

        <el-form label-width="120px">
          <el-form-item label="编码" required>
            <el-input
              v-model="form.data.code"
              :disabled="form.mode === 'edit'"
              maxlength="32"
              placeholder="小写字母开头，3~32 位（如 corp_mcp）"
            />
            <div v-if="form.mode === 'edit'" class="hint">编码是唯一标识，创建后不可修改</div>
          </el-form-item>
          <el-form-item label="名称" required>
            <el-input v-model="form.data.name" maxlength="50" show-word-limit placeholder="如 厂商运维 MCP" />
          </el-form-item>
          <el-form-item label="传输方式" required>
            <el-select v-model="form.data.transport" style="width: 100%">
              <el-option label="http（JSON-RPC over HTTP）" value="http" />
              <el-option label="stdio（本期未开放：需在本机拉起外部进程，安全面更大）" value="stdio" disabled />
            </el-select>
            <div class="hint">本期只接受 http；stdio 待安全方案评审后再开放（后端也会明确拒绝）</div>
          </el-form-item>
          <el-form-item label="接入地址" required>
            <el-input v-model="form.data.endpoint" maxlength="500" placeholder="https://mcp.example.com/rpc" />
            <div class="hint">仅 http/https；出网白名单由部署层（防火墙/代理）保证，页面不承诺可达</div>
          </el-form-item>
          <el-form-item label="超时（毫秒）">
            <el-input-number
              v-model="form.data.timeoutMs"
              :min="1000"
              :max="5000"
              :step="500"
              controls-position="right"
              style="width: 160px"
            />
            <span class="hint">1000~5000 ms；单次调用超时（超出即失败，不重试）</span>
          </el-form-item>
          <el-form-item label="返回行数上限">
            <el-input-number v-model="form.data.rowLimit" :min="1" :max="200" :step="10" controls-position="right" style="width: 160px" />
            <span class="hint">1~200 行；防止一次 MCP 调用把上下文打爆</span>
          </el-form-item>

          <el-form-item label="环境变量映射">
            <div class="env-box">
              <div v-for="(item, index) in form.data.envKeys" :key="index" class="env-row">
                <el-input v-model="item.header" maxlength="100" placeholder="HTTP 头名，如 Authorization" />
                <span class="env-arrow">→</span>
                <el-input v-model="item.envName" maxlength="64" placeholder="环境变量名，如 DKD_MCP_X_KEY" />
                <el-button link type="danger" @click="removeEnvRow(index)">删除</el-button>
              </div>
              <el-button link type="primary" @click="addEnvRow">+ 添加一行</el-button>
              <div class="hint">
                左边是发给 server 的头名，右边是取密钥的环境变量名（大写字母开头，可含数字/下划线）。
                没有密钥输入框：密钥值永远不会经过本页面。
              </div>
            </div>
          </el-form-item>

          <el-form-item label="启用">
            <el-switch v-model="form.data.enabled" :active-value="1" :inactive-value="0" />
            <span class="hint hint-warn">
              默认停用。勾选=确认已完成安全复核（出网白名单、超时≤5s、返回≤200 行、只读工具）
            </span>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <span class="dim foot-tip">页面只保存配置与「环境变量名映射」，不保存也不显示任何密钥</span>
        <el-button @click="form.open = false">取消</el-button>
        <el-button type="primary" :loading="form.saving" @click="submitForm">保存</el-button>
      </template>
    </el-drawer>

    <!--
      探测结果：持久展示（失败原因要能回看）。
      探测成功会自动同步只读工具到工具注册表（source=mcp）；rejectedCount 是写工具/缺只读标记
      被拒的数量——必须显示，否则“拒绝”会被误读成“没探测到”。
    -->
    <el-dialog
      v-model="probeResult.open"
      :title="'探测结果：' + (probeResult.serverName || '')"
      width="760px"
      append-to-body
    >
      <div v-loading="probeResult.loading" element-loading-text="正在探测（会真实连接 MCP server）…">
        <el-skeleton v-if="probeResult.loading" :rows="5" animated />
        <template v-else>
          <el-alert
            v-if="probeResult.requestFailed"
            class="mb8"
            type="error"
            :closable="false"
            show-icon
            :title="'探测请求失败：' + probeResult.failMsg"
            description="MCP 服务不可达/被拒时探测会失败；若已配置 python 探测记录，可在后端日志与 agent_mcp_probe 中核对。"
          />
          <template v-if="probeResult.data">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="结论">
                <el-tag size="small" :type="probeResult.data.ok === 1 || probeResult.data.ok === true ? 'success' : 'danger'">
                  {{ probeResult.data.ok === 1 || probeResult.data.ok === true ? '连通成功' : '连通失败' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="延迟">{{ formatMs(probeResult.data.latencyMs) }}</el-descriptions-item>
              <el-descriptions-item label="只读工具数">{{ probeTools.length }}</el-descriptions-item>
              <el-descriptions-item label="被拒工具数">
                <span :class="{ 'rejected-text': (probeResult.data.rejectedCount ?? 0) > 0 }">
                  {{ probeResult.data.rejectedCount ?? 0 }}
                </span>
              </el-descriptions-item>
              <el-descriptions-item label="错误类型">{{ probeResult.data.errorType || '—' }}</el-descriptions-item>
              <el-descriptions-item label="错误摘要">
                <span v-if="probeResult.data.errorMsg" class="err-text">{{ probeResult.data.errorMsg }}</span>
                <span v-else class="dim">—</span>
              </el-descriptions-item>
              <!-- 探测成功会**顺带同步**只读工具：synced/disabled 是这次同步的结果，不显示就等于少了一半结论 -->
              <el-descriptions-item label="顺带同步（新增/更新）">
                {{ probeResult.data.synced === null || probeResult.data.synced === undefined ? '—' : probeResult.data.synced }}
              </el-descriptions-item>
              <el-descriptions-item label="顺带停用">
                {{ probeResult.data.disabled === null || probeResult.data.disabled === undefined ? '—' : probeResult.data.disabled }}
              </el-descriptions-item>
            </el-descriptions>

            <div v-if="probeTools.length > 0" class="probe-tools">
              <div class="section-title">探测到的只读工具（探测成功会自动同步到工具注册表）</div>
              <el-table :data="probeTools" border size="small" max-height="260">
                <el-table-column label="工具名" min-width="160">
                  <template #default="{ row }">
                    <span class="mono">{{ row.name }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="描述" min-width="240" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.description || '—' }}</template>
                </el-table-column>
              </el-table>
            </div>
            <el-alert
              v-if="(probeResult.data.rejectedCount ?? 0) > 0"
              class="mt8"
              type="warning"
              :closable="false"
              show-icon
              title="有工具被拒绝，不会进入模型可调用清单"
              description="被拒原因是「写工具」或「缺少只读标记（readOnlyHint/hint 缺失）」；写操作必须走人工确认 + Java 回调，不允许 MCP 直调。"
            />
          </template>
        </template>
      </div>
      <template #footer>
        <el-button @click="probeResult.open = false">关闭</el-button>
        <el-button type="primary" @click="openToolsFromProbe">查看已同步工具</el-button>
      </template>
    </el-dialog>

    <!-- 同步结果：synced/rejected/disabled 三个数就是同步的全部结论，必须持久展示 -->
    <el-dialog
      v-model="syncResult.open"
      :title="'同步结果：' + (syncResult.serverName || '')"
      width="620px"
      append-to-body
    >
      <div v-loading="syncResult.loading" element-loading-text="正在同步工具清单…">
        <el-skeleton v-if="syncResult.loading" :rows="4" animated />
        <template v-else>
          <el-alert
            v-if="syncResult.requestFailed"
            class="mb8"
            type="error"
            :closable="false"
            show-icon
            :title="'同步失败：' + syncResult.failMsg"
          />
          <el-descriptions v-if="syncResult.data" :column="3" border size="small">
            <el-descriptions-item label="新增/更新">
              {{ syncResult.data.synced ?? 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="被拒绝">
              <span :class="{ 'rejected-text': (syncResult.data.rejected ?? 0) > 0 }">
                {{ syncResult.data.rejected ?? 0 }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="被停用">
              {{ syncResult.data.disabled ?? 0 }}
            </el-descriptions-item>
          </el-descriptions>
          <div class="sync-note">
            被拒绝 = 写工具或缺少只读标记（永远不会进入模型工具清单）；
            被停用 = server 上已消失的工具被标记为停用（不物理删除，保留调用留痕的关联）。
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="syncResult.open = false">关闭</el-button>
        <el-button type="primary" @click="openToolsFromProbe">查看工具清单</el-button>
      </template>
    </el-dialog>

    <!-- 工具抽屉：已同步的只读工具（写工具根本不会出现在这里） -->
    <el-drawer v-model="tools.open" :title="'工具清单：' + (tools.serverName || '')" size="72%" append-to-body>
      <div v-loading="tools.loading" class="tools-wrap">
        <el-alert
          class="mb8"
          type="info"
          :closable="false"
          show-icon
          title="只读工具才会出现在这里"
          description="写工具与缺少只读标记的工具在探测/同步阶段就被拒绝（写操作只能人工确认后回调 Java）。「启用」是工具级开关，停用后下一轮模型不再看到它。"
        />
        <el-skeleton v-if="tools.loading" :rows="6" animated />
        <el-empty v-else-if="tools.failed" :description="'工具清单加载失败：' + tools.failMsg">
          <el-button type="primary" @click="loadTools">重试</el-button>
        </el-empty>
        <el-empty v-else-if="tools.rows.length === 0" description="还没有已同步的工具：先点「探测」（成功会自动同步），或点「同步」手动拉取" />
        <el-table v-else :data="tools.rows" border size="small" row-key="code">
          <el-table-column label="工具编码" min-width="230">
            <template #default="{ row }">
              <span class="mono">{{ row.code || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="名称" min-width="150">
            <template #default="{ row }">{{ row.name || '—' }}</template>
          </el-table-column>
          <el-table-column label="只读" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.readOnly === 1 ? 'success' : 'danger'">
                {{ row.readOnly === 1 ? '只读' : '写工具' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.enabled === 1 ? 'success' : 'info'">
                {{ row.enabled === 1 ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="描述" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">{{ row.description || '—' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup name="AgentMcp">
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import {
  addAgentMcpServer,
  listAgentMcpServers,
  listAgentMcpTools,
  probeAgentMcpServer,
  setAgentMcpServerEnabled,
  syncAgentMcpServer,
  updateAgentMcpServer
} from '@/api/manage/agentMcp'

/**
 * MCP 接入页（S12，排期任务 3-7）。
 *
 * 数据来源：`agent_mcp_server / agent_mcp_tool / agent_mcp_probe`（docs/ddl/agent_kb_mcp.sql）。
 * 配置写由 Java（RuoYi 权限 + 审计）落库；探测/同步/调用由 Python 接入层执行（服务间密钥）。
 *
 * 三个刻意的安全取舍：
 *  1. **默认停用**：新建 server enabled=0。MCP 会引入外部网络/进程，能绕过本地 sql_guard；
 *     “建好就能用”会让一次误配直达生产数据，因此必须人工复核后再开。
 *  2. **只接只读工具**：写工具/缺只读标记的工具在探测阶段就被拒（页面显示 rejectedCount）；
 *     写操作只能走 §7.9 的人工确认 + 回调 Java，绝不允许 MCP 直调。
 *  3. **无密钥输入框**：envKeys 只存「HTTP 头名 → 环境变量名」，密钥值在部署环境；
 *     页面没有任何 apiKey 字段可填，也就没有“把密钥粘进库”的路径。
 */

const loading = ref(true)
const loadError = ref('')
const serverList = ref([])
/** 正在启停/探测/同步的行 id：只让该行按钮转圈，不锁整表 */
const togglingId = ref(null)
const probingId = ref(null)
const syncingId = ref(null)

/** 新增/编辑抽屉；errorMsg 持久展示在抽屉里（silent 写接口） */
const form = reactive({
  open: false,
  mode: 'add',
  saving: false,
  errorMsg: '',
  data: emptyForm()
})

/** 探测结果弹窗 */
const probeResult = reactive({
  open: false,
  loading: false,
  requestFailed: false,
  failMsg: '',
  serverId: null,
  serverName: '',
  data: null
})

/** 同步结果弹窗 */
const syncResult = reactive({
  open: false,
  loading: false,
  requestFailed: false,
  failMsg: '',
  serverId: null,
  serverName: '',
  data: null
})

/** 工具抽屉（三态） */
const tools = reactive({
  open: false,
  loading: false,
  failed: false,
  failMsg: '',
  serverId: null,
  serverName: '',
  rows: []
})

const TRANSPORTS = {
  http: { label: 'HTTP', tag: '' },
  stdio: { label: 'stdio（未开放）', tag: 'info' }
}
/** 与 agent_mcp_server.status 列注释一致：unknown/ok/error/disabled */
const STATUSES = {
  unknown: { label: '未探测', tag: 'info' },
  ok: { label: '正常', tag: 'success' },
  error: { label: '异常', tag: 'danger' },
  disabled: { label: '已停用', tag: 'info' }
}

const probeTools = computed(() => {
  const list = probeResult.data && probeResult.data.tools
  return Array.isArray(list) ? list : []
})

function emptyForm() {
  return {
    id: null,
    code: '',
    name: '',
    transport: 'http',
    endpoint: '',
    timeoutMs: 5000,
    rowLimit: 200,
    enabled: 0,
    envKeys: []
  }
}

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------
function transportLabel(value) {
  const hit = TRANSPORTS[value]
  return hit ? hit.label : String(value || '—')
}

function statusLabel(value) {
  const hit = STATUSES[value]
  return hit ? hit.label : String(value || '—')
}

function statusTag(value) {
  const hit = STATUSES[value]
  return hit ? hit.tag : 'info'
}

function formatTime(value) {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : String(value)
}

function formatMs(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (Number.isNaN(ms)) return String(value)
  return ms >= 1000 ? (ms / 1000).toFixed(ms % 1000 === 0 ? 0 : 1) + ' s' : ms + ' ms'
}

/** 后端返回 AjaxResult.success(...) → 兼容「裸数组」「data 数组」「data.{total,rows}」「顶层 {total,rows}」四种形态 */
function normalizeRows(response) {
  if (Array.isArray(response)) return response
  if (response && Array.isArray(response.data)) return response.data
  // GET /{id}/tools 的真实形状是 data={total,rows[]}（Python 包了一层）；只认裸数组会显示空
  if (response && response.data && Array.isArray(response.data.rows)) return response.data.rows
  if (response && Array.isArray(response.rows)) return response.rows
  return []
}

/**
 * envKeys 归一化为表单行：[{ header, envName }]。
 * 后端/DB 里它是 JSON 对象（{header: ENV_NAME}）或 JSON 串；两种都接，
 * 任何一行不合法都不该让整个抽屉打不开（返回空数组由用户重填）。
 */
function parseEnvKeys(value) {
  let obj = value
  if (typeof value === 'string') {
    try {
      obj = JSON.parse(value)
    } catch (error) {
      return []
    }
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return []
  return Object.keys(obj).map((header) => ({ header, envName: String(obj[header] || '') }))
}

/** 表单行 → {header: ENV_NAME}，空行不提交 */
function buildEnvKeys() {
  const map = {}
  form.data.envKeys.forEach((item) => {
    const header = (item.header || '').trim()
    const envName = (item.envName || '').trim()
    if (header && envName) map[header] = envName
  })
  return map
}

// ---------------------------------------------------------------------------
// 列表
// ---------------------------------------------------------------------------
/** 查询 server 列表（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  loadError.value = ''
  listAgentMcpServers()
    .then((response) => {
      serverList.value = normalizeRows(response)
    })
    .catch((error) => {
      loadError.value = (error && error.message) || '网络异常'
      serverList.value = []
    })
    .finally(() => {
      loading.value = false
    })
}

// ---------------------------------------------------------------------------
// 新增 / 编辑
// ---------------------------------------------------------------------------
function openAdd() {
  form.mode = 'add'
  form.data = emptyForm()
  form.errorMsg = ''
  form.open = true
}

/**
 * 编辑直接使用**列表行**数据：列表已返回全部可编辑字段（含 envKeys），
 * 再请求 GET /{id} 只会多一次网络与一个权限面（菜单里没有独立的 query 按钮权限）。
 */
function openEdit(row) {
  form.mode = 'edit'
  form.data = {
    id: row.id,
    code: row.code || '',
    name: row.name || '',
    // 本期只支持 http；历史数据若是别的值，展示原值但不允许改成其他（后端校验会拒绝）
    transport: row.transport || 'http',
    endpoint: row.endpoint || '',
    timeoutMs: row.timeoutMs ?? 5000,
    rowLimit: row.rowLimit ?? 200,
    enabled: row.enabled ?? 0,
    envKeys: parseEnvKeys(row.envKeys)
  }
  form.errorMsg = ''
  form.open = true
}

function addEnvRow() {
  form.data.envKeys.push({ header: '', envName: '' })
}

function removeEnvRow(index) {
  form.data.envKeys.splice(index, 1)
}

/** 校验：只拦契约里明确的显式约束（正则/区间/成对），业务裁决仍在后端 */
function validateForm() {
  const data = form.data
  if (!/^[a-z][a-z0-9_]{2,31}$/.test((data.code || '').trim())) {
    form.errorMsg = '编码不合法：小写字母开头，3~32 位小写字母/数字/下划线'
    return false
  }
  if ((data.name || '').trim().length < 2 || (data.name || '').trim().length > 50) {
    form.errorMsg = '名称需 2~50 字'
    return false
  }
  if (data.transport !== 'http') {
    form.errorMsg = '本期只接受 http 传输方式（stdio 未开放）'
    return false
  }
  if (!/^https?:\/\/.+/.test((data.endpoint || '').trim())) {
    form.errorMsg = '接入地址必须以 http:// 或 https:// 开头'
    return false
  }
  if (!Number.isFinite(data.timeoutMs) || data.timeoutMs < 1000 || data.timeoutMs > 5000) {
    form.errorMsg = '超时需在 1000~5000 毫秒之间'
    return false
  }
  if (!Number.isFinite(data.rowLimit) || data.rowLimit < 1 || data.rowLimit > 200) {
    form.errorMsg = '返回行数上限需在 1~200 之间'
    return false
  }
  // 环境变量映射：头名必须是合法 HTTP token；右侧必须是环境变量名（不是密钥值）
  const headerRe = /^[A-Za-z0-9!#$%&'*+.^_`|~-]+$/
  const envRe = /^[A-Z][A-Z0-9_]{2,63}$/
  const seen = {}
  for (let i = 0; i < data.envKeys.length; i += 1) {
    const header = (data.envKeys[i].header || '').trim()
    const envName = (data.envKeys[i].envName || '').trim()
    if (!header && !envName) continue
    if (!header || !envName) {
      form.errorMsg = '环境变量映射第 ' + (i + 1) + ' 行不完整：头名与环境变量名必须成对填写'
      return false
    }
    if (!headerRe.test(header)) {
      form.errorMsg = '环境变量映射第 ' + (i + 1) + ' 行的头名不合法（只允许 HTTP token 字符）'
      return false
    }
    if (!envRe.test(envName)) {
      form.errorMsg = '环境变量映射第 ' + (i + 1) + ' 行的环境变量名不合法（大写字母开头，可含数字/下划线；不要填密钥值）'
      return false
    }
    if (seen[header]) {
      form.errorMsg = '环境变量映射里 HTTP 头名重复：' + header
      return false
    }
    seen[header] = true
  }
  return true
}

function submitForm() {
  if (!validateForm()) return
  form.saving = true
  form.errorMsg = ''
  const payload = {
    code: form.data.code.trim(),
    name: form.data.name.trim(),
    transport: 'http',
    endpoint: form.data.endpoint.trim(),
    timeoutMs: form.data.timeoutMs,
    rowLimit: form.data.rowLimit,
    enabled: form.data.enabled ? 1 : 0,
    envKeys: buildEnvKeys()
  }
  const request = form.mode === 'add'
    ? addAgentMcpServer(payload)
    : updateAgentMcpServer(form.data.id, payload)
  request
    .then(() => {
      if (payload.enabled === 1 && form.mode === 'add') {
        ElMessage.warning('已新增并启用：请确认已完成安全复核（出网白名单/超时/行数/只读工具）')
      } else if (form.mode === 'add') {
        // 停用状态下智能体服务不加载配置，探测/同步会失败；这里把下一步说清楚，避免用户马上点探测撞 502
        ElMessage.success('已新增 MCP server（默认停用）：请先完成安全复核并启用，然后再「探测」')
      } else {
        ElMessage.success('已保存 MCP server 配置')
      }
      form.open = false
      getList()
    })
    .catch((error) => {
      // silent 写接口：持久展示在抽屉里，用户可改完直接重试
      form.errorMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      form.saving = false
    })
}

// ---------------------------------------------------------------------------
// 启停（启用前二次确认：MCP 是安全面最大的开关，不能一点就开）
// ---------------------------------------------------------------------------
function handleToggle(row, enabled) {
  if (!enabled) {
    ElMessageBox.confirm(
      '停用后该 MCP server 的工具不再进入模型可调用清单（已同步工具会标记停用，不删除）。确认停用「' + row.name + '」？',
      '停用确认',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' }
    )
      .then(() => submitToggle(row, 0))
      .catch(() => {})
    return
  }
  ElMessageBox.confirm(
    '启用 MCP server 意味着允许智能体调用外部服务提供的只读工具。请先确认已完成安全复核：' +
      '出网白名单、超时≤5s、返回≤200 行、只接只读工具。确认启用「' + row.name + '」？',
    '启用确认（需安全复核）',
    { type: 'warning', confirmButtonText: '确认启用', cancelButtonText: '取消' }
  )
    .then(() => submitToggle(row, 1))
    .catch(() => {})
}

function submitToggle(row, next) {
  togglingId.value = row.id
  setAgentMcpServerEnabled(row.id, { enabled: next })
    .then((response) => {
      const updated = response && response.data
      if (updated && updated.id) Object.assign(row, updated)
      else row.enabled = next
      ElMessage.success(next === 1 ? '已启用，下一次运行生效' : '已停用')
    })
    .catch((error) => {
      ElMessage.error('启停失败：' + ((error && error.message) || '网络异常'))
    })
    .finally(() => {
      togglingId.value = null
    })
}

// ---------------------------------------------------------------------------
// 探测
// ---------------------------------------------------------------------------
/**
 * 探测：真实连接一次 MCP server，成功会**自动同步只读工具**。
 *
 * 失败分两类展示：200 + ok=false（连上了但协议/鉴权失败）看 errorType/errorMsg；
 * 请求本身失败（Python 不可达 → 503）看 requestFailed 的 msg。两类都持久留在弹窗里。
 */
function handleProbe(row) {
  probeResult.open = true
  probeResult.loading = true
  probeResult.requestFailed = false
  probeResult.failMsg = ''
  probeResult.serverId = row.id
  probeResult.serverName = row.name || row.code || ''
  probeResult.data = null
  probingId.value = row.id
  probeAgentMcpServer(row.id)
    .then((response) => {
      probeResult.data = response && response.data ? response.data : null
      if (!probeResult.data) {
        probeResult.requestFailed = true
        probeResult.failMsg = '响应缺少探测结果'
      }
      getList()
    })
    .catch((error) => {
      probeResult.requestFailed = true
      probeResult.failMsg = (error && error.message) || '网络异常'
      // 探测失败也可能已写入探测记录（lastProbeTime/status 变了），刷新列表保证展示的是库里事实
      getList()
    })
    .finally(() => {
      probeResult.loading = false
      probingId.value = null
    })
}

// ---------------------------------------------------------------------------
// 同步
// ---------------------------------------------------------------------------
function handleSync(row) {
  syncResult.open = true
  syncResult.loading = true
  syncResult.requestFailed = false
  syncResult.failMsg = ''
  syncResult.serverId = row.id
  syncResult.serverName = row.name || row.code || ''
  syncResult.data = null
  syncingId.value = row.id
  syncAgentMcpServer(row.id)
    .then((response) => {
      syncResult.data = response && response.data ? response.data : null
      if (!syncResult.data) {
        syncResult.requestFailed = true
        syncResult.failMsg = '响应缺少同步结果'
      }
    })
    .catch((error) => {
      syncResult.requestFailed = true
      syncResult.failMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      syncResult.loading = false
      syncingId.value = null
    })
}

// ---------------------------------------------------------------------------
// 工具抽屉
// ---------------------------------------------------------------------------
function openTools(row) {
  tools.serverId = row.id
  tools.serverName = row.name || row.code || ''
  tools.open = true
  loadTools()
}

/** 从探测/同步结果弹窗跳工具清单（先关结果弹窗，避免两层弹窗叠着） */
function openToolsFromProbe() {
  // 两个弹窗同一时间只会开一个：探测结果优先，否则取同步结果
  const serverId = probeResult.open ? probeResult.serverId : syncResult.serverId
  const name = probeResult.open ? probeResult.serverName : syncResult.serverName
  probeResult.open = false
  syncResult.open = false
  tools.serverId = serverId
  tools.serverName = name
  tools.open = true
  loadTools()
}

/** 加载工具清单（loading 必须 finally 复位；空/失败/加载三态） */
function loadTools() {
  if (!tools.serverId) return
  tools.loading = true
  tools.failed = false
  tools.failMsg = ''
  listAgentMcpTools(tools.serverId)
    .then((response) => {
      tools.rows = normalizeRows(response)
    })
    .catch((error) => {
      tools.rows = []
      tools.failed = true
      tools.failMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      tools.loading = false
    })
}

onMounted(() => {
  getList()
})
</script>

<style lang="scss" scoped>
/* 抽屉/弹窗都带 append-to-body，内容 teleport 到 body，弹窗内样式一律写顶层（决策审计页踩过坑） */

.list-wrap {
  min-height: 220px;
}

.list-skeleton {
  padding: 8px 0;
}

.endpoint {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 放在 el-tooltip 里的按钮容器：禁用按钮自身不派发鼠标事件，需要外层 span 承接 tooltip 触发 */
.op-wrap {
  display: inline-block;
  margin-right: 8px;
}

.form-wrap {
  max-height: 74vh;
  overflow-y: auto;
}

.env-box {
  width: 100%;
}

.env-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.env-arrow {
  color: var(--el-text-color-secondary);
}

.section-title {
  margin: 12px 0 8px;
  font-size: 13px;
  font-weight: 600;
}

.probe-tools {
  margin-top: 4px;
}

.sync-note {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}

.rejected-text {
  color: var(--el-color-warning);
  font-weight: 700;
}

.err-text {
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.tools-wrap {
  min-height: 200px;
}

.mt8 {
  margin-top: 8px;
}

.dim {
  margin-left: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.hint {
  margin-top: 2px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.hint-warn {
  color: var(--el-color-warning);
}

.foot-tip {
  float: left;
  line-height: 32px;
  font-size: 12px;
}
</style>
