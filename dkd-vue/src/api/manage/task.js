import { createCrud } from '@/api/manage/crudFactory'
import request from '@/utils/request'

// 运维工单：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// 其余 6 个业务方法是本域自定义方法，保留原实现（不改 URL/method）。
const crud = createCrud('/manage/task')

// 查询运维工单列表
export const listTask = crud.list

// 查询运维工单详细
export const getTask = crud.get

// 新增运维工单
export const addTask = crud.add

// 修改运维工单
export const updateTask = crud.update

// 删除运维工单
export const delTask = crud.del

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
