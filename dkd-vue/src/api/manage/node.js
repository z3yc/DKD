import { createCrud } from '@/api/manage/crudFactory'
import request from '@/utils/request'

// 点位管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// getImportTemplate 是本域自定义方法，保留原实现（不改 URL/method）。
const crud = createCrud('/manage/node')

// 查询点位管理列表
export const listNode = crud.list

// 查询点位管理详细
export const getNode = crud.get

// 新增点位管理
export const addNode = crud.add

// 修改点位管理
export const updateNode = crud.update

// 删除点位管理
export const delNode = crud.del

// 导出点位导入模板
// 注意：后端 NodeController.importTemplate 是 @PostMapping，前端必须 POST，否则 405
export function getImportTemplate() {
  return request({
    url: '/manage/node/importTemplate',
    method: 'post',
    responseType: 'blob'
  })
}
