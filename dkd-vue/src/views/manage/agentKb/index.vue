<template>
  <div class="app-container agent-kb">
    <!-- 顶部统计卡：文档数 / 切片数 / 检索次数 / 检索回退率（回退率=仅列证据回退占比，5-3 治理指标） -->
    <el-row :gutter="12" class="mb8">
      <el-col :xs="24" :sm="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">L2 文档数</div>
          <el-skeleton v-if="stats.loading" :rows="1" animated />
          <div v-else class="stat-value" :title="stats.error ? '统计加载失败：' + stats.error : ''">
            {{ stats.error ? '—' : (stats.data.documents ?? 0) }}
          </div>
          <div class="stat-foot">已入库的文档（含停用）</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">切片数</div>
          <el-skeleton v-if="stats.loading" :rows="1" animated />
          <div v-else class="stat-value">{{ stats.error ? '—' : (stats.data.chunks ?? 0) }}</div>
          <div class="stat-foot">检索的最小单位（300~500 字/片）</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">检索次数</div>
          <el-skeleton v-if="stats.loading" :rows="1" animated />
          <div v-else class="stat-value">{{ stats.error ? '—' : (stats.data.retrievals ?? 0) }}</div>
          <div class="stat-foot">含本次页面的检索测试（每次都会记录）</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">
            <el-tooltip content="仅列证据回退率，目标 <15%（5-3）" placement="top">
              <span>检索回退率（仅列证据）</span>
            </el-tooltip>
          </div>
          <el-skeleton v-if="stats.loading" :rows="1" animated />
          <div
            v-else
            class="stat-value"
            :class="{ 'stat-value--warn': !stats.error && fallbackRateHigh }"
            :title="stats.error ? '统计加载失败：' + stats.error : ''"
          >
            {{ stats.error ? '—' : fallbackRateText }}
          </div>
          <div class="stat-foot">
            <span v-if="stats.error">统计加载失败</span>
            <span v-else>{{ fallbackDetail }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="76px">
      <el-form-item label="分类" prop="category">
        <el-select v-model="queryParams.category" placeholder="全部分类" clearable style="width: 150px">
          <el-option v-for="item in CATEGORIES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态" prop="enabled">
        <el-select v-model="queryParams.enabled" placeholder="全部状态" clearable style="width: 130px">
          <el-option label="启用" :value="1" />
          <el-option label="停用" :value="0" />
        </el-select>
      </el-form-item>
      <el-form-item label="关键词" prop="keyword">
        <el-input
          v-model="queryParams.keyword"
          placeholder="文档编码/标题（≤100 字）"
          clearable
          maxlength="100"
          style="width: 190px"
          @keyup.enter="handleQuery"
        />
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
          v-hasPermi="['manage:agentKb:ingest']"
          @click="openIngest"
        >新增文档</el-button>
      </el-col>
      <el-col :span="1.5">
        <el-button plain icon="Refresh" @click="reloadAll">刷新</el-button>
      </el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList" />
    </el-row>

    <!--
      页面自解释（信息架构红线，缺口文档 §三）：
      知识库分三层——L1 结构化事实走只读工具直查、L2 文档语料（本页）、L3 案例库（未启用）。
      把取数塞进 RAG 是精度灾难；L2 只回答“厂商手册/故障码表/SOP 里怎么写”。
    -->
    <el-alert
      class="mb8"
      type="info"
      :closable="false"
      show-icon
      title="知识库只用于文档语料（L2）；设备/库存/销量等结构化事实请走只读工具直查"
      description="入库由 Python 侧切片与本地向量化（文档/切片/检索记录属智能体自有数据 agent_kb_*，Java 只做代理与鉴权）。检索测试每次都会写一条检索记录；无命中或相似度低于阈值时智能体必须回退「仅列证据」模板，不编造知识库结论。"
    />

    <el-row :gutter="12">
      <!-- 左：文档列表 -->
      <el-col :xs="24" :lg="15">
        <el-card shadow="never" class="list-card">
          <div class="list-wrap">
            <!-- 三态①：加载骨架 -->
            <el-skeleton v-if="loading" :rows="8" animated class="list-skeleton" />

            <!-- 三态②：加载失败（原因 + 重试） -->
            <el-empty v-else-if="loadError" :description="'文档列表加载失败：' + loadError">
              <el-button type="primary" @click="getList">重试</el-button>
            </el-empty>

            <!-- 三态③：空数据（提示放宽筛选/新增，而不是“知识库坏了”） -->
            <el-empty
              v-else-if="documentList.length === 0"
              description="当前筛选条件下没有文档：可放宽分类/状态/关键词，或新增第一份语料"
            >
              <el-button v-hasPermi="['manage:agentKb:ingest']" type="primary" @click="openIngest">新增文档</el-button>
            </el-empty>

            <el-table v-else :data="documentList" border size="small" row-key="docCode">
              <el-table-column label="文档编码" min-width="170">
                <template #default="{ row }">
                  <span class="mono">{{ row.docCode }}</span>
                </template>
              </el-table-column>
              <el-table-column label="标题" prop="title" min-width="200" show-overflow-tooltip />
              <el-table-column label="分类" width="110" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="categoryTag(row.category)">{{ categoryLabel(row.category) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="来源" min-width="150">
                <template #default="{ row }">
                  <div>{{ sourceTypeLabel(row.sourceType) }}</div>
                  <div v-if="row.sourceRef" class="dim">{{ row.sourceRef }}</div>
                </template>
              </el-table-column>
              <el-table-column label="切片数" width="80" align="center">
                <template #default="{ row }">{{ row.chunkCount ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="状态" width="100" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="更新时间" width="170">
                <template #default="{ row }">{{ formatTime(row.updateTime) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="150" align="center" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" link @click="openDetail(row)">详情</el-button>
                  <el-button
                    size="small"
                    type="primary"
                    link
                    v-hasPermi="['manage:agentKb:edit']"
                    :loading="togglingCode === row.docCode"
                    :disabled="row.status === 0"
                    @click="handleToggle(row)"
                  >{{ row.enabled === 1 ? '停用' : '启用' }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!--
            分页 pageSize 上限由 Python 侧强制（≤100）；本项目 Pagination 默认档位 [10,20,30,50]
            全部在限内，故不额外限制选项——但不要随意加 200/500 档，否则会拿到 502 而不是分页变大。
          -->
          <pagination
            v-show="total > 0"
            :total="total"
            v-model:page="queryParams.pageNum"
            v-model:limit="queryParams.pageSize"
            @pagination="getList"
          />
        </el-card>
      </el-col>

      <!-- 右：检索测试面板（只读召回，调阈值用；每次检索都会写一条检索记录） -->
      <el-col :xs="24" :lg="9">
        <el-card shadow="never" class="panel-card">
          <template #header>
            <div class="panel-head">
              <span>检索测试（L2 召回）</span>
              <el-tag size="small" type="info">每次检索都会记录</el-tag>
            </div>
          </template>

          <el-form label-position="top">
            <el-form-item label="检索问题">
              <el-input
                v-model="search.query"
                type="textarea"
                :rows="3"
                maxlength="500"
                show-word-limit
                placeholder="例如：A1000001 出货异常，按厂商手册应该先查什么？"
              />
            </el-form-item>
            <el-form-item label="期望召回数（topK）">
              <div class="inline-field">
                <el-input-number v-model="search.topK" :min="1" :max="20" controls-position="right" style="width: 120px" />
                <span class="hint">先取 topK 再按阈值过滤；最终引用 ≤3 条</span>
              </div>
            </el-form-item>
            <el-button
              type="primary"
              :loading="search.loading"
              v-hasPermi="['manage:agentKb:search']"
              @click="runSearch"
            >检索</el-button>
          </el-form>

          <div class="search-result">
            <!-- 顺序有意：加载 → 失败 → 未检索 → 结果。失败必须在“未检索”之前判断，
                 否则检索失败会被错误地画成“还没开始搜”（两种状态的处理完全不同）。 -->
            <el-skeleton v-if="search.loading" :rows="6" animated />

            <el-alert
              v-else-if="search.failed"
              type="error"
              :closable="false"
              show-icon
              :title="'检索失败：' + search.failMsg"
            >
              <el-button type="primary" link @click="runSearch">重试</el-button>
            </el-alert>

            <!-- 未检索：说明这是只读召回测试，不会写业务数据 -->
            <el-empty
              v-else-if="!search.searched"
              :image-size="70"
              description="输入问题后点「检索」：只做召回与阈值验证，不会修改任何业务数据"
            />

            <template v-else>
              <div class="result-meta">
                命中 {{ hits.length }} 条 · topK {{ search.data?.topK ?? search.topK }} · 阈值
                {{ formatScore(search.data?.minScore) }} · 检索ID
                <span class="mono">{{ search.data?.retrievalId ?? '—' }}</span>
              </div>

              <div v-if="hits.length > 0" class="hit-list">
                <div v-for="(hit, index) in hits" :key="hit.chunkId || index" class="hit">
                  <div class="hit-head">
                    <el-tag size="small" :type="scoreTag(hit.score)">{{ formatScore(hit.score) }}</el-tag>
                    <span class="hit-title">{{ hit.title || '（无标题）' }}</span>
                    <span class="mono dim">{{ hit.docCode }}</span>
                  </div>
                  <!-- 摘录用文本插值渲染（不渲染 HTML/markdown）：语料是外部内容，v-html 会开注入面 -->
                  <pre class="hit-excerpt">{{ hit.excerpt || '（无摘要）' }}</pre>
                </div>
              </div>

              <!--
                无命中不是“没结果可看”，而是一个**必须显式说明的治理信号**：
                3-6 的引用强制校验要求无命中/低分时回退「仅列证据」模板，不得编造结论。
              -->
              <div v-else class="fallback">
                <el-alert
                  type="warning"
                  :closable="false"
                  show-icon
                  title="无命中：已回退「仅列证据」模板"
                  :description="fallbackText"
                />
                <div class="fallback-foot">
                  回退原因码：<span class="mono">{{ search.data?.fallbackReason || 'no_hit' }}</span>
                  <span class="dim">（no_hit 无召回 / low_score 相似度低于阈值 / citation_missing 无可引用断言）</span>
                </div>
              </div>
            </template>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!--
      L3 案例库占位卡：**不可点、不填演示数据、无后端**。
      它是“如实告知”而不是功能入口——判定依据与恢复前提见
      docs/agent-knowledge-corpus-inventory.md §3（FIX-22 数据不达标）。
    -->
    <el-card shadow="never" class="l3-card">
      <template #header>
        <div class="panel-head">
          <span>L3 案例库（历史维修工单结构化案例）</span>
          <el-tag type="warning" size="small">未启用 · 数据未达标（FIX-22）</el-tag>
        </div>
      </template>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="维修类工单条数">
          5 条 <span class="dim">（门槛 ≥50 条）</span>
        </el-descriptions-item>
        <el-descriptions-item label="现象文本平均长度">
          4.0 字 <span class="dim">（门槛 ≥15 字）</span>
        </el-descriptions-item>
        <el-descriptions-item label="结构化字段">
          不存在 <span class="dim">（tb_task 只有 desc 备注，无「现象/原因/处置」列）</span>
        </el-descriptions-item>
        <el-descriptions-item label="本页处理">
          不建 L3 表、不填演示案例、不提供可点入口
        </el-descriptions-item>
      </el-descriptions>
      <p class="l3-note">
        为什么不做：这批数据无法支撑相似案例召回，检索结果不可验证，比没有更危险（计划表 R11）。
        恢复前提见 docs/agent-knowledge-corpus-inventory.md §3.2：业务为维修单补「现象/原因/处置」结构化回填（≥50 条且现象平均 ≥15 字），
        或拿到厂商故障码表走文档语料（路径 ②）。未达标前 L3 保持挂起。
      </p>
      <el-button disabled>未启用，无可用入口</el-button>
    </el-card>

    <!-- 新增文档抽屉：入库 = 切片 + 本地向量化（Python 侧完成），这里只提交纯文本 -->
    <el-drawer v-model="ingest.open" title="新增知识库文档（L2 语料）" size="720px" append-to-body :close-on-click-modal="false">
      <div class="form-wrap">
        <el-alert
          class="mb8"
          type="info"
          :closable="false"
          show-icon
          title="只提交提取后的纯文本（≤1MB）"
          description="首版支持粘贴文本/文本引用（PDF/Word 请先转文本再粘）。内容会按语义章节切片并做本地向量化，写入 agent_kb_* 表；相同内容（content_hash 一致）会跳过重复切片。"
        />
        <el-alert
          v-if="ingest.errorMsg"
          class="mb8"
          type="error"
          :closable="false"
          show-icon
          :title="'入库失败：' + ingest.errorMsg"
        />

        <el-form label-width="110px">
          <el-form-item label="文档编码" required>
            <div class="inline-field">
              <el-input v-model="ingest.data.docCode" maxlength="64" placeholder="如 valve-manual-a1000001" />
              <el-button link type="primary" @click="genDocCode">自动生成</el-button>
            </div>
            <div class="hint">3~64 位：小写字母/数字开头，可含 - 和 _；编码唯一（重复会入库失败）</div>
          </el-form-item>
          <el-form-item label="标题" required>
            <el-input v-model="ingest.data.title" maxlength="200" show-word-limit placeholder="如 A1000001 冷柜机型维修手册" />
          </el-form-item>
          <el-form-item label="分类" required>
            <el-select v-model="ingest.data.category" style="width: 100%">
              <el-option v-for="item in CATEGORIES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="来源类型" required>
            <el-select v-model="ingest.data.sourceType" style="width: 100%">
              <el-option v-for="item in SOURCE_TYPES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="来源引用">
            <el-input v-model="ingest.data.sourceRef" maxlength="500" placeholder="文件名 / URL / 出处说明（不填敏感内容）" />
          </el-form-item>
          <el-form-item label="正文" required>
            <el-input
              v-model="ingest.data.content"
              type="textarea"
              :rows="12"
              placeholder="粘贴提取后的纯文本。建议按章节组织（标题/步骤/故障码行）；故障码表建议一行一码"
            />
            <div class="hint" :class="{ 'hint-warn': contentOversize }">
              字符数 {{ (ingest.data.content || '').length }} · 字节数 {{ contentBytes }} / 上限 1,048,576
              （UTF-8；超出会被前端直接拦截）
            </div>
          </el-form-item>
          <el-form-item label="操作人">
            <el-input :model-value="operator" disabled />
            <div class="hint">取自当前登录用户，随入库请求一起提交用于留痕</div>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <span class="dim foot-tip">写入方是 Python ingest（agent_* 自有数据）；本页不直接写库</span>
        <el-button @click="ingest.open = false">取消</el-button>
        <el-button type="primary" :loading="ingest.saving" @click="submitIngest">入库</el-button>
      </template>
    </el-drawer>

    <!-- 文档详情：列表接口不带正文（避免大字段），能看到的元信息如实展示，不假装有摘要 -->
    <el-dialog v-model="detail.open" title="文档详情" width="680px" append-to-body>
      <div v-if="detail.row">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="文档编码" :span="2">
            <span class="mono">{{ detail.row.docCode }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="标题" :span="2">{{ detail.row.title || '—' }}</el-descriptions-item>
          <el-descriptions-item label="分类">{{ categoryLabel(detail.row.category) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="statusTag(detail.row.status)">{{ statusLabel(detail.row.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="来源类型">{{ sourceTypeLabel(detail.row.sourceType) }}</el-descriptions-item>
          <el-descriptions-item label="切片数">{{ detail.row.chunkCount ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="来源引用" :span="2">{{ detail.row.sourceRef || '—' }}</el-descriptions-item>
          <el-descriptions-item label="内容指纹" :span="2">
            <span class="mono">{{ detail.row.contentHash || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="更新时间" :span="2">{{ formatTime(detail.row.updateTime) }}</el-descriptions-item>
        </el-descriptions>

        <!-- 若后端以后在列表/详情 VO 里补正文摘要会自动显示；当前契约不含 content，故如实提示 -->
        <div v-if="detailExcerpt" class="detail-content">
          <div class="detail-content__title">内容摘要</div>
          <pre class="detail-content__body">{{ detailExcerpt }}</pre>
        </div>
        <el-alert
          v-else
          class="detail-hint"
          type="info"
          :closable="false"
          show-icon
          title="列表接口不返回正文"
          description="文档正文属大字段（≤1MB），列表/详情 VO 刻意不带上（否则分页会越来越慢）。需要验证内容是否可召回，请用右侧「检索测试」搜关键词；正文以入库源为准。"
        />
      </div>
      <template #footer>
        <el-button @click="detail.open = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="AgentKb">
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import useUserStore from '@/store/modules/user'
import {
  getAgentKbStats,
  ingestAgentKbDocument,
  listAgentKbDocuments,
  searchAgentKb,
  setAgentKbDocumentEnabled
} from '@/api/manage/agentKb'

/**
 * 知识库 L2 页面（S11，排期任务 3-6）。
 *
 * 数据来源：`agent_kb_document / agent_kb_chunk / agent_kb_retrieval`（docs/ddl/agent_kb_mcp.sql）。
 * 这三张表属**智能体自有数据**（AGENTS §7.3）：写入方是 Python ingest（切片+本地向量化），
 * Java 只代理查询/入库/启停/检索测试。页面不做任何“前端直写库”，也不渲染语料 HTML。
 *
 * 三个取舍：
 *  1. **L3 只放占位卡**：FIX-22 数据不达标（5 条 / 平均 4 字 / 无结构化字段），
 *     如实显示「未启用 · 数据未达标」，不填演示数据、不留可点入口——不可验证的检索比没有更危险。
 *  2. **检索测试是只读召回**：用于调阈值（score/minScore）与验证 fallback；每次检索都会写一条
 *     检索记录（只增），所以页面上明确写出“每次都会记录”，避免把它当免费试玩。
 *  3. **详情不假装有正文**：列表契约不含 content（大字段分离），详情展示元信息 + 内容指纹；
 *     需要看内容效果就用检索测试，而不是让列表带 1MB 正文把分页拖死。
 */

const CATEGORIES = [
  { value: 'manual', label: '厂商手册', tag: '' },
  { value: 'fault_code', label: '故障码表', tag: 'warning' },
  { value: 'sop', label: '运营 SOP', tag: 'success' },
  { value: 'faq', label: '常见问题', tag: 'info' },
  { value: 'other', label: '其他', tag: 'info' }
]
const SOURCE_TYPES = [
  { value: 'text', label: '粘贴/上传文本' },
  { value: 'file', label: '文件名引用' },
  { value: 'url', label: '链接（只存引用文本）' }
]
/** 与 agent_kb_document.status 列注释一致：1-已启用 2-已停用 0-处理中（切片失败时保持 0） */
const STATUSES = {
  1: { label: '已启用', tag: 'success' },
  2: { label: '已停用', tag: 'info' },
  0: { label: '处理中', tag: 'warning' }
}
/** 回退原因码的中文解释（与 agent_kb_retrieval.fallback_reason 列注释一致） */
const FALLBACK_REASONS = {
  no_hit: '没有任何片段被召回：语料未覆盖这个问题（新增/调整语料后再试）',
  low_score: '召回到了片段，但相似度低于阈值：按引用强制校验不引用（可调阈值复测）',
  citation_missing: '召回到了片段，但结论没有可绑定相似度的断言：回退「仅列证据」'
}

const userStore = useUserStore()
/** 操作人：取自登录态（后端入库接口要求 operator 字段，用于留痕） */
const operator = computed(() => userStore.name || 'unknown')
/** 正文上限 1MB（与 agent_kb_document.content 的应用层限制一致，前端先拦一次） */
const MAX_CONTENT_BYTES = 1024 * 1024

const loading = ref(true)
const loadError = ref('')
const showSearch = ref(true)
const documentList = ref([])
const total = ref(0)
const queryRef = ref(null)
/** 正在启停的文档编码：只让该行按钮转圈 */
const togglingCode = ref(null)

/** 统计卡状态：失败只把数字显示成「—」并给 tooltip，不弹 toast（统计失败不该打断列表操作） */
const stats = reactive({
  loading: true,
  error: '',
  data: {
    documents: 0,
    chunks: 0,
    retrievals: 0,
    // 5-3 引用治理：回退次数与回退率（Python /agents/kb/stats 增量返回；未上线时为空）
    fallbackRetrievals: null,
    fallbackRate: null
  }
})

/** 回退率（0~1）；缺失/非法视为无数据（不要显示成 0%，那会被读成“零回退，治理达标”） */
const fallbackRate = computed(() => {
  const value = stats.data.fallbackRate
  if (value === null || value === undefined || value === '') return null
  const num = Number(value)
  return Number.isNaN(num) ? null : num
})

/** 回退率展示：两位小数百分比；无数据显示「—」 */
const fallbackRateText = computed(() => {
  if (fallbackRate.value === null) return '—'
  return (fallbackRate.value * 100).toFixed(2) + '%'
})

/** 目标 <15%（5-3）：超过阈值用 warning 色，提醒需要回去看语料/阈值，而不是继续加语料硬撑 */
const fallbackRateHigh = computed(() => fallbackRate.value !== null && fallbackRate.value > 0.15)

/** 卡片副文案：能拿到回退/总检索次数就写清分子分母（百分比没有分母时最容易被误读） */
const fallbackDetail = computed(() => {
  if (fallbackRate.value === null) return '暂无数据（后端未返回 fallbackRate）'
  const fallback = stats.data.fallbackRetrievals
  const total = stats.data.retrievals
  if (fallback === null || fallback === undefined || total === null || total === undefined) {
    return '目标 <15%（5-3）；回退=检索无引用时回退「仅列证据」'
  }
  return '回退 ' + fallback + ' / 检索 ' + total + ' 次；目标 <15%（5-3）'
})

/** 新增文档抽屉状态 */
const ingest = reactive({
  open: false,
  saving: false,
  errorMsg: '',
  data: emptyIngest()
})

/** 文档详情弹窗 */
const detail = reactive({ open: false, row: null })

/** 检索测试面板：searched 用来区分“还没搜过”和“搜了但零命中”（两者 UX 语义完全不同） */
const search = reactive({
  loading: false,
  failed: false,
  failMsg: '',
  searched: false,
  query: '',
  topK: 5,
  data: null
})

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    category: null,
    enabled: null,
    keyword: null
  }
})
const { queryParams } = toRefs(data)

/** 命中列表（data.hits 缺失时回落空数组，页面不因契约缺字段崩） */
const hits = computed(() => (search.data && Array.isArray(search.data.hits) ? search.data.hits : []))

/** 正文 UTF-8 字节数：用 Blob 计（输入框里的是字符，1MB 限制按字节算才是真正的传输/入库上限） */
const contentBytes = computed(() => {
  const text = ingest.data.content || ''
  if (!text) return 0
  try {
    return new Blob([text]).size
  } catch (error) {
    // 极端环境拿不到 Blob 时退回字符数估计（ASCII 1:1，中文 3 字节的上界）
    return text.length * 3
  }
})
const contentOversize = computed(() => contentBytes.value > MAX_CONTENT_BYTES)

/**
 * 详情里的内容摘要：当前契约不含 content，但后端以后若补 content/contentSummary/excerpt
 * 会自动显示（防御式读取，避免“多返回了字段前端却不认”）。
 */
const detailExcerpt = computed(() => {
  const row = detail.row
  if (!row) return ''
  const text = row.contentSummary || row.excerpt || row.content || ''
  if (!text) return ''
  const limit = 2000
  return text.length > limit ? text.slice(0, limit) + '\n…（已截断，仅展示前 ' + limit + ' 字）' : text
})

const fallbackText = computed(() => {
  const reason = (search.data && search.data.fallbackReason) || 'no_hit'
  const base = FALLBACK_REASONS[reason] || '未命中可用片段'
  return base + '。按 3-6 引用强制校验：知识库没有可引用片段时，智能体必须回退为「仅列证据」模板，不得编造知识库结论。'
})

function emptyIngest() {
  return {
    docCode: '',
    title: '',
    category: 'manual',
    sourceType: 'text',
    sourceRef: '',
    content: ''
  }
}

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------
function categoryHit(value) {
  return CATEGORIES.filter((item) => item.value === value)[0] || null
}

function categoryLabel(value) {
  const hit = categoryHit(value)
  return hit ? hit.label : String(value || '—')
}

function categoryTag(value) {
  const hit = categoryHit(value)
  return hit ? hit.tag : 'info'
}

function sourceTypeLabel(value) {
  const hit = SOURCE_TYPES.filter((item) => item.value === value)[0]
  return hit ? hit.label : String(value || '—')
}

/** 状态：优先用后端 status；缺失时按 enabled 推断（“启用/停用”的数字口径一致） */
function statusLabel(value) {
  const hit = STATUSES[value]
  if (hit) return hit.label
  if (value === undefined || value === null) return '—'
  return '状态 ' + value
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

/** 相似度分数统一两位小数（(Number)?.toFixed 前先判空，避免 null 变 NaN 显示） */
function formatScore(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isNaN(num) ? String(value) : num.toFixed(2)
}

/** 分数配色：≥0.5 绿、≥0.3 橙、其余灰（阈值以后端 minScore 为准，这里只是可读性提示） */
function scoreTag(value) {
  const num = Number(value)
  if (Number.isNaN(num)) return 'info'
  if (num >= 0.5) return 'success'
  if (num >= 0.3) return 'warning'
  return 'info'
}

/** 空串不传：传 `category=` 这类空参数可能被后端当“已填写但为空”而查出 0 条 */
function buildQuery() {
  const query = {
    pageNum: queryParams.value.pageNum,
    pageSize: queryParams.value.pageSize
  }
  const optional = ['category', 'enabled', 'keyword']
  optional.forEach((key) => {
    const value = queryParams.value[key]
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      query[key] = String(value).trim()
    }
  })
  return query
}

// ---------------------------------------------------------------------------
// 列表 / 统计
// ---------------------------------------------------------------------------
/**
 * 分页响应归一化。
 *
 * 当前实现是「Java 代理 Python」：`GET /manage/agent/kb/documents` 返回 AjaxResult，
 * 分页数据在 `data.{total,rows}` 里；而 RuoYi 惯例的裸 TableDataInfo 是顶层 `{total,rows}`。
 * 两种都接（外加裸数组），否则一旦某一侧换了信封形态，列表会**恒空**且不报错——
 * 这种“静默空列表”最难排查，必须两边兜住。
 */
function normalizePage(response) {
  if (response && Array.isArray(response.rows)) {
    return { rows: response.rows, total: response.total || 0 }
  }
  if (response && response.data && Array.isArray(response.data.rows)) {
    return { rows: response.data.rows, total: response.data.total || 0 }
  }
  if (Array.isArray(response)) return { rows: response, total: response.length }
  if (response && Array.isArray(response.data)) return { rows: response.data, total: response.data.length }
  return { rows: [], total: 0 }
}

/** 查询文档列表（loading 必须 finally 复位，AGENTS §2.2） */
function getList() {
  loading.value = true
  loadError.value = ''
  listAgentKbDocuments(buildQuery())
    .then((response) => {
      const page = normalizePage(response)
      documentList.value = page.rows
      total.value = page.total
    })
    .catch((error) => {
      loadError.value = (error && error.message) || '网络异常'
      documentList.value = []
      total.value = 0
    })
    .finally(() => {
      loading.value = false
    })
}

/** 查询统计（失败只把卡片显示成「—」，不打断页面其他部分） */
function loadStats() {
  stats.loading = true
  stats.error = ''
  getAgentKbStats()
    .then((response) => {
      // 同 search：兼容扁平 data 与 {stats: {...}} 两种包装（Python/Java 任一侧换信封都不至于把统计画成 0）
      const raw = response.data || {}
      const payload = raw.stats && typeof raw.stats === 'object' ? raw.stats : raw
      stats.data = {
        documents: payload.documents ?? 0,
        chunks: payload.chunks ?? 0,
        retrievals: payload.retrievals ?? 0,
        // 新字段保留原值（null=未返回），不要兜底成 0：0% 回退率与“无数据”结论相反
        fallbackRetrievals: payload.fallbackRetrievals ?? null,
        fallbackRate: payload.fallbackRate ?? null
      }
    })
    .catch((error) => {
      stats.error = (error && error.message) || '网络异常'
    })
    .finally(() => {
      stats.loading = false
    })
}

function reloadAll() {
  getList()
  loadStats()
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
// 启停（二次确认）
// ---------------------------------------------------------------------------
function handleToggle(row) {
  const next = row.enabled === 1 ? 0 : 1
  const title = next === 1 ? '启用确认' : '停用确认'
  const message = next === 1
    ? '启用后「' + row.title + '」的切片会参与检索。文档编码「' + row.docCode + '」，确认启用？'
    : '停用后「' + row.title + '」不再参与检索（已产生的检索/引用记录不回滚）。确认停用？'
  ElMessageBox.confirm(message, title, {
    type: 'warning',
    confirmButtonText: next === 1 ? '启用' : '停用',
    cancelButtonText: '取消'
  })
    .then(() => submitToggle(row, next))
    .catch(() => {})
}

function submitToggle(row, next) {
  togglingCode.value = row.docCode
  setAgentKbDocumentEnabled(row.docCode, { enabled: next })
    .then(() => {
      // 本地同步两个字段：status 与 enabled 同源（1/0），后端可能只回其中一个
      row.enabled = next
      row.status = next === 1 ? 1 : 2
      ElMessage.success(next === 1 ? '已启用，下一次检索生效' : '已停用，下一次检索生效')
    })
    .catch((error) => {
      ElMessage.error('启停失败：' + ((error && error.message) || '网络异常'))
    })
    .finally(() => {
      togglingCode.value = null
    })
}

function openDetail(row) {
  detail.row = row
  detail.open = true
}

// ---------------------------------------------------------------------------
// 新增文档
// ---------------------------------------------------------------------------
function openIngest() {
  ingest.data = emptyIngest()
  ingest.errorMsg = ''
  ingest.saving = false
  ingest.open = true
}

/** 生成一个可用编码：kb-<日期>-<时分秒>-<随机>，满足 3~64 位小写/数字/- 的规则 */
function genDocCode() {
  const rand = Math.random().toString(36).slice(2, 6)
  ingest.data.docCode = 'kb-' + dayjs().format('YYYYMMDD-HHmmss') + '-' + rand
}

/**
 * 入库前校验：只做契约要求的显式约束（编码格式、标题、正文非空且 ≤1MB）。
 * 切片规则/去重由 Python 侧裁决，前端不重复实现（重复实现必然漂移）。
 */
function validateIngest() {
  const form = ingest.data
  const code = (form.docCode || '').trim()
  if (!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(code)) {
    ingest.errorMsg = '文档编码不合法：3~64 位，小写字母/数字开头，可含 - 和 _'
    return false
  }
  if (!(form.title || '').trim()) {
    ingest.errorMsg = '标题必填'
    return false
  }
  if (!(form.content || '').trim()) {
    ingest.errorMsg = '正文必填（粘贴提取后的纯文本）'
    return false
  }
  if (contentOversize.value) {
    ingest.errorMsg = '正文 ' + contentBytes.value + ' 字节，超过 1MB 上限（1,048,576）；请拆分文档后分别入库'
    return false
  }
  return true
}

/**
 * 入库结果的提示文案（对齐 java-log 实装形状）。
 *
 * 实装返回：`{docCode, chunks, chunkCount, skipped, skippedChunks, contentHash}`。
 * ⚠️ `skipped` 可能是**布尔**（Python kb_store：contentHash 相同 → skipped=True），
 * 也可能是数字；直接拼「true 个切片被跳过」会变成一句胡话，所以两种语义分开翻译。
 * 静默跳过必须显式提示：否则运营会以为“入库了但切片是 0”是 bug。
 */
function ingestResultMessage(result) {
  const chunkCount =
    result.chunkCount ?? (Array.isArray(result.chunks) ? result.chunks.length : result.chunks) ?? 0
  const skippedChunks = Array.isArray(result.skippedChunks) ? result.skippedChunks.length : 0
  if (result.skipped === true) {
    return (
      '内容与已有文档一致（contentHash 相同），未新增切片' +
      (chunkCount ? '；该文档现有 ' + chunkCount + ' 个切片' : '')
    )
  }
  const skipped = typeof result.skipped === 'number' ? result.skipped : skippedChunks
  return '已入库：' + chunkCount + ' 个切片' + (skipped ? '（跳过 ' + skipped + ' 个切片：内容重复）' : '')
}

function submitIngest() {
  if (!validateIngest()) return
  ingest.saving = true
  ingest.errorMsg = ''
  const payload = {
    docCode: ingest.data.docCode.trim(),
    title: ingest.data.title.trim(),
    category: ingest.data.category,
    sourceType: ingest.data.sourceType,
    sourceRef: (ingest.data.sourceRef || '').trim() || null,
    content: ingest.data.content,
    operator: operator.value
  }
  ingestAgentKbDocument(payload)
    .then((response) => {
      // Java 代理 Python：当前实装是扁平 data（{docCode,chunks,chunkCount,skipped,...}）；
      // 兼容历史上文档写过的 {ingest: {...}} 包装，避免某一侧换信封后切片数显示成 0
      const raw = response.data || {}
      const result = raw.ingest && typeof raw.ingest === 'object' ? raw.ingest : raw
      ElMessage.success(ingestResultMessage(result))
      ingest.open = false
      reloadAll()
    })
    .catch((error) => {
      // 持久展示在抽屉里（silent 写接口，拦截器不弹）
      ingest.errorMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      ingest.saving = false
    })
}

// ---------------------------------------------------------------------------
// 检索测试
// ---------------------------------------------------------------------------
/**
 * 执行检索测试。
 *
 * 为什么检索失败也保留面板状态：失败（Python 不可达/参数错误）与“零命中”是两回事——
 * 零命中要展示 fallback 治理信息，失败要展示原因并可重试，页面不能把它们混成一个空态。
 * 每次成功检索都会写一条检索记录，所以成功后刷新统计卡（检索次数是真实使用口径）。
 */
function runSearch() {
  const query = (search.query || '').trim()
  if (!query) {
    ElMessage.warning('请先输入检索问题')
    return
  }
  if (!Number.isFinite(search.topK) || search.topK < 1 || search.topK > 20) {
    ElMessage.warning('topK 需在 1~20 之间')
    return
  }
  search.loading = true
  search.failed = false
  search.failMsg = ''
  search.data = null
  searchAgentKb({ query, topK: search.topK })
    .then((response) => {
      // Java 代理 Python：检索结果当前按“扁平 data”约定（hits/retrievalId/fallbackReason），
      // 但 Python 侧（task-16）也可能包一层 {search: ...}；两种都接，避免“有数据却显示无命中”。
      const raw = response.data || {}
      search.data = raw.search && typeof raw.search === 'object' ? raw.search : raw
      search.searched = true
      loadStats()
    })
    .catch((error) => {
      search.failed = true
      search.failMsg = (error && error.message) || '网络异常'
    })
    .finally(() => {
      search.loading = false
    })
}

onMounted(() => {
  loadStats()
  getList()
})
</script>

<style lang="scss" scoped>
.stat-card {
  .stat-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .stat-value {
    margin: 4px 0 2px;
    font-size: 24px;
    font-weight: 600;
    line-height: 1.3;
  }

  /* 回退率超阈值（>15%）用 warning 色：数值本身要“扎眼”，否则治理指标会被当装饰忽略 */
  .stat-value--warn {
    color: var(--el-color-warning);
  }

  .stat-foot {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

.list-card,
.panel-card {
  margin-bottom: 12px;
}

.list-wrap {
  min-height: 220px;
}

.list-skeleton {
  padding: 8px 0;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
}

.inline-field {
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-result {
  margin-top: 12px;
}

.result-meta {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}

.hit-list {
  max-height: 420px;
  overflow-y: auto;
}

.hit {
  padding: 8px 10px;
  margin-bottom: 8px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;

  .hit-head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .hit-title {
    font-size: 13px;
    font-weight: 600;
  }

  /* 摘录保留换行并强制换行：语料是外部内容，不渲染 HTML */
  .hit-excerpt {
    margin: 6px 0 0;
    padding: 8px;
    background: var(--el-fill-color-light);
    border-radius: 4px;
    font-size: 12px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
  }
}

.fallback {
  .fallback-foot {
    margin-top: 8px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

.l3-card {
  margin-bottom: 12px;
}

.l3-note {
  margin: 10px 0 12px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}

.form-wrap {
  max-height: 74vh;
  overflow-y: auto;
}

.detail-content {
  margin-top: 12px;

  &__title {
    margin-bottom: 6px;
    font-size: 13px;
    font-weight: 600;
  }

  &__body {
    max-height: 260px;
    overflow: auto;
    margin: 0;
    padding: 10px;
    background: var(--el-fill-color-light);
    border-radius: 4px;
    font-size: 12px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
  }
}

.detail-hint {
  margin-top: 12px;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 4px;
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
  color: var(--el-color-danger);
}

.foot-tip {
  float: left;
  line-height: 32px;
  font-size: 12px;
}
</style>
