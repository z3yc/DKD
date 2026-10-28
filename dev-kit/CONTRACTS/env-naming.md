# L1 · 环境变量与配置契约

> 目标：配置**只有一处来源**（环境变量），命名可预测，改一个配置不需要翻代码。

## 1. 命名规则

```
<APP>_<MODULE>_<KEY>
```
- 全大写 + 下划线；
- `<APP>` 为服务名大写（如 `DKD_AGENT_`），**所有本服务变量共享该前缀** —— 避免多服务混部时污染；
- `<MODULE>` 为功能模块（`DB` / `REDIS` / `LLM` / `HTTP` / `LOG`）；
- `<KEY>` 语义明确，带单位（如 `TIMEOUT_MS` 而非 `TIMEOUT`）。

```
✅ DKD_AGENT_DB_READ_DSN
✅ DKD_AGENT_HTTP_TIMEOUT_MS
❌ dbDsn          （大小写混乱）
❌ DKD_DSN        （缺模块，无法区分多个数据源）
❌ DKD_AGENT_TIMEOUT（缺单位）
```

## 2. 保留前缀（框架/语言约定，不要占用）

`PYTHON_`、`JAVA_`、`NODE_`、`PATH`、`HOME`、`TZ`、`LANG`、`CI`

## 3. 通用配置项（所有服务都应有的最小集合）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `<APP>_ENV` | `local` | `local/dev/test/prod` |
| `<APP>_LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARN/ERROR` |
| `<APP>_LOG_FORMAT` | `json` | `json`（生产）/ `console`（本地） |
| `<APP>_HTTP_TIMEOUT_MS` | `10000` | 出站 HTTP 超时 |
| `<APP>_HTTP_MAX_RETRIES` | `2` | 仅对幂等请求 |
| `<APP>_TZ` | `Asia/Shanghai` | 时区，避免容器默认 UTC 造成的时间错位 |

## 4. 配置加载的硬规则

| # | 规则 |
|---|---|
| E1 | **配置只允许从环境变量读取**，禁止散落的 `.properties`/`config.py` 常量；语言特有的是**加载器**，不是第二来源 |
| E2 | **启动时校验**：必填项缺失直接 fail-fast，不允许"跑起来才发现没配" |
| E3 | **`.env.example` 必须提交**，每项带默认值、是否必填、影响范围注释 |
| E4 | **`.env` 禁止提交**，且 pre-commit 开 `detect-private-key` |
| E5 | **敏感值只在运行时注入**（密钥、DSN 含密码），不进镜像、不进日志、不进错误信息 |
| E6 | **布尔配置统一 `true/false` 字符串**，禁止混用 `1/0`、`yes/no` |
| E7 | 开关类配置命名用 `ENABLED` 后缀（`FEATURE_X_ENABLED`），**不用 `DISABLED`**（双重否定难读） |

## 5. 各语言加载方式

| 语言 | 方式 |
|---|---|
| Python | `pydantic-settings` 的 `BaseSettings`，`env_prefix="<APP>_"`，**类型即校验**（`timeout_ms: int`） |
| Java | Spring Boot `@ConfigurationProperties` + `relaxed binding`；或 `@Value` + 启动校验 |
| Vue | 仅 `VITE_` 前缀会进打包产物 —— **任何敏感配置都不得放前端环境变量** |

> ⚠️ 前端环境变量是**公开的**（会被打进 JS 产物）。前端只放非敏感配置（API base、功能开关的默认值）。
