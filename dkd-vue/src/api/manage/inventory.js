import { createCrud } from '@/api/manage/crudFactory'
import request from '@/utils/request'

// 库存：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// 库存预警/低库存/缺货/初始化等是自定义方法，保留原实现（不改 URL/method），
// 其中 /alerts /restock 自 P1-11 起返回 TableDataInfo 分页结构（rows/total）。
const crud = createCrud('/manage/inventory')

// 查询库存列表
export const listInventory = crud.list

// 获取库存详情
export const getInventory = crud.get

// 新增库存
export const addInventory = crud.add

// 修改库存
export const updateInventory = crud.update

// 删除库存
export const delInventory = crud.del

// 查询低库存列表
export function listLowInventory(query) {
    return request({
        url: '/manage/inventory/low',
        method: 'get',
        params: query
    })
}

// 查询缺货列表
export function listOutOfStockInventory(query) {
    return request({
        url: '/manage/inventory/outofstock',
        method: 'get',
        params: query
    })
}

// 初始化库存
export function initInventory(data) {
    return request({
        url: '/manage/inventory/init',
        method: 'post',
        data: data
    })
}

// 获取库存预警（P1-11：返回 TableDataInfo 分页结构 rows/total）
export function getInventoryAlerts(query) {
    return request({
        url: '/manage/inventory/alerts',
        method: 'get',
        params: query
    })
}

// 获取补货建议（P1-11：返回 TableDataInfo 分页结构 rows/total）
export function getRestockSuggestions(query) {
    return request({
        url: '/manage/inventory/restock',
        method: 'get',
        params: query
    })
}
