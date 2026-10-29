import { createCrud } from '@/api/manage/crudFactory'

// 策略管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/policy')

// 查询策略管理列表
export const listPolicy = crud.list

// 查询策略管理详细
export const getPolicy = crud.get

// 新增策略管理
export const addPolicy = crud.add

// 修改策略管理
export const updatePolicy = crud.update

// 删除策略管理
export const delPolicy = crud.del
