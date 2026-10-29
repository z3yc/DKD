import { createCrud } from '@/api/manage/crudFactory'
import request from '@/utils/request'

// 工单类型：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// cancelTaskType 是本域自定义方法，保留原实现（不改 URL/method）。
const crud = createCrud('/manage/taskType')

// 查询工单类型列表
export const listTaskType = crud.list

// 查询工单类型详细
export const getTaskType = crud.get

// 新增工单类型
export const addTaskType = crud.add

// 修改工单类型
export const updateTaskType = crud.update

// 删除工单类型
export const delTaskType = crud.del

// 取消工单
export function cancelTaskType(data) {
  return request({
    url: '/manage/task/cancel',
    method: 'put',
    data: data
  })
}
