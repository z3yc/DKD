# DevKit · 个人开发体系（V1.0）

> 目标：**一套轻量、模块化、可裁剪的跨项目开发基座**，覆盖规范 → 契约 → 核心模块 → 项目模板四层。
> 基准日：2026-10-27 · 维护人：zyc
> 覆盖语言：Python 3.11+ / Java 8+（Spring Boot）/ Vue 3 + Vite

---

## 一、为什么要有四层

把"规范""代码""骨架"混在一个仓库里，最后的结果一定是：想用其中一小块时，必须把整包搬走，然后删掉 80%，删着删着就不敢动了 —— 体系随之腐烂。

所以本体系按**依赖方向单向向下**分为四层：

```
L3  模板层 TEMPLATES/    可运行的项目骨架（组合 L1+L2 的产物）
        ↓ 依赖
L2  核心层 CORE/         可迁移复用的实现（按语言分目录，模块间单向依赖）
        ↓ 依赖
L1  契约层 CONTRACTS/    跨语言一致的"接口形状"（错误码/响应包装/日志字段/env 命名）
        ↓ 依赖
L0  规范层 SPEC/         纯文档，零依赖，任何项目都能直接贴
```

**铁律：禁止反向依赖。** L2 的模块不得反向引用 L3 的任何东西；L1 不得引用 L2。任何一层都可以被**单独取走**而不破坏其他层。

## 二、四层职责与依赖关系

| 层 | 目录 | 职责 | 依赖 | 能否单独取用 |
|---|---|---|---|---|
| **L0 规范** | `SPEC/` | 目录结构、命名、风格与注释、Git/版本、Agent 文档 | 无 | ✅ 纯 md，复制即可 |
| **L1 契约** | `CONTRACTS/` | 跨语言对齐的错误码、响应包装、日志字段、env 命名 | L0 | ✅ 纯 md + 示例片段 |
| **L2 核心** | `CORE/` | 可直接 import 的实现模块（配置/日志/错误/ID/HTTP） | L1 | ✅ 按模块摘取，见各模块 `DEPS` 声明 |
| **L3 模板** | `TEMPLATES/` | 已组合好的可运行骨架 | L1+L2 | ⚠️ 整体复制后裁剪 |

**L2 内部依赖图（Python）**：

```
config  ←  最底层：环境变量 / 配置加载（依赖 pydantic-settings）
   ↑
logging ←  结构化日志（依赖 config：读 LOG_LEVEL / 服务名）
   ↑
errors   ←  统一异常与错误码（依赖 config：读环境决定是否暴露堆栈）
   ↑
http     ←  HTTP 客户端封装（依赖 logging + errors：超时/重试/统一异常转换）
```
`ids`（request_id / trace_id）为**零依赖**叶子模块，任何层可直接引用。

## 三、60 秒接入新项目

```powershell
# PowerShell（Windows）
.\SCRIPTS\init-project.ps1 -TargetDir "F:\my-new-project" -ProjectName "my-new-project" -Stack python-fastapi
```
```bash
# bash / Git Bash
./SCRIPTS/init-project.sh /path/to/my-new-project my-new-project python-fastapi
```

脚本会：复制模板 → 替换 `__APP_NAME__` 等占位符 → 生成本地 `.env` → 输出后续 5 步 checklist。

**手工接入（不用脚本也行）**：
1. 复制 `TEMPLATES/<stack>/` 到新目录
2. 复制 `SPEC/AGENTS.md` `CLAUDE.md` `REVIEW.md` 模板到项目根，填项目专属信息
3. 若用 Python：`pip install -e ../dev-kit/CORE/python` 或直接把 `CORE/python/devkit` 拷进项目
4. 复制 `.env.example` → `.env` 并填值
5. 跑模板自带的冒烟测试（`pytest` / `mvn test` / `npm run build`）

## 四、裁剪指南（按需取用，不强制全量）

| 你的项目类型 | 建议取用 |
|---|---|
| 一次性脚本 / 数据分析 | 只取 L0 中 `02-naming` + `03-style` + CORE/python 的 `config`/`logging` |
| Python 服务（FastAPI） | L0 全量 + L1 全量 + CORE/python 全量 + `TEMPLATES/python-fastapi` |
| Java Spring Boot 服务 | L0 全量 + L1 全量 + `TEMPLATES/java-springboot`（CORE/java 为规范 + 关键片段） |
| 纯前端 | L0（目录 + 命名 + 风格）+ L1（错误码/响应包装）+ `TEMPLATES/vue3-vite` |
| 多语言全栈 | 全量，各子系统各自取对应子目录 |

