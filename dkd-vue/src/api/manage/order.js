import request from '@/utils/request'

// 查询订单管理列表
export function listOrder(query) {
  return request({
    url: '/manage/order/list',
    method: 'get',
    params: query
  })
}

// 查询订单管理详细
export function getOrder(id) {
  return request({
    url: '/manage/order/' + id,
    method: 'get'
  })
}

// 新增订单管理
export function addOrder(data) {
  return request({
    url: '/manage/order',
    method: 'post',
    data: data
  })
}

// 修改订单管理
export function updateOrder(data) {
  return request({
    url: '/manage/order',
    method: 'put',
    data: data
  })
}

// 删除订单管理
export function delOrder(id) {
  return request({
    url: '/manage/order/' + id,
    method: 'delete'
  })
}

// 取消订单（仅 status=0 待支付可取消；cancelDesc 为取消原因纯文本，对应后端 @RequestBody String）
export function cancelOrder(id, cancelDesc) {
  return request({
    url: '/manage/order/cancel/' + id,
    method: 'put',
    data: cancelDesc
  })
}

// 申请退款（仅 status=2 出货成功可申请，后端置 payStatus=2 退款中）
export function refundOrder(id) {
  return request({
    url: '/manage/order/refund/' + id,
    method: 'put'
  })
}
