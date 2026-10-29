import request from '@/utils/request'

/**
 * 生成某一业务域的标准 CRUD 五件套（list / get / add / update / del）。
 *
 * 为什么要有它：`api/manage/*.js` 里十多个文件是同一份 RuoYi 生成器样板——URL 拼接、
 * method、params/data 放哪儿逐字重复。改一个约定要改十几处，漏一处就是“同一个后台
 * 十个页面行为不一致”。收敛到本文件后，api 文件只声明 baseUrl 与本域自定义方法。
 *
 * 边界（P2-21 迁移纪律，见 docs/P2治理与一致性详细计划-2026-10-29.md §2.3）：
 * 1) 本函数只做**原样拼接**，不对 URL 做任何归一化/小写化/去尾斜杠——baseUrl 必须与后端
 *    `@RequestMapping` 逐字一致（如 `/manage/skuClass` 的大小写），否则会改变既有请求契约；
 * 2) 只有「五个函数的 URL/method/参数名与这里完全一致」的 api 文件才允许迁移；有自定义方法
 *    （resetPwd、importTemplate、cancel 等）的文件保留自定义方法原实现，五件套走工厂；
 * 3) `del` 的 id 允许传数组：与迁移前一样依赖 JS 的 String() 语义拼成 `1,2,3`，RuoYi 后端
 *    `@PathVariable Long[] ids` 认这个格式，**不要**在这里改成循环调用，否则批量删除语义变了。
 */
export function createCrud(baseUrl) {
  return {
    // 查询列表（RuoYi 分页参数 pageNum/pageSize 由调用方放在 query 对象里）
    list(query) {
      return request({
        url: baseUrl + '/list',
        method: 'get',
        params: query
      })
    },
    // 查询详细（RuoYi 惯例：GET restful/{id}）
    get(id) {
      return request({
        url: baseUrl + '/' + id,
        method: 'get'
      })
    },
    // 新增（POST 表单对象）
    add(data) {
      return request({
        url: baseUrl,
        method: 'post',
        data: data
      })
    },
    // 修改（PUT 表单对象）
    update(data) {
      return request({
        url: baseUrl,
        method: 'put',
        data: data
      })
    },
    // 删除（DELETE restful/{id}；单删传 id，批量删除传以逗号拼接的 id 串）
    del(id) {
      return request({
        url: baseUrl + '/' + id,
        method: 'delete'
      })
    }
  }
}
