import { createCrud } from '@/api/manage/crudFactory'

// 设备类型管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/vmType')

// 查询设备类型管理列表
export const listVmType = crud.list

// 查询设备类型管理详细
export const getVmType = crud.get

// 新增设备类型管理
export const addVmType = crud.add

// 修改设备类型管理
export const updateVmType = crud.update

// 删除设备类型管理
export const delVmType = crud.del
