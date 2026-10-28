# L1 · 统一日志字段契约

> 目标：任何一个请求，能用 `request_id` 在各子系统日志里串起来。这是分布式排障的最低门槛。

## 1. 必填字段（每条日志都要有）

| 字段 | 类型 | 说明 |
|---|---|---|
| `ts` | ISO-8601 | 时间戳，带毫秒与时区 |
| `level` | string | `DEBUG/INFO/WARN/ERROR` |
| `msg` | string | 人类可读信息 |
| `service` | string | 服务名（来自环境变量 `APP_NAME`） |
| `env` | string | `local/dev/test/prod` |
| `request_id` | string | **贯穿全链路**；无请求上下文时填 `-` |
| `logger` | string | 模块名（Python `app.services.x` / Java 类名） |

## 2. 上下文字段（有就填）

| 字段 | 说明 |
|---|---|
| `user_id` | 操作者；系统行为填 `system` |
| `trace_id` | 跨服务追踪（若用 OpenTelemetry） |
| `duration_ms` | 耗时 |
| `error_code` | 业务错误码（见 `error-codes.md`） |
| `stack` | **仅 ERROR 且非生产**，或生产下写入独立字段并脱敏 |

## 3. 格式约定

- **生产环境输出 JSON 行日志**（一行一条，便于采集）；
- **本地开发可输出彩色可读格式**，但字段集合保持一致；
- **禁止**：
  - 用 `print` / `System.out` 打日志；
  - 拼接非结构化字符串作为主要日志内容（`用户123创建了订单` → 应结构化）；
  - 记录敏感信息（密码、token、身份证、手机号、银行卡）—— 必须脱敏；
  - 同一错误在多处重复记录（**记一次**）。

## 4. request_id 的传递规则

```
入口（网关/Controller 拦截器）生成 request_id
  → 写入 MDC / contextvar
  → 出站 HTTP 调用带上 X-Request-Id 头
  → 日志自动携带
  → 响应体返回 request_id（见 api-envelope）
```

**跨语言统一头名**：`X-Request-Id`（不带前缀 X- 的 `Request-Id` 亦可，但**一个项目内只能有一个**）。

## 5. 日志级别使用约定

| 级别 | 用于 |
|---|---|
| `DEBUG` | 排查用细节，生产关闭 |
| `INFO` | 关键业务动作（创建/状态变更/外部调用结果） |
| `WARN` | **可自愈**的异常：重试成功、降级生效、配置了未知项 |
| `ERROR` | 需要人介入：业务失败、依赖不可用、未知异常 |

> 常见错误：把"用户输入错误"打成 `ERROR`。参数校验失败是**预期行为**，打 `INFO` 或 `WARN`，否则 ERROR 日志会被淹没，告警失效。
