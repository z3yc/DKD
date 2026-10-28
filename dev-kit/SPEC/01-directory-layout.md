# L0-01 · 统一目录结构与命名

## 1. 通用顶层布局（所有项目，与语言无关）

```
<project>/
├─ AGENTS.md          # 给 AI 编码助手的完整约定（唯一权威入口）
├─ CLAUDE.md          # 精简版：技术栈 + 模块 + skill 路由（≤60 行，指向 AGENTS.md）
├─ REVIEW.md          # 代码评审关注点：什么最重要 / 失败长什么样 / 跳过的路径
├─ README.md          # 给人看：是什么、怎么跑起来、怎么验证
├─ VERSION            # 单行版本号
├─ CHANGELOG.md       # 倒序，每次发版一条
├─ .env.example       # 全部可配置项 + 默认值 + 说明（**必须提交**）
├─ .env               # 本地真实值（**禁止提交**）
├─ .gitignore
├─ docs/              # 方案、决策、DDL、验收、事故复盘
│   ├─ ddl/           # 表结构变更归档（可回滚语句一并记录）
│   ├─ decisions/     # 重要技术决策及依据（ADR 风格）
│   └─ evidence/      # 验收记录、截图、实测数据
├─ scripts/           # 一次性脚本（迁移、数据修复、本地工具）
├─ tests/             # 或 src/test；跨模块的端到端测试
└─ deploy/            # Dockerfile、compose、nginx conf、启动脚本
```

**五条硬规则**：
1. **构建产物目录不进版本库，且 AI 不得读取**（`target/`、`dist/`、`build/`、`node_modules/`、`.venv/`）—— 读 `.class` 或打包产物作为判断依据一律无效，一切以源码为准。
2. **`docs/` 是决策的归处**。任何"为什么这么选"必须落文档，不允许只存在于聊天记录里。
3. **`.env.example` 必须提交**，且每一项都有注释说明默认值与是否必填。
4. **`scripts/` 只放一次性脚本**，常驻能力必须进正式代码目录（否则它会成为无人维护的暗债）。
5. **测试目录与源码目录层级对齐**，便于一眼看出哪个模块没有测试。

## 2. 语言专属布局

### Python（FastAPI 为例）
```
app/
├─ main.py            # 应用工厂 / 路由装配（不写业务逻辑）
├─ config.py           # 配置（pydantic-settings，全大写）
├─ logging_conf.py     # 日志初始化（在 main 最早处调用一次）
├─ errors.py           # 业务异常 + 全局异常处理器
├─ api/                # 路由层：只做参数校验 + 调 service + 包装响应
├─ services/           # 业务逻辑（可互相调用）
├─ domain/             # 实体 / DTO / VO
├─ integrations/       # 外部系统适配（DB、HTTP、MQ）
└─ prompts/            # LLM 提示词（**禁止散落在业务代码里**）
tests/                  # 与 app 目录结构对齐
```

### Java（Spring Boot 为例）
```
<module>/
├─ controller/         # 只做参数校验 + 调 Service + 包装响应
├─ service/  impl/
├─ mapper/  resources/mapper/*.xml
├─ domain/  dto/  vo/
├─ constant/  enums/
└─ config/
```

### Vue 3
```
src/
├─ api/                # 按业务域分文件，统一封装请求（**禁止组件内裸 axios**）
├─ views/               # 页面级组件
├─ components/          # 仅全局通用组件
├─ stores/              # Pinia
├─ utils/
└─ styles/
```

## 3. 文件命名

| 类型 | 规则 | 示例 |
|---|---|---|
| Python 模块 | `snake_case.py` | `restock_service.py` |
| Java 类 | `PascalCase.java`，一文件一顶级类 | `TaskServiceImpl.java` |
| Vue 组件 | `PascalCase.vue`（多词） | `VmPanel.vue` |
| 文档 | `kebab-case.md` | `error-codes.md` |
| 决策记录 | `YYYY-MM-DD-<主题>.md` | `2026-10-27-checkpointer-selection.md` |
| 脚本 | `kebab-case.sh` / `.ps1` | `init-project.sh` |
| 测试 | `test_<被测>.py` / `<被测>Test.java` | `test_restock_service.py` |

## 4. 命名规范（跨语言一致的部分）

| 对象 | 规则 | 正例 / 反例 |
|---|---|---|
| 环境变量 | `<APP>_<MODULE>_<KEY>` 全大写下划线 | `MYAPP_DB_DSN` / ~~`dbDsn`~~ |
| 常量 | 全大写下划线，集中放 `constant` 包 | `MAX_RETRY` / ~~`maxRetry`~~ |
| 布尔字段 | 避免 `is` 前缀（序列化陷阱），用 `enabled`/`flag`/`has` | `enabled` / ~~`isEnabled`~~ |
| 时间字段 | 统一 `xxx_time` / `xxx_at`，**不得**混用 | `create_time` / ~~`createdAt`~~ |
| 金额 | 类型 + 单位后缀，禁 float/double | `amount_cents` / `BigDecimal amount` |
| ID | `<实体>_id`，不带类型信息 | `task_id` / ~~`taskId`~~（Java 内 `taskId` 可接受，跨系统边界必须 `task_id`） |
| 接口 URL | 小写复数，模块前缀 | `/manage/tasks` / ~~`/manage/getTaskList`~~ |
| 布尔语义枚举 | code + name 双字段 + `getByCode` | `VmStatus.RUNNING` |

**长度与可读性**：名字宁可长也不要含糊。`calculate_restock_quantity` 好过 `calc`。缩写只在**团队内已成术语**时使用（如 `vm` = 售货机），且必须在 AGENTS.md 的术语表里登记。
