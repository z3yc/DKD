import { createCrud } from '@/api/manage/crudFactory'

// 区域管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/region')

// 查询区域管理列表
export const listRegion = crud.list

// 查询区域管理详细
export const getRegion = crud.get

// 新增区域管理
export const addRegion = crud.add

// 修改区域管理
export const updateRegion = crud.update

// 删除区域管理
export const delRegion = crud.del
