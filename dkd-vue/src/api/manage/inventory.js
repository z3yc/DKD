import request from '@/utils/request'

// 查询库存列表
export function listInventory(query) {
    return request({
        url: '/manage/inventory/list',
        method: 'get',
        params: query
    })
}

// 获取库存详情
export function getInventory(id) {
    return request({
        url: '/manage/inventory/' + id,
        method: 'get'
    })
}

// 新增库存
export function addInventory(data) {
    return request({
        url: '/manage/inventory',
        method: 'post',
        data: data
    })
}

// 修改库存
export function updateInventory(data) {
    return request({
        url: '/manage/inventory',
        method: 'put',
        data: data
    })
}

// 删除库存
export function delInventory(id) {
    return request({
        url: '/manage/inventory/' + id,
        method: 'delete'
    })
}

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

// 获取库存预警
export function getInventoryAlerts(query) {
    return request({
        url: '/manage/inventory/alerts',
        method: 'get',
        params: query
    })
}

// 获取补货建议
export function getRestockSuggestions(query) {
    return request({
        url: '/manage/inventory/restock',
        method: 'get',
        params: query
    })
}
