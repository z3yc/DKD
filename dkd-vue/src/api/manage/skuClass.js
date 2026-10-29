import { createCrud } from '@/api/manage/crudFactory'

// 商品类型：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）
const crud = createCrud('/manage/skuClass')

// 查询商品类型列表
export const listSkuClass = crud.list

// 查询商品类型详细
export const getSkuClass = crud.get

// 新增商品类型
export const addSkuClass = crud.add

// 修改商品类型
export const updateSkuClass = crud.update

// 删除商品类型
export const delSkuClass = crud.del
