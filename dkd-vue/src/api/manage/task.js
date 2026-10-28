import request from '@/utils/request'

// 查询运维工单列表
export function listTask(query) {
  return request({
    url: '/manage/task/list',
    method: 'get',
    params: query
  })
}

// 查询运维工单详细
export function getTask(taskId) {
  return request({
    url: '/manage/task/' + taskId,
    method: 'get'
  })
}

// 新增运维工单
export function addTask(data) {
  return request({
    url: '/manage/task',
    method: 'post',
    data: data
  })
}

// 修改运维工单
export function updateTask(data) {
  return request({
    url: '/manage/task',
    method: 'put',
    data: data
  })
}

// 删除运维工单
export function delTask(taskId) {
  return request({
    url: '/manage/task/' + taskId,
    method: 'delete'
  })
}

//根据售货机获取维修人员列表
export function getOperationList(innerCode) {
  return request({
    url: '/manage/emp/operationList/' + innerCode,
    method: 'get'
  })
}
//根据售货机获取运营人员列表
export function getBusinessList(innerCode) {
  return request({
    url: '/manage/emp/businessList/' + innerCode,
    method: 'get'
  })
}
// 查看工单补货详情
export function getTaskDetails(taskId) {
  return request({
    url: '/manage/taskDetails/byTaskId/' + taskId,
    method: 'get'
  })
}
// 获取补货预警值（后端 JobController 的映射路径是 /manage/taskJob，不是 /manage/job）
export function getJob(id) {
  return request({
    url: '/manage/taskJob/' + id,
    method: 'get'
  })
}

// 设置补货阈值（后端 JobController 的映射路径是 /manage/taskJob，不是 /manage/job）
export function setJob(data) {
  return request({
    url: '/manage/taskJob',
    method: 'put',
    data:data
  })
}