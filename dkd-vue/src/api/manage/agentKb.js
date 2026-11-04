import request from '@/utils/request'

/**
 * 智能体知识库 L2 API（排期任务 3-6，页面 S11）。
 *
 * 数据归属（AGENTS §7.3）：知识库文档 / 切片 / 检索记录属**智能体自有数据**（agent_kb_*），
 * 由 Python 侧 ingest 写入（切片 + 本地向量化），Java 在这里只做**代理**（鉴权 + 分页 + 审计）。
 * 为什么页面不直连 Python：Python 服务没有 RuoYi 权限体系，直连等于绕过登录态与权限点，
 * 且会把 service 间密钥暴露到浏览器。
 *
 * 为什么知识库不用 RAG 查业务数字：设备/库存/销量/工单这类**结构化事实必须走只读工具直查**
 * （精确、可回归、有口径）；L2 只负责“厂商手册/故障码表/SOP/口径说明”这类文档语料。
 *
 * 写接口（入库/启停）带 `silent: true`：失败原因（重复内容、切片失败、服务不可达）
 * 需要持久展示在抽屉/页面上，而不是被拦截器 toast 一闪带过。
 */

/**
 * 查询知识库文档列表（分页）。
 *
 * ⚠️ 响应形状：Java 只是**代理** Python（`AgentKbController.documents` 返回 AjaxResult），
 * 所以分页数据在 `data.total / data.rows` 里（不是 RuoYi 裸 TableDataInfo）；页面做两种形态归一化，
 * 避免后续 Java 换成 TableDataInfo 时列表忽然恒空。
 *
 * @param {Object} query category / enabled / keyword / pageNum / pageSize
 * @returns AjaxResult：data = { total, rows }；rows 字段 camelCase
 *          （docCode/title/category/sourceType/sourceRef/chunkCount/status/enabled/contentHash/updateTime）
 */
export function listAgentKbDocuments(query) {
  return request({
    url: '/manage/agent/kb/documents',
    method: 'get',
    params: query
  })
}

/**
 * 知识库概览统计（页面顶部三张卡：文档数 / 切片数 / 检索次数）。
 *
 * 检索次数来自只增的 `agent_kb_retrieval`，是“知识库到底被用了几次”的唯一客观口径，
 * 不用前端把某次搜索次数凑上去（那会把测试点击也算成真实使用）。
 *
 * @returns AjaxResult：data = { documents, chunks, retrievals }
 */
export function getAgentKbStats() {
  return request({
    url: '/manage/agent/kb/stats',
    method: 'get'
  })
}

/**
 * 新增文档并入库（切片 + 向量化在 Python 侧完成）。
 *
 * `content` 是**提取后的纯文本**（首版支持粘贴/文本），≤1MB 由页面先拦、后端再校验；
 * 内容相同（content_hash 一致）会走“跳过”路径，返回里的 skipped 要展示给用户——
 * 静默跳过会让人以为文档没存进去。
 *
 * ⚠️ 响应形状：Java 代理的是 Python 信封 data，入库结果当前包在 `data.ingest` 里
 * （如 `{ ingest: { docCode, chunks, skipped } }`）；页面同时兼容“扁平”形态。
 *
 * @param {Object} data { docCode, title, category, sourceType, sourceRef, content, operator }
 * @returns AjaxResult：data = { ingest: { docCode, chunks, skipped } }（或扁平 { docCode, chunks, skipped }）
 */
export function ingestAgentKbDocument(data) {
  return request({
    url: '/manage/agent/kb/documents',
    method: 'post',
    data,
    silent: true
  })
}

/**
 * 启用 / 停用文档（停用后不再参与检索，但已产生的检索记录与引用不回滚）。
 *
 * @param {string} docCode 文档编码（进 URL 前编码，避免编码里的特殊字符截断路由）
 * @param {Object} data { enabled: 0|1 }
 */
export function setAgentKbDocumentEnabled(docCode, data) {
  return request({
    url: '/manage/agent/kb/documents/' + encodeURIComponent(docCode) + '/enabled',
    method: 'put',
    data,
    silent: true
  })
}

/**
 * 检索测试（只读召回，用于调阈值；每次调用都会写一条检索记录）。
 *
 * 结果语义（对应 3-6 的“引用强制校验”）：
 *  - hits 非空：每条带 score / excerpt / docCode / title，score 需 ≥ minScore 才可能被引用；
 *  - hits 为空：fallbackReason 说明为什么没命中（no_hit / low_score / citation_missing），
 *    此时智能体必须回退为「仅列证据」模板，**不得编造知识库结论**。
 * 这也是测试面板要把 fallbackReason 原样显示出来的原因：它是“引用治理”的可观测入口。
 *
 * @param {Object} data { query, topK, scene? }
 * @returns AjaxResult：data = { hits: [{chunkId,docCode,title,score,excerpt}], topK, minScore,
 *          retrievalId, fallbackReason }
 */
export function searchAgentKb(data) {
  return request({
    url: '/manage/agent/kb/search',
    method: 'post',
    data,
    silent: true
  })
}
