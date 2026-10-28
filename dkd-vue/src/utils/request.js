import axios from 'axios'
import { ElNotification , ElMessage, ElLoading } from 'element-plus'
import { getToken } from '@/utils/auth'
import errorCode from '@/utils/errorCode'
import { tansParams, blobValidate } from '@/utils/ruoyi'
import cache from '@/plugins/cache'
import { saveAs } from 'file-saver'
// 会话过期处置：**唯一实现**在 utils/sessionExpired.js（绕开 axios 的 restock.js/agent.js/sse.js 共用同一份，
// 否则两份实现必然漂移；2026-10-03 实测报障就是绕开 axios 的链路没有这段处置）
import { isRelogin, handleSessionExpired, SESSION_EXPIRED_MSG } from '@/utils/sessionExpired'

let downloadLoadingInstance;
// 保持既有导入点不变（permission.js 读写的就是这个标记）
export { isRelogin };

axios.defaults.headers['Content-Type'] = 'application/json;charset=utf-8'
// 创建axios实例
const service = axios.create({
  // axios中请求配置有baseURL选项，表示请求URL公共部分
  baseURL: import.meta.env.VITE_APP_BASE_API,
  // 超时
  timeout: 10000
})

// request拦截器
service.interceptors.request.use(config => {
  // 是否需要设置 token
  const isToken = (config.headers || {}).isToken === false
  // 是否需要防止数据重复提交
  const isRepeatSubmit = (config.headers || {}).repeatSubmit === false
  if (getToken() && !isToken) {
    config.headers['Authorization'] = 'Bearer ' + getToken() // 让每个请求携带自定义token 请根据实际情况自行修改
  }
  // get请求映射params参数
  if (config.method === 'get' && config.params) {
    let url = config.url + '?' + tansParams(config.params);
    url = url.slice(0, -1);
    config.params = {};
    config.url = url;
  }
  if (!isRepeatSubmit && (config.method === 'post' || config.method === 'put')) {
    const requestObj = {
      url: config.url,
      data: typeof config.data === 'object' ? JSON.stringify(config.data) : config.data,
      time: new Date().getTime()
    }
    const requestSize = Object.keys(JSON.stringify(requestObj)).length; // 请求数据大小
    const limitSize = 5 * 1024 * 1024; // 限制存放数据5M
    if (requestSize >= limitSize) {
      console.warn(`[${config.url}]: ` + '请求数据大小超出允许的5M限制，无法进行防重复提交验证。')
      return config;
    }
    const sessionObj = cache.session.getJSON('sessionObj')
    if (sessionObj === undefined || sessionObj === null || sessionObj === '') {
      cache.session.setJSON('sessionObj', requestObj)
    } else {
      const s_url = sessionObj.url;                // 请求地址
      const s_data = sessionObj.data;              // 请求数据
      const s_time = sessionObj.time;              // 请求时间
      const interval = 1000;                       // 间隔时间(ms)，小于此时间视为重复提交
      if (s_data === requestObj.data && requestObj.time - s_time < interval && s_url === requestObj.url) {
        const message = '数据正在处理，请勿重复提交';
        console.warn(`[${s_url}]: ` + message)
        return Promise.reject(new Error(message))
      } else {
        cache.session.setJSON('sessionObj', requestObj)
      }
    }
  }
  return config
}, error => {
    console.log(error)
    Promise.reject(error)
})

