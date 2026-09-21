import request from '@/utils/request'

// 查询设备管理列表
export function listVm(query) {
  return request({
    url: '/manage/vm/list',
    method: 'get',
    params: query
  })
}

// 查询设备管理详细
export function getVm(id) {
  return request({
    url: '/manage/vm/' + id,
    method: 'get'
  })
}

// 新增设备管理
export function addVm(data) {
  return request({
    url: '/manage/vm',
    method: 'post',
    data: data
  })
}

// 修改设备管理
export function updateVm(data) {
  return request({
    url: '/manage/vm',
    method: 'put',
    data: data
  })
}

// 删除设备管理
export function delVm(id) {
  return request({
    url: '/manage/vm/' + id,
    method: 'delete'
  })
}
//人工智能
export function getAiDiagnosis(innerCode) {
  return request({
    url: '/manage/ai/diagnose', // 对应后端 Controller 的路径
    method: 'get',
    params: { innerCode }
  })
}