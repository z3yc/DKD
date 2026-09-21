<template>
  <div class="app-container report-page">
    <!-- 顶部操作栏 -->
    <div class="report-header">
      <div class="header-left">
        <span class="header-title">AI报表中心</span>
      </div>
      <div class="header-right">
        <el-button type="primary" :loading="generating" @click="handleGenerate('daily')" v-hasPermi="['manage:report:generate']">
          <el-icon><Document /></el-icon>生成日报
        </el-button>
        <el-button type="success" :loading="generating" @click="handleGenerate('weekly')" v-hasPermi="['manage:report:generate']">
          <el-icon><Calendar /></el-icon>生成周报
        </el-button>
      </div>
    </div>

    <!-- 最新报表区域 -->
    <div class="latest-report-section">
      <div class="section-header">
        <span class="section-title">最新报表</span>
        <el-radio-group v-model="activeReportType" size="small" @change="loadLatestReport">
          <el-radio-button label="daily">日报</el-radio-button>
          <el-radio-button label="weekly">周报</el-radio-button>
        </el-radio-group>
      </div>

      <!-- 数据概览卡片 -->
      <el-row :gutter="16" class="stat-cards" v-loading="latestLoading">
        <template v-if="latestReportData">
          <el-col :xs="12" :sm="8" :md="6" :lg="4">
            <div class="stat-card card-revenue">
              <div class="stat-icon"><el-icon :size="28"><Money /></el-icon></div>
              <div class="stat-info">
                <div class="stat-value">{{ formatRevenue(latestReportData.totalRevenue) }}</div>
                <div class="stat-label">总收入</div>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="6" :lg="4">
            <div class="stat-card card-orders clickable" @click="navigateTo('/order/order')">
              <div class="stat-icon"><el-icon :size="28"><ShoppingCart /></el-icon></div>
              <div class="stat-info">
                <div class="stat-value">{{ latestReportData.totalOrders }}</div>
                <div class="stat-label">订单总数</div>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="6" :lg="4">
            <div class="stat-card card-devices clickable" @click="navigateTo('/vm/vm')">
              <div class="stat-icon"><el-icon :size="28"><Monitor /></el-icon></div>
              <div class="stat-info">
                <div class="stat-value">{{ latestReportData.totalDevices }}</div>
                <div class="stat-label">设备总数</div>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="6" :lg="4">
            <div class="stat-card card-online clickable" @click="navigateTo('/vm/vmStatus')">
              <div class="stat-icon"><el-icon :size="28"><Connection /></el-icon></div>
              <div class="stat-info">
                <div class="stat-value">{{ latestReportData.onlineDevices }}</div>
                <div class="stat-label">在线设备</div>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="6" :lg="4">
            <div class="stat-card card-fault clickable" @click="navigateTo('/vm/vmStatus')">
              <div class="stat-icon"><el-icon :size="28"><WarningFilled /></el-icon></div>
              <div class="stat-info">
                <div class="stat-value">{{ latestReportData.faultDevices }}</div>
                <div class="stat-label">故障设备</div>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="6" :lg="4">
            <div class="stat-card card-lowstock clickable" @click="navigateTo('/sku/sku')">
              <div class="stat-icon"><el-icon :size="28"><Box /></el-icon></div>
              <div class="stat-info">
                <div class="stat-value">{{ latestReportData.lowStockDevices }}</div>
                <div class="stat-label">库存不足</div>
              </div>
            </div>
          </el-col>
        </template>
        <template v-else-if="!latestLoading">
          <el-col :span="24">
            <el-empty description="暂无报表数据，请先生成报表" :image-size="80" />
          </el-col>
        </template>
      </el-row>

      <!-- AI 分析 & 排行 -->
      <el-row :gutter="16" class="analysis-row" v-if="latestReport">
        <el-col :xs="24" :sm="24" :md="16">
          <div class="analysis-card">
            <div class="card-title">
              <el-icon><Cpu /></el-icon>
              <span>AI 运营分析</span>
              <span class="report-date">{{ latestReport.reportDate }}</span>
            </div>
            <!-- AI 分析失败提示 -->
            <el-alert
              v-if="isAiFailed(latestReport.aiAnalysis)"
              title="AI 分析生成失败"
              :description="latestReport.aiAnalysis"
              type="error"
              show-icon
              :closable="false"
              style="margin-bottom: 12px"
            />
            <div v-if="!isAiFailed(latestReport.aiAnalysis)" class="markdown-body" v-html="renderedAnalysis"></div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="24" :md="8">
          <div class="rank-card">
            <div class="card-title">
              <el-icon><TrendCharts /></el-icon>
              <span>商品销量排行</span>
            </div>
            <div class="rank-list" v-if="topProducts.length">
              <div class="rank-item" v-for="(item, index) in topProducts" :key="index">
                <span class="rank-index" :class="'rank-' + (index + 1)">{{ index + 1 }}</span>
                <span class="rank-name">{{ item.skuName }}</span>
                <span class="rank-value">{{ item.sales }}件</span>
              </div>
            </div>
            <el-empty v-else description="暂无数据" :image-size="60" />
          </div>
          <div class="rank-card" style="margin-top: 16px">
            <div class="card-title">
              <el-icon><OfficeBuilding /></el-icon>
              <span>点位收入排行</span>
            </div>
            <div class="rank-list" v-if="topNodes.length">
              <div class="rank-item" v-for="(item, index) in topNodes" :key="index">
                <span class="rank-index" :class="'rank-' + (index + 1)">{{ index + 1 }}</span>
                <span class="rank-name">{{ item.nodeName }}</span>
                <span class="rank-value">{{ formatRevenue(item.revenue) }}</span>
              </div>
            </div>
            <el-empty v-else description="暂无数据" :image-size="60" />
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 历史报表列表 -->
    <div class="history-section">
      <div class="section-header">
        <span class="section-title">历史报表</span>
      </div>

      <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch" label-width="68px">
        <el-form-item label="报表类型" prop="reportType">
          <el-select v-model="queryParams.reportType" placeholder="全部" clearable>
            <el-option label="日报" value="daily" />
            <el-option label="周报" value="weekly" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态" prop="status">
          <el-select v-model="queryParams.status" placeholder="全部" clearable>
            <el-option label="生成中" :value="0" />
            <el-option label="成功" :value="1" />
            <el-option label="失败" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
          <el-button icon="Refresh" @click="resetQuery">重置</el-button>
          <el-button type="danger" plain icon="Delete" :disabled="!selectedIds.length" @click="handleBatchDelete" v-hasPermi="['manage:report:remove']">
            批量删除{{ selectedIds.length ? '(' + selectedIds.length + ')' : '' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="history-table-wrapper">
        <el-table v-loading="historyLoading" :data="sortedReportList" :border="false" @selection-change="handleSelectionChange">
          <el-table-column type="selection" width="45" align="center" />
          <el-table-column label="报表日期" align="center" prop="reportDate" min-width="110" />
          <el-table-column label="类型" align="center" prop="reportType" min-width="70">
            <template #default="scope">
              <el-tag :type="scope.row.reportType === 'daily' ? '' : 'success'" size="small">
                {{ scope.row.reportType === 'daily' ? '日报' : '周报' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="总收入" align="center" prop="totalRevenue" min-width="100">
            <template #default="scope">
              {{ formatRevenue(scope.row.totalRevenue) }}
            </template>
          </el-table-column>
          <el-table-column label="订单" align="center" prop="totalOrders" min-width="60" class-name="hidden-sm-and-down" />
          <el-table-column label="设备" align="center" prop="totalDevices" min-width="60" class-name="hidden-sm-and-down" />
          <el-table-column label="在线" align="center" prop="onlineDevices" min-width="60">
            <template #default="scope">
              <span class="text-success">{{ scope.row.onlineDevices }}</span>
            </template>
          </el-table-column>
          <el-table-column label="故障" align="center" prop="faultDevices" min-width="60">
            <template #default="scope">
              <span :class="scope.row.faultDevices > 0 ? 'text-danger' : ''">{{ scope.row.faultDevices }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" align="center" prop="status" min-width="70">
            <template #default="scope">
              <el-tag :type="getStatusType(scope.row.status)" size="small">
                {{ getStatusLabel(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="生成时间" align="center" prop="createTime" min-width="150" class-name="hidden-sm-and-down">
            <template #default="scope">
              <span>{{ parseTime(scope.row.createTime, '{y}-{m}-{d} {h}:{i}') }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" align="center" fixed="right" min-width="100">
            <template #default="scope">
              <el-button link type="primary" @click="handleDetail(scope.row)" v-hasPermi="['manage:report:query']">
                详情
              </el-button>
              <el-button link type="danger" @click="handleDelete(scope.row)" v-hasPermi="['manage:report:remove']">
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <pagination
        v-show="total > 0"
        :total="total"
        v-model:page="queryParams.pageNum"
        v-model:limit="queryParams.pageSize"
        @pagination="getHistoryList"
      />
    </div>

    <!-- 详情对话框 -->
    <el-dialog :title="detailTitle" v-model="detailOpen" width="800px" append-to-body destroy-on-close :fullscreen="isMobile">
      <div v-loading="detailLoading">
        <template v-if="detailData">
          <!-- 基础统计 -->
          <el-descriptions :column="3" border size="default" class="detail-desc">
            <el-descriptions-item label="报表日期">{{ detailData.reportDate }}</el-descriptions-item>
            <el-descriptions-item label="报表类型">
              <el-tag :type="detailData.reportType === 'daily' ? '' : 'success'" size="small">
                {{ detailData.reportType === 'daily' ? '日报' : '周报' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="getStatusType(detailData.status)" size="small">
                {{ getStatusLabel(detailData.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="总收入">{{ formatRevenue(detailData.totalRevenue) }}</el-descriptions-item>
            <el-descriptions-item label="订单总数">{{ detailData.totalOrders }}</el-descriptions-item>
            <el-descriptions-item label="设备总数">{{ detailData.totalDevices }}</el-descriptions-item>
            <el-descriptions-item label="在线设备">
              <span class="text-success">{{ detailData.onlineDevices }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="故障设备">
              <span :class="detailData.faultDevices > 0 ? 'text-danger' : ''">{{ detailData.faultDevices }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="库存不足">
              <span :class="detailData.lowStockDevices > 0 ? 'text-warning' : ''">{{ detailData.lowStockDevices }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="生成时间" :span="3">{{ detailData.createTime }}</el-descriptions-item>
          </el-descriptions>

          <!-- 商品排行 -->
          <div class="detail-section" v-if="detailTopProducts.length">
            <div class="detail-section-title">商品销量排行</div>
            <el-table :data="detailTopProducts" size="small" stripe>
              <el-table-column label="排名" type="index" width="60" align="center" />
              <el-table-column label="商品名称" prop="skuName" />
              <el-table-column label="销量" prop="sales" align="center" />
            </el-table>
          </div>

          <!-- 点位排行 -->
          <div class="detail-section" v-if="detailTopNodes.length">
            <div class="detail-section-title">点位收入排行</div>
            <el-table :data="detailTopNodes" size="small" stripe>
              <el-table-column label="排名" type="index" width="60" align="center" />
              <el-table-column label="点位名称" prop="nodeName" />
              <el-table-column label="收入" align="center">
                <template #default="scope">{{ formatRevenue(scope.row.revenue) }}</template>
              </el-table-column>
            </el-table>
          </div>

          <!-- AI 分析 -->
          <div class="detail-section" v-if="detailData.aiAnalysis">
            <div class="detail-section-title">AI 运营分析</div>
            <el-alert
              v-if="isAiFailed(detailData.aiAnalysis)"
              title="AI 分析生成失败"
              :description="detailData.aiAnalysis"
              type="error"
              show-icon
              :closable="false"
            />
            <div v-else class="markdown-body" v-html="renderMarkdown(detailData.aiAnalysis)"></div>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="detailOpen = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="Report">
import { getLatestDaily, getLatestWeekly, listReport, getReport, generateReport, delReport } from '@/api/manage/report'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { parseTime } from '@/utils/ruoyi'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'

// 配置 marked（v18 使用 use 替代 setOptions）
marked.use({ breaks: true, gfm: true })

const router = useRouter()

/** 点击卡片跳转 */
function navigateTo(path) {
  router.push(path)
}

const { proxy } = getCurrentInstance()
const activeReportType = ref('daily')
const isMobile = computed(() => window.innerWidth < 768)
const latestReport = ref(null)
const latestLoading = ref(false)
const generating = ref(false)

// 历史列表
const reportList = ref([])
const historyLoading = ref(false)
const selectedIds = ref([])

function handleSelectionChange(selection) {
  selectedIds.value = selection.map(item => item.id)
}

// 统计卡片使用列表最新一条数据
const latestReportData = computed(() => sortedReportList.value?.[0] || null)

// 历史列表按生成时间倒序
const sortedReportList = computed(() => {
  return [...reportList.value].sort((a, b) => new Date(b.createTime) - new Date(a.createTime))
})
const showSearch = ref(true)
const total = ref(0)

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    reportType: undefined,
    status: undefined
  }
})
const { queryParams } = toRefs(data)

// 详情
const detailOpen = ref(false)
const detailTitle = ref('')
const detailData = ref(null)
const detailLoading = ref(false)
const detailTopProducts = ref([])
const detailTopNodes = ref([])

// 最新报表的商品/点位排行
const topProducts = computed(() => {
  if (!latestReport.value?.topProducts) return []
  try {
    return JSON.parse(latestReport.value.topProducts)
  } catch {
    return []
  }
})

const topNodes = computed(() => {
  if (!latestReport.value?.topNodes) return []
  try {
    return JSON.parse(latestReport.value.topNodes)
  } catch {
    return []
  }
})

// Markdown 渲染
const renderedAnalysis = computed(() => {
  if (!latestReport.value?.aiAnalysis) return ''
  return renderMarkdown(latestReport.value.aiAnalysis)
})

function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(text)
  return DOMPurify.sanitize(raw)
}

// 判断 AI 分析是否失败（后端返回超时等错误信息）
function isAiFailed(text) {
  if (!text) return false
  return text.startsWith('系统处理失败') || text.includes('SocketTimeoutException')
}

// 金额格式化（分转元）
function formatRevenue(val) {
  if (val === null || val === undefined) return '¥0.00'
  const yuan = (val / 100).toFixed(2)
  return yuan < 0 ? '-¥' + Math.abs(yuan) : '¥' + yuan
}

// 状态格式化
function getStatusType(status) {
  const map = { 0: 'warning', 1: 'success', 2: 'danger' }
  return map[status] || 'info'
}

function getStatusLabel(status) {
  const map = { 0: '生成中', 1: '成功', 2: '失败' }
  return map[status] || '未知'
}

// 加载最新报表
function loadLatestReport() {
  latestLoading.value = true
  const api = activeReportType.value === 'daily' ? getLatestDaily : getLatestWeekly
  api().then(res => {
    latestReport.value = res.data
  }).catch(() => {
    latestReport.value = null
  }).finally(() => {
    latestLoading.value = false
  })
}

// 生成报表
function handleGenerate(type) {
  const typeName = type === 'daily' ? '日报' : '周报'
  ElMessageBox.confirm(`确认生成${typeName}？AI分析需要5-60秒，请耐心等待。`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'info'
  }).then(() => {
    generating.value = true
    generateReport(type).then(() => {
      ElMessage.success(`${typeName}生成成功`)
      // 自动切换到生成的报表类型并刷新
      activeReportType.value = type
      loadLatestReport()
      queryParams.value.pageNum = 1
      getHistoryList()
    }).catch(() => {
      ElMessage.error(`${typeName}生成失败，请重试`)
    }).finally(() => {
      generating.value = false
    })
  }).catch(() => {})
}

// 历史报表列表
function getHistoryList() {
  historyLoading.value = true
  listReport(queryParams.value).then(res => {
    reportList.value = res.rows
    total.value = res.total
  }).finally(() => {
    historyLoading.value = false
  })
}

function handleQuery() {
  queryParams.value.pageNum = 1
  getHistoryList()
}

function resetQuery() {
  proxy.resetForm('queryRef')
  handleQuery()
}

// 删除报表
function handleDelete(row) {
  const ids = row.id
  ElMessageBox.confirm('确认删除该报表？', '提示', { type: 'warning' }).then(() => {
    return delReport(ids)
  }).then(() => {
    ElMessage.success('删除成功')
    getHistoryList()
  }).catch(() => {})
}

function handleBatchDelete() {
  ElMessageBox.confirm(`确认删除选中的 ${selectedIds.value.length} 条报表？`, '提示', { type: 'warning' }).then(() => {
    return delReport(selectedIds.value.join(','))
  }).then(() => {
    ElMessage.success('删除成功')
    selectedIds.value = []
    getHistoryList()
  }).catch(() => {})
}

// 查看详情
function handleDetail(row) {
  detailLoading.value = true
  detailOpen.value = true
  detailTitle.value = `报表详情 - ${row.reportDate} ${row.reportType === 'daily' ? '日报' : '周报'}`
  detailData.value = null
  detailTopProducts.value = []
  detailTopNodes.value = []

  getReport(row.id).then(res => {
    detailData.value = res.data
    try {
      detailTopProducts.value = res.data.topProducts ? JSON.parse(res.data.topProducts) : []
    } catch { detailTopProducts.value = [] }
    try {
      detailTopNodes.value = res.data.topNodes ? JSON.parse(res.data.topNodes) : []
    } catch { detailTopNodes.value = [] }
  }).finally(() => {
    detailLoading.value = false
  })
}

// 初始化
loadLatestReport()
getHistoryList()
</script>

<style lang="scss" scoped>
.report-page {
  padding: 20px;
}

.report-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;

  .header-title {
    font-size: 20px;
    font-weight: 600;
    color: #303133;
  }

  .header-right {
    display: flex;
    gap: 10px;
  }
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;

  .section-title {
    font-size: 16px;
    font-weight: 600;
    color: #303133;
    position: relative;
    padding-left: 12px;

    &::before {
      content: '';
      position: absolute;
      left: 0;
      top: 50%;
      transform: translateY(-50%);
      width: 4px;
      height: 16px;
      background: #409eff;
      border-radius: 2px;
    }
  }
}

// 统计卡片
.stat-cards {
  margin-bottom: 16px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 20px 16px;
  border-radius: 12px;
  transition: transform 0.2s, box-shadow 0.2s;

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }

  .stat-icon {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-right: 12px;
    flex-shrink: 0;
  }

  .stat-info {
    flex: 1;
    min-width: 0;

    .stat-value {
      font-size: 24px;
      font-weight: 700;
      line-height: 1.2;
      font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
    }

    .stat-label {
      font-size: 12px;
      color: #909399;
      margin-top: 4px;
    }
  }
}

.card-revenue {
  background: linear-gradient(135deg, #fef0f0, #fde2e2);
  .stat-icon { background: rgba(245, 108, 108, 0.15); color: #f56c6c; }
  .stat-value { color: #f56c6c; }
}

.card-orders {
  background: linear-gradient(135deg, #f0f9eb, #e1f3d8);
  .stat-icon { background: rgba(103, 194, 58, 0.15); color: #67c23a; }
  .stat-value { color: #67c23a; }
}

.card-devices {
  background: linear-gradient(135deg, #ecf5ff, #d9ecff);
  .stat-icon { background: rgba(64, 158, 255, 0.15); color: #409eff; }
  .stat-value { color: #409eff; }
}

.card-online {
  background: linear-gradient(135deg, #f0f9eb, #e1f3d8);
  .stat-icon { background: rgba(103, 194, 58, 0.15); color: #95d475; }
  .stat-value { color: #67c23a; }
}

.card-fault {
  background: linear-gradient(135deg, #fef6f0, #fde8d8);
  .stat-icon { background: rgba(230, 162, 60, 0.15); color: #e6a23c; }
  .stat-value { color: #e6a23c; }
}

.card-lowstock {
  background: linear-gradient(135deg, #fdf6ec, #faecd8);
  .stat-icon { background: rgba(230, 162, 60, 0.15); color: #e6a23c; }
  .stat-value { color: #e6a23c; }
}

.stat-card.clickable {
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  }
}

// AI 分析区域
.analysis-row {
  margin-bottom: 20px;
}

.analysis-card,
.rank-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid #ebeef5;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;

  .el-icon {
    color: #409eff;
  }

  .report-date {
    margin-left: auto;
    font-size: 12px;
    font-weight: 400;
    color: #909399;
  }
}

// Markdown 样式
.markdown-body {
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
  max-height: 480px;
  overflow-y: auto;
  padding-right: 8px;

  :deep(h1) { font-size: 20px; margin: 16px 0 8px; font-weight: 700; }
  :deep(h2) { font-size: 18px; margin: 14px 0 8px; font-weight: 600; color: #303133; border-bottom: 1px solid #eee; padding-bottom: 6px; }
  :deep(h3) { font-size: 16px; margin: 12px 0 6px; font-weight: 600; }
  :deep(h4) { font-size: 15px; margin: 10px 0 4px; font-weight: 600; }
  :deep(p) { margin: 8px 0; }
  :deep(ul), :deep(ol) { padding-left: 20px; margin: 8px 0; }
  :deep(li) { margin: 4px 0; }
  :deep(strong) { color: #409eff; }
  :deep(blockquote) { border-left: 4px solid #409eff; padding: 8px 16px; margin: 8px 0; background: #f4f7ff; border-radius: 0 4px 4px 0; }
  :deep(code) { background: #f5f7fa; padding: 2px 6px; border-radius: 4px; font-size: 13px; color: #e6a23c; }
  :deep(table) { width: 100%; border-collapse: collapse; margin: 8px 0; }
  :deep(th), :deep(td) { border: 1px solid #ebeef5; padding: 8px 12px; text-align: left; font-size: 13px; }
  :deep(th) { background: #f5f7fa; font-weight: 600; }
}

// 排行列表
.rank-list {
  .rank-item {
    display: flex;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid #f5f5f5;

    &:last-child { border-bottom: none; }
  }

  .rank-index {
    width: 24px;
    height: 24px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
    margin-right: 10px;
    flex-shrink: 0;
    background: #f0f2f5;
    color: #909399;

    &.rank-1 { background: linear-gradient(135deg, #ffd700, #ffb800); color: #fff; }
    &.rank-2 { background: linear-gradient(135deg, #c0c0c0, #a8a8a8); color: #fff; }
    &.rank-3 { background: linear-gradient(135deg, #cd7f32, #b8690e); color: #fff; }
  }

  .rank-name {
    flex: 1;
    font-size: 14px;
    color: #606266;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rank-value {
    font-size: 14px;
    font-weight: 600;
    color: #409eff;
    margin-left: 10px;
    flex-shrink: 0;
  }
}

// 历史报表区域
.history-section {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid #ebeef5;
}

.history-table-wrapper {
  width: 100%;
  overflow-x: auto;

  :deep(.el-table) {
    min-width: 560px;
  }

  // 小屏隐藏次要列
  @media (max-width: 768px) {
    :deep(.hidden-sm-and-down) {
      display: none;
    }
  }
}

// 详情弹窗
.detail-desc {
  margin-bottom: 20px;
}

.detail-section {
  margin-top: 20px;

  .detail-section-title {
    font-size: 15px;
    font-weight: 600;
    color: #303133;
    margin-bottom: 12px;
    padding-left: 10px;
    border-left: 3px solid #409eff;
  }
}

.text-success { color: #67c23a; font-weight: 600; }
.text-danger { color: #f56c6c; font-weight: 600; }
.text-warning { color: #e6a23c; font-weight: 600; }
</style>
