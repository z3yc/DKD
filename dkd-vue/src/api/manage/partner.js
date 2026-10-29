import { createCrud } from '@/api/manage/crudFactory'
import request from '@/utils/request'

// 合作商管理：CRUD 五件套与 RuoYi 生成器样板逐字同构（URL/method/params 见 crudFactory），
// 命名导出保持不变，调用方 import 一行都不用改（P2-21）。
// resetPartnerPwd 是本域自定义方法，保留原实现（不改 URL/method）。
const crud = createCrud('/manage/partner')

// 查询合作商管理列表
export const listPartner = crud.list

// 查询合作商管理详细
export const getPartner = crud.get

// 新增合作商管理
export const addPartner = crud.add

// 修改合作商管理
export const updatePartner = crud.update

// 删除合作商管理
export const delPartner = crud.del

//重置密码
export function resetPartnerPwd(id) {
  return request({
    url: '/manage/partner/resetPwd/' + id,
    method: 'put'
  })
}