裁剪操作就是**删目录**，不需要改任何配置 —— 这是分层设计的唯一目的。

## 五、目录总览

```
dev-kit/
├─ README.md                    ← 你在这
├─ VERSION
├─ SPEC/                        L0 规范（6 份）
│   ├─ 00-overview.md
│   ├─ 01-directory-layout.md
│   ├─ 02-naming.md
│   ├─ 03-style-and-comments.md
│   ├─ 04-git-version-changelog.md
│   ├─ 05-agent-docs.md
│   └─ templates/               AGENTS.md / CLAUDE.md / REVIEW.md 三件套模板
├─ CONTRACTS/                   L1 契约（4 份）
│   ├─ error-codes.md
│   ├─ api-envelope.md
│   ├─ log-fields.md
│   └─ env-naming.md
├─ CORE/                        L2 核心模块
│   ├─ python/devkit/           可 import 的 Python 包（已验证可运行）
│   └─ java/                    规范 + 关键片段（待补全，见 §六）
├─ TEMPLATES/                   L3 模板
│   ├─ python-fastapi/          完整可运行
│   ├─ java-springboot/         骨架 + 关键配置
│   └─ vue3-vite/               骨架 + 关键配置
└─ SCRIPTS/
    ├─ init-project.ps1
    └─ init-project.sh
```

## 六、V1.0 的边界与待你确认项

**已完成**：L0 全量、L1 全量、L2 的 Python 部分（可运行）、L3 的 Python 模板（可运行）、初始化脚本。

**待确认后再补全**（V1.0 给了默认方案，默认方案本身可用，只是可能不合你的偏好）：

| # | 待确认项 | 当前默认方案 | 影响 |
|---|---|---|---|
| 1 | Java 侧基线版本 | 默认 **Java 8**（沿用 DKD 约束），禁用 var/record/switch 表达式 | 若你新项目用 Java 17/21，`CORE/java` 与模板需整体改写 |
| 2 | Java 是否用 Lombok | 默认**不用**（手写 getter/setter，与 RuoYi 一致） | 影响模板实体与 DTO 写法 |
| 3 | 前端状态管理与 UI 库 | 默认 **Pinia + Element Plus**（与 dkd-vue 一致） | 影响 `TEMPLATES/vue3-vite` 依赖 |
| 4 | 前端是否引入 TypeScript | 默认**不引入**（与 DKD 一致） | 影响模板全部文件后缀与 ESLint 配置 |
| 5 | 包管理与容器化基线 | 默认 Python 用 **uv**、Node 用 **npm**、Java 用 **Maven**；Docker 多阶段构建 | 若你偏好 pnpm/poetry/Gradle 需替换 |
| 6 | 日志是否上集中式采集 | 默认**本地 JSON 行日志 + request_id 贯穿**，不接 ELK/Loki | 影响 `logging` 模块与部署脚本 |
| 7 | 是否需要 monorepo 形态 | 默认**每个项目独立仓**，dev-kit 用复制而非 submodule | 若走 monorepo，init 脚本需改为 workspace 生成 |
| 8 | 体系存放位置 | 当前在 `F:\DKD\dev-kit\`（寄居在 DKD 仓库内） | **建议独立成仓**，见下 |

> ⚠️ **关于第 8 项**：dev-kit 现在放在 DKD 项目目录里，会被 DKD 的 `.gitignore` / CI / 代码统计误伤，也不利于你从别的项目复制。建议 `git init` 独立成仓（或直接迁到 `F:\dev-kit`），需要我执行的话说一声，我会连迁移步骤一起给。

## 七、维护约定

- 改动任何一层，同步更新本 README 的依赖图与 `VERSION`；
- 新增 L2 模块必须在文件头写明 `DEPS:`（它依赖哪些模块）—— 没有依赖声明的模块不允许进 CORE；
- 每季度做一次"逆向同步"：把各项目里被反复修正的写法回灌进 SPEC/CORE。
