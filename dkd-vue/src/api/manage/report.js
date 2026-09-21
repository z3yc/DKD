import request from '@/utils/request'

// 查询最新日报
export function getLatestDaily() {
  return request({
    url: '/manage/report/daily',
    method: 'get'
  })
}

// 查询最新周报
export function getLatestWeekly() {
  return request({
    url: '/manage/report/weekly',
    method: 'get'
  })
}

// 查询报表列表（分页）
export function listReport(query) {
  return request({
    url: '/manage/report/list',
    method: 'get',
    params: query
  })
}

// 查询报表详情
export function getReport(id) {
  return request({
    url: '/manage/report/' + id,
    method: 'get'
  })
}

// 删除报表
export function delReport(ids) {
  return request({
    url: '/manage/report/' + ids,
    method: 'delete'
  })
}

// 手动生成报表（AI 生成耗时较长，超时设为 60 秒）
export function generateReport(reportType) {
  return request({
    url: '/manage/report/generate',
    method: 'post',
    params: { reportType },
    timeout: 60000
  })
}
