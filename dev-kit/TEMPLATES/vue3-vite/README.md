# 模板 · Vue 3 + Vite（骨架）

> ⚠️ **V1.0 状态：请求层与规范已给全，页面/路由骨架需按项目生成。**
> 默认基线（待确认）：**JavaScript（非 TypeScript）** + Pinia + Element Plus + npm。

## 待确认项

| # | 项 | 当前默认 | 你的选择 |
|---|---|---|---|
| 1 | TypeScript | **不引入** | □ 不引入　□ 引入 |
| 2 | UI 库 | Element Plus | □ Element Plus　□ Naive UI　□ 无 |
| 3 | 状态管理 | Pinia | □ Pinia　□ 无 |
| 4 | 包管理 | npm | □ npm　□ pnpm　□ yarn |

## 目录约定

```
src/
├─ api/          按业务域分文件，**禁组件内裸 axios**
├─ views/        页面级组件
├─ components/   仅全局通用组件
├─ stores/       Pinia
├─ utils/
└─ styles/
```

## 已提供的关键文件

| 文件 | 作用 |
|---|---|
| `src/api/request.js` | 统一请求封装：错误码分类处理、幂等重试（仅 06/07）、request_id 透传、AbortController |

## 三条铁律（SPEC/03 §6）

1. **API 调用统一走 `src/api/<域>.js`** —— 组件内裸 axios 会让错误处理出现 N 种实现；
2. **loading 必须在 `finally` 复位** —— 异常路径忘记复位是最常见的前端 bug；
3. **长请求必须可取消**（AbortController）—— 组件卸载后仍在 setState 会引发内存泄漏与竞态。

## 安全红线

- `v-html` 只渲染经 **dompurify** sanitize 后的内容；
- **前端环境变量是公开的**（会被打进 JS 产物），任何密钥都不得放在 `VITE_` 变量里。

## 接入步骤

1. `npm create vite@latest <name> -- --template vue`；
2. 覆盖目录结构为本模板约定，复制 `src/api/request.js`；
3. 新增业务域时在 `src/api/` 下建对应文件，函数名 `getXxx/listXxx/addXxx/updateXxx/delXxx`；
4. 复制 `SPEC/templates/REVIEW.md.template` 为 `REVIEW.md`，填写前端专属的"失败长什么样"
   （例：文档失败=它在撒谎；状态失败=界面与服务端不一致且无提示）。
