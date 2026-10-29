import { createCrud } from '@/api/manage/crudFactory'

// 商品管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/sku')

// 查询商品管理列表
export const listSku = crud.list

// 查询商品管理详细
export const getSku = crud.get

// 新增商品管理
export const addSku = crud.add

// 修改商品管理
export const updateSku = crud.update

// 删除商品管理
export const delSku = crud.del