// 响应拦截器
service.interceptors.response.use(res => {
    // 未设置状态码则默认成功状态
    const code = res.data.code || 200;
    // 获取错误信息
    const msg = errorCode[code] || res.data.msg || errorCode['default']
    // P1-16 silent：需要自管文案与交互的调用方（如 api/manage/restock.js）传
    // `silent: true` 关闭拦截器的全局提示，拦截器只做信封解析与 reject
    // （错误对象带 code/msg），展示交给调用方——否则会「拦截器弹一条、调用方再弹一条」
    const silent = res.config && res.config.silent === true
    // 二进制数据则直接返回
    if (res.request.responseType ===  'blob' || res.request.responseType ===  'arraybuffer') {
      return res.data
    }
    if (code === 401) {
      // 行为与改动前逐字一致，只是实现搬到了 utils/sessionExpired.js
      handleSessionExpired()
      if (silent) {
        return Promise.reject(Object.assign(new Error(SESSION_EXPIRED_MSG), { code: 401, unauthorized: true }))
      }
      return Promise.reject('无效的会话，或者会话已过期，请重新登录。')
    } else if (code === 500) {
      if (!silent) {
        ElMessage({ message: msg, type: 'error' })
      }
      return Promise.reject(Object.assign(new Error(msg), { code: 500 }))
    } else if (code === 601) {
      if (!silent) {
        ElMessage({ message: msg, type: 'warning' })
      }
      return Promise.reject(Object.assign(new Error(msg), { code: 601 }))
    } else if (code !== 200) {
      if (!silent) {
        ElNotification.error({ title: msg })
      }
      return Promise.reject(Object.assign(new Error(msg), { code: code }))
    } else {
      return  Promise.resolve(res.data)
    }
  },
  error => {
    console.log('err' + error)
    // 主动取消不弹错误提示（AGENTS §2.2 可取消）：restock 工作台页面卸载会中止在途请求
    if (axios.isCancel(error)) {
      return Promise.reject(Object.assign(new Error('请求已取消'), { code: -1, aborted: true }))
    }
    const silent = error.config && error.config.silent === true
    // P1-16：区分「传输层失败」与「业务层失败」。后端（dkd-agent 网关）把业务拒绝
    // 表达为**真实 HTTP 状态码**（409 状态机冲突 / 422 字段校验 / 400 规则拒绝 /
    // 502 回调失败 / 503 未初始化），可行动文案在响应体 msg 里——原先统一改写成
    // 「系统接口409异常」，运营看到「系统异常」而不是「已建单的计划不能重复确认」
    // 这类能自救的提示（这正是 restock.js 此前绕开 axios 的根因）。
    let message
    let code = 0
    let bodyCode = null
    if (error.response) {
      code = error.response.status
      const body = error.response.data
      if (body && typeof body === 'object') {
        bodyCode = typeof body.code !== 'undefined' ? body.code : null
        if (body.msg) {
          // 业务层失败：原样透传后端文案
          message = body.msg
        }
      }
    }
    if (!message) {
      // 传输层失败（网络/超时/无信封）：沿用既有兜底文案
      message = error.message
      if (message == "Network Error") {
        message = "后端接口连接异常";
      } else if (message && message.includes("timeout")) {
        message = "系统接口请求超时";
      } else if (message && message.includes("Request failed with status code")) {
        message = "系统接口" + message.substr(message.length - 3) + "异常";
      }
    }
    if (!silent) {
      ElMessage({ message: message, type: 'error', duration: 5 * 1000 })
    }
    // code 优先取信封 body.code（网关可能以非 2xx 状态 + 信封 code 表达业务失败），
    // 无信封时退回 HTTP 状态码；network 标记供调用方区分「连不上」与「业务拒绝」
    return Promise.reject(Object.assign(new Error(message), {
      code: bodyCode != null ? bodyCode : code,
      network: !error.response
    }))
  }
)

// 通用下载方法
export function download(url, params, filename, config) {
  downloadLoadingInstance = ElLoading.service({ text: "正在下载数据，请稍候", background: "rgba(0, 0, 0, 0.7)", })
  return service.post(url, params, {
    transformRequest: [(params) => { return tansParams(params) }],
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    responseType: 'blob',
    ...config
  }).then(async (data) => {
    const isBlob = blobValidate(data);
    if (isBlob) {
      const blob = new Blob([data])
      saveAs(blob, filename)
    } else {
      const resText = await data.text();
      const rspObj = JSON.parse(resText);
      const errMsg = errorCode[rspObj.code] || rspObj.msg || errorCode['default']
      ElMessage.error(errMsg);
    }
    downloadLoadingInstance.close();
  }).catch((r) => {
    console.error(r)
    ElMessage.error('下载文件出现错误，请联系管理员！')
    downloadLoadingInstance.close();
  })
}

export default service
