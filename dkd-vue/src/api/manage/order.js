import { createCrud } from '@/api/manage/crudFactory'
import request from '@/utils/request'

// 订单管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// cancelOrder / refundOrder 是本域自定义方法，保留原实现（不改 URL/method）。
const crud = createCrud('/manage/order')

// 查询订单管理列表
export const listOrder = crud.list

// 查询订单管理详细
export const getOrder = crud.get

// 新增订单管理
export const addOrder = crud.add

// 修改订单管理
export const updateOrder = crud.update

// 删除订单管理
export const delOrder = crud.del

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
