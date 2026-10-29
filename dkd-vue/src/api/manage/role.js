import { createCrud } from '@/api/manage/crudFactory'

// 工单角色：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/role')

// 查询工单角色列表
export const listRole = crud.list

// 查询工单角色详细
export const getRole = crud.get

// 新增工单角色
export const addRole = crud.add

// 修改工单角色
export const updateRole = crud.update

// 删除工单角色
export const delRole = crud.del
