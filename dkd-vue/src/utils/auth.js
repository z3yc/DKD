import Cookies from 'js-cookie'

const TokenKey = 'Admin-Token'

// 「记住账号」相关 Cookie（登录页 src/views/login.vue 写入）
const UsernameKey = 'username'
const RememberMeKey = 'rememberMe'
// 历史版本曾把 encrypt(password) 写入 Cookie；前端私钥可解密，等同明文，只做残留清理
const LegacyPasswordKey = 'password'

export function getToken() {
  return Cookies.get(TokenKey)
}

export function setToken(token) {
  return Cookies.set(TokenKey, token)
}

export function removeToken() {
  return Cookies.remove(TokenKey)
}

/**
 * 清除「记住登录」留下的全部 Cookie。
 *
 * 为什么放在 utils/auth：登出入口不止一处（Navbar 主动退出、sessionExpired 会话过期、
 * permission.js 拉取用户信息失败），只改 login.vue 会漏掉这些路径，残留用户名/标记
 * （以及旧版本的可逆密码 Cookie）。与 removeToken 一样由调用方在退出时统一调用。
 */
export function removeRememberedLogin() {
  Cookies.remove(UsernameKey)
  Cookies.remove(RememberMeKey)
  Cookies.remove(LegacyPasswordKey)
}
