import { createCrud } from '@/api/manage/crudFactory'

// 人员列表：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/emp')

// 查询人员列表列表
export const listEmp = crud.list

// 查询人员列表详细
export const getEmp = crud.get

// 新增人员列表
export const addEmp = crud.add

// 修改人员列表
export const updateEmp = crud.update

// 删除人员列表
export const delEmp = crud.del
