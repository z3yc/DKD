import { createCrud } from '@/api/manage/crudFactory'

// 设备管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// 注意：AI 智能诊断接口的**唯一实现**在 api/manage/ai.js（P2-20 去重），本文件不再导出该接口；
// 设备页请从 '@/api/manage/ai' 导入。
const crud = createCrud('/manage/vm')

// 查询设备管理列表
export const listVm = crud.list

// 查询设备管理详细
export const getVm = crud.get

// 新增设备管理
export const addVm = crud.add

// 修改设备管理
export const updateVm = crud.update

// 删除设备管理
export const delVm = crud.del
